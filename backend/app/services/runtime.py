"""应用运行时编排：交易池、K 线初始化、实时订阅与定时扫描。"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Kline, Symbol
from app.schemas import ConfigValues, KlineData, TickerData
from app.services.binance.client import BinanceClient
from app.services.cache.kline_cache import KlineCache
from app.services.config_service import ConfigService
from app.services.performance.tracker import PerformanceTracker
from app.services.scanner.scanner import Scanner
from app.services.websocket.manager import BinanceWebSocketManager

logger = logging.getLogger(__name__)


class MonitorRuntime:
    """监控系统的进程级协调器。

    负责动态交易池、受控并发的 REST 初始化、WebSocket 生命周期、边界扫描和表现回填。
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        broadcaster,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.client = BinanceClient(settings)
        self.cache = KlineCache(settings.kline_history_limit)
        self.config_service = ConfigService()
        self.broadcaster = broadcaster
        self.scanner = Scanner(self.cache, self.client, session_factory, broadcaster.publish)
        self.performance = PerformanceTracker(self.cache, session_factory)
        self.websocket = BinanceWebSocketManager(settings, self.on_kline)
        self.active_symbols: set[str] = set()
        self.tickers: dict[str, TickerData] = {}
        self.config = ConfigValues()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self.initialization_status = "idle"

    async def start(self) -> None:
        """启动后台维护任务；首次采集失败不会拖垮 API 服务。"""
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._bootstrap_loop(), name="monitor-bootstrap"),
            asyncio.create_task(self._scan_loop(), name="monitor-scanner"),
            asyncio.create_task(self._performance_loop(), name="performance-tracker"),
        ]

    async def stop(self) -> None:
        """有序停止所有任务与网络连接。"""
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.websocket.stop()
        await self.client.close()

    async def _bootstrap_loop(self) -> None:
        """立即初始化，之后每 15 分钟刷新交易池和 24h 行情。"""
        while not self._stop.is_set():
            try:
                await self.refresh_pool()
                await asyncio.wait_for(self._stop.wait(), timeout=900)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self.initialization_status = "retrying"
                logger.exception("Failed to refresh Binance symbol pool")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass

    async def refresh_pool(self) -> None:
        """批量刷新合约与 ticker，并仅初始化新进入活跃池的交易对。"""
        self.initialization_status = "loading"
        async with self.session_factory() as session:
            self.config = await self.config_service.get(session)
        exchange_symbols, tickers = await asyncio.gather(
            self.client.exchange_symbols(), self.client.tickers()
        )
        available = {item["symbol"]: item for item in exchange_symbols}
        new_active = {
            symbol for symbol, ticker in tickers.items()
            if symbol in available and ticker.quote_volume >= self.config.min_24h_quote_volume
        }
        previous = self.active_symbols
        self.active_symbols = new_active
        self.tickers = tickers
        await self._persist_symbols(available, tickers)
        new_symbols = new_active - previous
        if new_symbols:
            await self._initialize_klines(new_symbols)
        if new_active != previous:
            await self.websocket.start(new_active, self.config.timeframes)
        self.initialization_status = "ready"

    async def apply_config(self, config: ConfigValues) -> None:
        """配置修改后立即重新筛选池，避免等待下一次定时刷新。"""
        self.config = config
        await self.refresh_pool()

    async def _persist_symbols(self, available: dict, tickers: dict[str, TickerData]) -> None:
        """持久化交易对市场快照；已有记录原地更新。"""
        async with self.session_factory() as session:
            existing = {
                row.symbol: row for row in (await session.execute(select(Symbol))).scalars()
            }
            for symbol, item in available.items():
                ticker = tickers.get(symbol)
                row = existing.get(symbol)
                if not row:
                    row = Symbol(
                        symbol=symbol, base_asset=item["baseAsset"], quote_asset=item["quoteAsset"],
                        contract_type=item["contractType"], status=item["status"],
                    )
                    session.add(row)
                row.is_active = symbol in self.active_symbols
                if ticker:
                    row.last_price = ticker.last_price
                    row.price_change_percent_24h = ticker.price_change_percent
                    row.quote_volume_24h = ticker.quote_volume
            await session.commit()

    async def _initialize_klines(self, symbols: set[str]) -> None:
        """通过固定数量 worker 初始化历史数据，防止瞬间创建大量 REST 请求。"""
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        for symbol in symbols:
            for timeframe in self.config.timeframes:
                queue.put_nowait((symbol, timeframe))

        async def worker() -> None:
            while not queue.empty():
                symbol, timeframe = await queue.get()
                try:
                    klines = await self.client.klines(symbol, timeframe, self.settings.kline_history_limit)
                    await self.cache.initialize(symbol, timeframe, klines)
                    await self._persist_closed_klines(klines)
                except Exception:
                    logger.exception("Kline initialization failed for %s %s", symbol, timeframe)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(self.settings.rest_concurrency)]
        await queue.join()
        await asyncio.gather(*workers)

    async def on_kline(self, kline: KlineData) -> None:
        """接收实时 K 线，并在发现收盘 K 线缺口时使用 REST 补齐。"""
        if kline.is_closed:
            _, closed = await self.cache.snapshot(kline.symbol, kline.timeframe)
            if closed and closed[-1].close_time < kline.open_time:
                # 重连期间可能跨过多个周期，REST 返回最近窗口后按开盘时间去重写入缓存。
                repaired = await self.client.klines(kline.symbol, kline.timeframe, 20)
                for item in repaired:
                    if item.is_closed and item.open_time > closed[-1].open_time:
                        await self.cache.update(item)
                await self._persist_closed_klines(repaired)
        closed_now = await self.cache.update(kline)
        if closed_now:
            await self._persist_closed_klines([kline])

    async def _persist_closed_klines(self, klines: list[KlineData]) -> None:
        """按唯一键保存完整 K 线，重复的 WebSocket 收盘事件不会制造脏数据。"""
        complete = [item for item in klines if item.is_closed]
        if not complete:
            return
        values = [item.model_dump() for item in complete]
        statement = pg_insert(Kline).values(values)
        # 历史初始化和重连补偿可能重复覆盖同一根 K 线，使用数据库原子 upsert 避免逐条查重。
        statement = statement.on_conflict_do_update(
            constraint="uq_kline_identity",
            set_={
                field: getattr(statement.excluded, field)
                for field in ("close_time", "open", "high", "low", "close", "volume", "quote_volume", "is_closed")
            },
        )
        async with self.session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def _scan_loop(self) -> None:
        """根据 UTC 服务器时间对齐下一个 N 分钟边界执行扫描。"""
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            interval = self.config.scan_interval_minutes
            next_minute = (now.minute // interval + 1) * interval
            boundary = now.replace(second=0, microsecond=0)
            if next_minute >= 60:
                boundary = boundary.replace(minute=0) + timedelta(hours=1)
            else:
                boundary = boundary.replace(minute=next_minute)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(0, (boundary - now).total_seconds()))
                continue
            except asyncio.TimeoutError:
                pass
            if self.initialization_status != "ready":
                # 历史窗口未全部进入缓存时扫描会基于不完整交易池产生偏差，因此等待下一边界。
                logger.info("Skipping scheduled scan while collector status is %s", self.initialization_status)
                continue
            try:
                await self.scanner.scan(self.active_symbols, self.tickers, self.config)
            except Exception:
                logger.exception("Scheduled scan failed")

    async def _performance_loop(self) -> None:
        """每分钟回填已到期的未来收益观察点。"""
        while not self._stop.is_set():
            try:
                await self.performance.update()
            except Exception:
                logger.exception("Performance update failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
