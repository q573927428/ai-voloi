"""成交量候选筛选、OI 时间匹配与 Signal 持久化。"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models import OpenInterestSnapshot, ScannerRun, Signal, SignalFuturePerformance
from app.schemas import ConfigValues, KlineData, TickerData
from app.services.binance.client import BinanceClient
from app.services.cache.kline_cache import KlineCache, kline_progress, volume_ema
from app.services.cache.technical_indicators import TechnicalIndicators, calculate_technical_indicators

logger = logging.getLogger(__name__)


def select_oi_range(points, target_start: datetime, now: datetime):
    """选择最接近 K 线开盘的 OI 起点和扫描时可用的最新终点。"""
    usable = sorted((p for p in points if p.timestamp <= now), key=lambda p: p.timestamp)
    if len(usable) < 2:
        return None
    oldest = min(usable, key=lambda p: abs((p.timestamp - target_start).total_seconds()))
    newest = usable[-1]
    if oldest.timestamp >= newest.timestamp or oldest.open_interest <= 0:
        return None
    return oldest, newest


class Scanner:
    """按 Volume 先筛选、再按需查询 OI 的单次扫描执行器。"""
    def __init__(
        self,
        cache: KlineCache,
        client: BinanceClient,
        session_factory: async_sessionmaker[AsyncSession],
        publish,
    ):
        self.cache = cache
        self.client = client
        self.session_factory = session_factory
        self.publish = publish
        self._lock = asyncio.Lock()

    async def scan(self, symbols: set[str], tickers: dict[str, TickerData], config: ConfigValues) -> ScannerRun:
        """扫描活跃池并持久化审计记录、OI 快照与合格 Signal。"""
        if self._lock.locked():
            raise RuntimeError("Scanner is already running")
        async with self._lock:
            started = datetime.now(timezone.utc)
            clock = perf_counter()
            # SQLAlchemy 列默认值在 INSERT 时才执行，扫描过程会先递增计数，因此必须显式初始化。
            run = ScannerRun(
                started_at=started,
                symbol_count=len(symbols),
                candidate_count=0,
                oi_request_count=0,
                signal_count=0,
                error_count=0,
            )
            errors: list[str] = []
            candidates: list[
                tuple[str, str, KlineData, Decimal, Decimal, Decimal, TechnicalIndicators]
            ] = []

            for timeframe in config.timeframes:
                for symbol in symbols:
                    current, closed = await self.cache.snapshot(symbol, timeframe)
                    if not current:
                        continue
                    progress = kline_progress(current, started)
                    # 扫描只针对正在形成的 K 线；缓存短暂滞后时不能把上一根已收盘 K 线误记成 100%。
                    if progress >= 1:
                        continue
                    if progress * 100 < config.min_progress_percent:
                        continue
                    ema = volume_ema(closed, config.volume_ema_period)
                    if not ema or ema <= 0 or progress <= 0:
                        continue
                    estimated = current.volume / progress
                    ratio = estimated / ema
                    if ratio >= config.volume_multiplier:
                        indicators = calculate_technical_indicators(closed)
                        # Signal 必须携带完整指标快照，历史数据不足时不能生成半完整记录。
                        if indicators:
                            candidates.append((symbol, timeframe, current, progress, ema, ratio, indicators))

            run.candidate_count = len(candidates)
            signal_models: list[Signal] = []
            oi_rows: list[OpenInterestSnapshot] = []
            for symbol, timeframe, current, progress, ema, ratio, indicators in candidates:
                try:
                    run.oi_request_count += 1
                    points = await self.client.open_interest(
                        symbol, timeframe, int(current.open_time.timestamp() * 1000)
                    )
                    matched = select_oi_range(points, current.open_time, started)
                    if not matched:
                        continue
                    oldest, newest = matched
                    change = newest.open_interest - oldest.open_interest
                    change_percent = change / oldest.open_interest * 100
                    oi_rows.extend([
                        OpenInterestSnapshot(symbol=symbol, timeframe=timeframe, open_interest=oldest.open_interest, timestamp=oldest.timestamp),
                        OpenInterestSnapshot(symbol=symbol, timeframe=timeframe, open_interest=newest.open_interest, timestamp=newest.timestamp),
                    ])
                    if change_percent < config.oi_change_threshold_percent:
                        continue
                    ticker = tickers.get(symbol)
                    if not ticker:
                        continue
                    signal_models.append(Signal(
                        symbol=symbol, timeframe=timeframe, detected_at=started,
                        open_time=current.open_time, close_time=current.close_time,
                        open=current.open, high=current.high, low=current.low, current_price=current.close,
                        current_volume=current.volume, current_quote_volume=current.quote_volume,
                        progress_percent=progress * 100, estimated_volume=current.volume / progress,
                        volume_ema=ema, volume_ema_period=config.volume_ema_period,
                        volume_ratio=ratio, volume_multiplier=config.volume_multiplier,
                        ema14=indicators.ema14, ema50=indicators.ema50,
                        rsi14=indicators.rsi14, adx14=indicators.adx14, atr14=indicators.atr14,
                        adx_slope=indicators.adx_slope,
                        ema14_slope_percent=indicators.ema14_slope_percent,
                        ema50_slope_percent=indicators.ema50_slope_percent,
                        oldest_oi=oldest.open_interest, newest_oi=newest.open_interest,
                        oi_change_absolute=change, oi_change_percent=change_percent,
                        oldest_timestamp=oldest.timestamp, newest_timestamp=newest.timestamp,
                        last_price=ticker.last_price, price_change_percent_24h=ticker.price_change_percent,
                        quote_volume_24h=ticker.quote_volume, signal_type="VOLUME_OI_ANOMALY",
                    ))
                except Exception as exc:
                    logger.exception("OI scan failed for %s %s", symbol, timeframe)
                    errors.append(f"{symbol}/{timeframe}: {exc}")

            run.signal_count = len(signal_models)
            run.error_count = len(errors)
            run.error_summary = "\n".join(errors)[:8000] or None
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = int((perf_counter() - clock) * 1000)
            async with self.session_factory() as session:
                session.add_all(oi_rows)
                for signal in signal_models:
                    session.add(signal)
                    signal.future_performance = SignalFuturePerformance()
                session.add(run)
                await session.commit()
                for signal in signal_models:
                    await session.refresh(signal)
                    await self.publish(signal)
            return run
