"""应用运行时编排：交易池、K 线初始化、实时订阅与定时扫描。"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Kline, Symbol
from app.schemas import ConfigValues, FundingRateData, KlineData, TickerData
from app.services.binance.client import BinanceClient
from app.services.cache.kline_cache import KlineCache
from app.services.chart import build_chart_candles
from app.services.config_service import ConfigService
from app.services.performance.tracker import PerformanceTracker
from app.services.scanner.scanner import Scanner
from app.services.websocket.manager import BinanceWebSocketManager

logger = logging.getLogger(__name__)

# Binance 在周期边界推送新 K 线通常会有极短延迟，给 WebSocket 事件留出落缓存时间。
SCAN_BOUNDARY_GRACE_SECONDS = 2
KLINE_INCREMENTAL_LIMIT = 20
TIMEFRAME_SECONDS = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}


def build_kline_retention_statement(
    markets: set[tuple[str, str]],
    history_limit: int,
):
    """构造 K 线保留清理语句，仅保留指定市场各自最新的历史窗口。"""
    ranked = (
        select(
            Kline.id.label("id"),
            func.row_number().over(
                partition_by=(Kline.symbol, Kline.timeframe),
                order_by=(Kline.open_time.desc(), Kline.id.desc()),
            ).label("retention_rank"),
        )
        .where(tuple_(Kline.symbol, Kline.timeframe).in_(markets))
        .cte("ranked_klines")
    )
    expired_ids = select(ranked.c.id).where(ranked.c.retention_rank > history_limit)
    return delete(Kline).where(Kline.id.in_(expired_ids)).execution_options(
        synchronize_session=False
    )


def update_active_pool_membership(row: Symbol, is_active: bool, observed_at: datetime) -> None:
    """更新当前活跃周期起点；持续活跃保持原值，重新入池才开始新周期。"""
    if is_active and (not row.is_active or row.active_since is None):
        row.active_since = observed_at
    elif not is_active:
        row.active_since = None
    row.is_active = is_active


def has_fresh_closed_history(
    latest_close_time: datetime,
    timeframe: str,
    now: datetime | None = None,
) -> bool:
    """判断数据库完整 K 线是否已连续覆盖到当前周期起点。"""
    timeframe_seconds = TIMEFRAME_SECONDS.get(timeframe)
    if timeframe_seconds is None:
        # 未知周期无法可靠计算 Binance 边界，保守回退到 REST 增量校验。
        return False
    current_time = now or datetime.now(timezone.utc)
    current_open_timestamp = (
        int(current_time.timestamp()) // timeframe_seconds * timeframe_seconds
    )
    current_open_time = datetime.fromtimestamp(current_open_timestamp, timezone.utc)
    return latest_close_time >= current_open_time


class MonitorRuntime:
    """监控系统的进程级协调器。

    负责动态交易池、受控并发的 REST 初始化、WebSocket 生命周期、边界扫描和表现回填。
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        broadcaster,
        kline_broadcaster,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.client = BinanceClient(settings)
        self.cache = KlineCache(settings.kline_history_limit)
        self.config_service = ConfigService()
        self.broadcaster = broadcaster
        self.kline_broadcaster = kline_broadcaster
        self.scanner = Scanner(self.cache, self.client, session_factory, broadcaster.publish)
        self.performance = PerformanceTracker(self.client, session_factory)
        self.websocket = BinanceWebSocketManager(settings, self.on_kline)
        self.active_symbols: set[str] = set()
        self.active_since_by_symbol: dict[str, datetime] = {}
        self.tickers: dict[str, TickerData] = {}
        self.funding_rates: dict[str, FundingRateData] = {}
        self.config = ConfigValues()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._chart_stream_lock = asyncio.Lock()
        self._chart_stream_subscribers: dict[tuple[str, str], int] = {}
        self._temporary_websockets: dict[tuple[str, str], BinanceWebSocketManager] = {}
        self.initialization_status = "idle"

    async def start(self) -> None:
        """启动后台维护任务；首次采集失败不会拖垮 API 服务。"""
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._bootstrap_loop(), name="monitor-bootstrap"),
            asyncio.create_task(self._scan_loop(), name="monitor-scanner"),
            asyncio.create_task(self._performance_loop(), name="performance-tracker"),
            asyncio.create_task(self._realtime_chart_poll_loop(), name="realtime-chart-fallback"),
        ]

    async def stop(self) -> None:
        """有序停止所有任务与网络连接。"""
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        async with self._chart_stream_lock:
            temporary = list(self._temporary_websockets.values())
            self._temporary_websockets.clear()
            self._chart_stream_subscribers.clear()
        for manager in temporary:
            await manager.stop()
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
        previous_timeframes = set(self.config.timeframes)
        async with self.session_factory() as session:
            self.config = await self.config_service.get(session)
        exchange_symbols, tickers, funding_rates = await asyncio.gather(
            self.client.exchange_symbols(), self.client.tickers(), self.client.funding_rates()
        )
        available = {item["symbol"]: item for item in exchange_symbols}
        new_active = {
            symbol for symbol, ticker in tickers.items()
            if symbol in available and ticker.quote_volume >= self.config.min_24h_quote_volume
        }
        previous = self.active_symbols
        self.active_symbols = new_active
        self.tickers = tickers
        self.funding_rates = funding_rates
        self.active_since_by_symbol = await self._persist_symbols(
            available, tickers, funding_rates
        )
        active_markets = {
            (symbol, timeframe)
            for symbol in new_active
            for timeframe in self.config.timeframes
        }
        # 图表临时订阅不进入 active_symbols，但交易池刷新时必须保留其精确缓存键。
        async with self._chart_stream_lock:
            chart_markets = set(self._chart_stream_subscribers)
            await self.cache.retain_markets(active_markets | chart_markets)
            await self._sync_temporary_websockets_locked()
        # 常驻市场全量初始化可能耗时较长，不能阻塞用户打开临时图表连接。
        missing_markets = active_markets - await self.cache.market_keys()
        if missing_markets:
            await self._initialize_markets(missing_markets)
        if new_active != previous or set(self.config.timeframes) != previous_timeframes:
            await self.websocket.start(new_active, self.config.timeframes)
        self.initialization_status = "ready"

    def _active_market_keys(self) -> set[tuple[str, str]]:
        """展开活跃交易池的全部启用周期，供临时图表缓存与常驻缓存合并。"""
        return {
            (symbol, timeframe)
            for symbol in self.active_symbols
            for timeframe in self.config.timeframes
        }

    async def _initialize_temporary_market(self, symbol: str, timeframe: str) -> None:
        """用 REST 初始化临时图表缓存，不持久化且不改变活跃池成员。"""
        klines = await self.client.klines(
            symbol,
            timeframe,
            self.settings.kline_history_limit,
        )
        if not klines:
            raise RuntimeError(f"No Kline data for {symbol} {timeframe}")
        await self.cache.initialize(symbol, timeframe, klines)

    async def _sync_temporary_websockets_locked(self) -> None:
        """使临时 Binance 订阅与当前查看者、活跃池状态保持一致。"""
        desired = {
            key
            for key in self._chart_stream_subscribers
            if key[0] not in self.active_symbols
        }
        for key in set(self._temporary_websockets) - desired:
            manager = self._temporary_websockets.pop(key)
            await manager.stop()
        for symbol, timeframe in desired - set(self._temporary_websockets):
            if (symbol, timeframe) not in await self.cache.market_keys():
                await self._initialize_temporary_market(symbol, timeframe)
            manager = BinanceWebSocketManager(self.settings, self.on_kline)
            await manager.start({symbol}, [timeframe])
            self._temporary_websockets[(symbol, timeframe)] = manager

    async def open_chart_stream(self, symbol: str, timeframe: str) -> None:
        """登记图表查看者，并按需建立非活跃市场的共享临时 Binance WebSocket。"""
        key = (symbol, timeframe)
        async with self._chart_stream_lock:
            self._chart_stream_subscribers[key] = self._chart_stream_subscribers.get(key, 0) + 1
            try:
                await self.cache.retain_markets(
                    self._active_market_keys() | set(self._chart_stream_subscribers)
                )
                await self._sync_temporary_websockets_locked()
            except Exception:
                count = self._chart_stream_subscribers[key] - 1
                if count:
                    self._chart_stream_subscribers[key] = count
                else:
                    self._chart_stream_subscribers.pop(key, None)
                await self.cache.retain_markets(
                    self._active_market_keys() | set(self._chart_stream_subscribers)
                )
                raise

    async def close_chart_stream(self, symbol: str, timeframe: str) -> None:
        """释放图表查看者；最后一个查看者离开时停止并清理临时行情。"""
        key = (symbol, timeframe)
        async with self._chart_stream_lock:
            count = self._chart_stream_subscribers.get(key, 0)
            if count <= 1:
                self._chart_stream_subscribers.pop(key, None)
            else:
                self._chart_stream_subscribers[key] = count - 1
            await self._sync_temporary_websockets_locked()
            await self.cache.retain_markets(
                self._active_market_keys() | set(self._chart_stream_subscribers)
            )

    def is_chart_stream_stale(self, symbol: str, timeframe: str) -> bool:
        """按市场实际所属的常驻或临时连接判断最近推送是否停滞。"""
        manager = self._temporary_websockets.get((symbol, timeframe), self.websocket)
        return manager.is_stale(symbol, timeframe)

    async def apply_config(self, config: ConfigValues) -> None:
        """配置修改后立即重新筛选池，避免等待下一次定时刷新。"""
        # 配置服务已先提交数据库；保留 self.config 旧值供 refresh_pool 判断周期订阅是否变化。
        await self.refresh_pool()

    async def _persist_symbols(
        self,
        available: dict,
        tickers: dict[str, TickerData],
        funding_rates: dict[str, FundingRateData],
    ) -> dict[str, datetime]:
        """持久化市场快照，并返回当前连续活跃周期的入池时间。"""
        observed_at = datetime.now(timezone.utc)
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
                    existing[symbol] = row
                is_active = symbol in self.active_symbols
                # 重启后数据库仍保留连续活跃周期；只有首次跟踪或状态跃迁才重置起点。
                update_active_pool_membership(row, is_active, observed_at)
                if ticker:
                    row.last_price = ticker.last_price
                    row.price_change_percent_24h = ticker.price_change_percent
                    row.quote_volume_24h = ticker.quote_volume
                funding_rate = funding_rates.get(symbol)
                row.funding_rate = funding_rate.funding_rate if funding_rate else None
            await session.commit()
            return {
                symbol: row.active_since
                for symbol, row in existing.items()
                if row.is_active and row.active_since is not None
            }

    async def _initialize_markets(self, markets: set[tuple[str, str]]) -> None:
        """数据库优先恢复缺失市场缓存，仅对缺失或过期部分发起 REST 请求。"""
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        for market in markets:
            queue.put_nowait(market)

        async def worker() -> None:
            while not queue.empty():
                symbol, timeframe = await queue.get()
                try:
                    await self._initialize_symbol_timeframe(symbol, timeframe)
                except Exception:
                    logger.exception("Kline initialization failed for %s %s", symbol, timeframe)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(self.settings.rest_concurrency)]
        await queue.join()
        await asyncio.gather(*workers)

    async def _initialize_symbol_timeframe(self, symbol: str, timeframe: str) -> None:
        """从数据库恢复单个市场周期，并向 Binance 增量补齐或首次全量初始化。"""
        stored = await self._load_stored_klines(symbol, timeframe)
        if not stored:
            # 数据库没有任何历史时才请求 498 根 K 线完成初始化。
            klines = await self.client.klines(symbol, timeframe, self.settings.kline_history_limit)
            await self.cache.initialize(symbol, timeframe, klines)
            await self._persist_closed_klines(klines)
            return

        await self.cache.initialize(symbol, timeframe, stored)
        if has_fresh_closed_history(stored[-1].close_time, timeframe):
            # 正常重启时数据库末根完整 K 线已与当前 K 线相邻，当前 K 线交给 WebSocket 接管即可。
            return
        start_ms = int(stored[-1].open_time.timestamp() * 1000)
        request_limit = min(KLINE_INCREMENTAL_LIMIT, self.settings.kline_history_limit)

        while True:
            # 包含数据库最后一根 K 线，以便 Binance 最终值可以修正本地重叠记录。
            klines = await self.client.klines(symbol, timeframe, request_limit, start_ms=start_ms)
            if not klines:
                return
            for item in klines:
                await self.cache.update(item)
            await self._persist_closed_klines(klines)

            latest = klines[-1]
            if not latest.is_closed or len(klines) < request_limit:
                return
            next_start_ms = int(latest.close_time.timestamp() * 1000)
            if next_start_ms <= start_ms:
                logger.warning("Kline incremental cursor did not advance for %s %s", symbol, timeframe)
                return
            start_ms = next_start_ms
            # 常见重启只请求 20 根；确认存在较大缺口后扩大批次，避免逐小页追赶。
            request_limit = self.settings.kline_history_limit

    async def _load_stored_klines(self, symbol: str, timeframe: str) -> list[KlineData]:
        """读取数据库中最近的完整 K 线，并转换为内存缓存使用的领域结构。"""
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(Kline)
                .where(
                    Kline.symbol == symbol,
                    Kline.timeframe == timeframe,
                    Kline.is_closed.is_(True),
                )
                .order_by(Kline.open_time.desc())
                .limit(self.settings.kline_history_limit)
            )).scalars().all()
        return [
            KlineData(
                symbol=row.symbol,
                timeframe=row.timeframe,
                open_time=row.open_time,
                close_time=row.close_time,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                quote_volume=row.quote_volume,
                taker_buy_quote_volume=row.taker_buy_quote_volume or 0,
                is_closed=row.is_closed,
            )
            for row in reversed(rows)
        ]

    async def on_kline(self, kline: KlineData) -> None:
        """接收实时 K 线，并在发现收盘 K 线缺口时使用 REST 补齐。"""
        is_active = kline.symbol in self.active_symbols
        if kline.is_closed:
            latest_closed = await self.cache.latest_closed(kline.symbol, kline.timeframe)
            if latest_closed and latest_closed.close_time < kline.open_time:
                # 重连期间可能跨过多个周期，REST 返回最近窗口后按开盘时间去重写入缓存。
                repaired = await self.client.klines(kline.symbol, kline.timeframe, 20)
                for item in repaired:
                    if item.is_closed and item.open_time > latest_closed.open_time:
                        await self.cache.update(item)
                # 临时查看行情不应扩展持久化采集范围，只有活跃池数据写入数据库。
                if is_active:
                    await self._persist_closed_klines(repaired)
        closed_now = await self.cache.update(kline)
        if closed_now and is_active:
            await self._persist_closed_klines([kline])
        if await self.kline_broadcaster.has_subscribers(kline.symbol, kline.timeframe):
            # 仅在详情页订阅存在时计算逐根 EMA，避免给全市场高频 K 线链路增加固定开销。
            current, closed = await self.cache.snapshot(kline.symbol, kline.timeframe)
            visible = [*closed, *([current] if current else [])]
            candles = build_chart_candles(visible)
            if (
                candles
                and candles[-1].emas.get(14) is not None
                and candles[-1].emas.get(50) is not None
            ):
                # 启动早期历史缓存尚未装满时，不用空 EMA 覆盖前端已有完整窗口。
                await self.kline_broadcaster.publish(kline.symbol, kline.timeframe, candles[-1])

    async def repair_market_klines(self, symbol: str, timeframe: str, limit: int = 20) -> None:
        """按时间顺序合并最近 K 线，补齐断流期间的完整 K 线并刷新当前 K 线。"""
        klines = await self.client.klines(symbol, timeframe, limit)
        latest_closed = await self.cache.latest_closed(symbol, timeframe)
        latest_closed_open = latest_closed.open_time if latest_closed else None
        relevant = (
            item for item in klines
            if latest_closed_open is None or item.open_time >= latest_closed_open
        )
        for item in sorted(relevant, key=lambda value: value.open_time):
            # 必须处理完整批次，不能只更新最后一根，否则倒数第二根已收盘 K 线会形成图表缺口。
            await self.on_kline(item)

    async def _persist_closed_klines(self, klines: list[KlineData]) -> None:
        """按唯一键保存完整 K 线，并将每个市场的数据库历史限制在配置窗口内。"""
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
                for field in (
                    "close_time", "open", "high", "low", "close", "volume", "quote_volume",
                    "taker_buy_quote_volume", "is_closed",
                )
            },
        )
        async with self.session_factory() as session:
            await session.execute(statement)
            affected_markets = {(item.symbol, item.timeframe) for item in complete}
            # 写入与淘汰处于同一事务，避免并发读取观察到超过保留上限的中间状态。
            await session.execute(
                build_kline_retention_statement(
                    affected_markets,
                    self.settings.kline_history_limit,
                )
            )
            await session.commit()

    async def _realtime_chart_poll_loop(self) -> None:
        """仅在上游推送停滞时，为正在查看的市场按需补充最新 K 线。"""
        while not self._stop.is_set():
            try:
                keys = await self.kline_broadcaster.subscription_keys()
                stale_keys = [key for key in keys if self.is_chart_stream_stale(*key)]
                for symbol, timeframe in stale_keys:
                    # limit=2 的权重最低，且只有存在前端订阅并确认断流时才会请求。
                    await self.repair_market_klines(symbol, timeframe, limit=2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Realtime chart fallback polling failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

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
                # 扫描不能抢在新 K 线事件之前，否则会把上一根已收盘 K 线误记为 100% 进度。
                timeout = max(0, (boundary - now).total_seconds() + SCAN_BOUNDARY_GRACE_SECONDS)
                await asyncio.wait_for(self._stop.wait(), timeout=timeout)
                continue
            except asyncio.TimeoutError:
                pass
            if self.initialization_status != "ready":
                # 历史窗口未全部进入缓存时扫描会基于不完整交易池产生偏差，因此等待下一边界。
                logger.info("Skipping scheduled scan while collector status is %s", self.initialization_status)
                continue
            try:
                await self.scanner.scan(
                    self.active_symbols,
                    self.tickers,
                    self.config,
                    self.funding_rates,
                    self.active_since_by_symbol,
                )
            except Exception:
                logger.exception("Scheduled scan failed")

    async def _performance_loop(self) -> None:
        """每分钟回填已到期的未来价格变化观察点。"""
        while not self._stop.is_set():
            try:
                await self.performance.update()
            except Exception:
                logger.exception("Performance update failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
