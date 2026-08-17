"""成交量候选筛选、OI 时间匹配与 Signal 持久化。"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import perf_counter

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models import OpenInterestSnapshot, ScannerRun, Signal, SignalFuturePerformance
from app.schemas import ConfigValues, KlineData, OIPoint, TickerData
from app.services.binance.client import BinanceClient
from app.services.cache.kline_cache import KlineCache, kline_progress, volume_ema
from app.services.cache.technical_indicators import TechnicalIndicators, calculate_technical_indicators

logger = logging.getLogger(__name__)

# Binance 单次最多取 100 个 OI 点；按窗口自动放大采样粒度，确保长窗口仍覆盖到检测时刻。
OI_HISTORY_PERIODS = (
    (5, "5m"),
    (15, "15m"),
    (30, "30m"),
    (60, "1h"),
    (120, "2h"),
    (240, "4h"),
    (360, "6h"),
    (720, "12h"),
    (1440, "1d"),
)


def oi_history_period(lookback_minutes: int) -> str:
    """选择能在 100 个观察点内覆盖完整回看窗口的最细 OI 采样周期。"""
    minimum_period_minutes = (lookback_minutes + 98) // 99
    for period_minutes, period in OI_HISTORY_PERIODS:
        if period_minutes >= minimum_period_minutes:
            return period
    return "1d"


def select_oi_range(points, target_start: datetime, now: datetime):
    """选择最接近回看起点的 OI 和扫描时可用的最新 OI。"""
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

    async def _existing_signal_keys(
        self,
        keys: set[tuple[str, str, datetime]],
    ) -> set[tuple[str, str, datetime]]:
        """查询已经产生 Signal 的 K 线，避免重复请求 OI 和重复入库。"""
        if not keys:
            return set()
        async with self.session_factory() as session:
            rows = await session.execute(
                select(Signal.symbol, Signal.timeframe, Signal.open_time).where(
                    tuple_(Signal.symbol, Signal.timeframe, Signal.open_time).in_(keys)
                )
            )
        return {(symbol, timeframe, open_time) for symbol, timeframe, open_time in rows.all()}

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
            candidate_keys = {
                (symbol, timeframe, current.open_time)
                for symbol, timeframe, current, *_ in candidates
            }
            existing_signal_keys = await self._existing_signal_keys(candidate_keys)
            signal_models: list[Signal] = []
            oi_rows: list[OpenInterestSnapshot] = []
            # 相同交易对和回看窗口只查询一次，供同轮扫描中的多个 K 线周期复用。
            oi_ranges: dict[tuple[str, int], tuple[OIPoint, OIPoint] | None] = {}
            for symbol, timeframe, current, progress, ema, ratio, indicators in candidates:
                signal_key = (symbol, timeframe, current.open_time)
                # 同一根 K 线只在首次满足组合条件时生成一次 Signal。
                if signal_key in existing_signal_keys:
                    continue
                try:
                    oi_lookback_minutes = config.oi_lookback_for(timeframe)
                    oi_cache_key = (symbol, oi_lookback_minutes)
                    if oi_cache_key not in oi_ranges:
                        run.oi_request_count += 1
                        oi_target_start = started - timedelta(minutes=oi_lookback_minutes)
                        points = await self.client.open_interest(
                            symbol,
                            oi_history_period(oi_lookback_minutes),
                            int(oi_target_start.timestamp() * 1000),
                        )
                        oi_ranges[oi_cache_key] = select_oi_range(points, oi_target_start, started)
                    matched = oi_ranges[oi_cache_key]
                    if not matched:
                        continue
                    oldest, newest = matched
                    change = newest.open_interest - oldest.open_interest
                    change_percent = change / oldest.open_interest * 100
                    oi_rows.extend([
                        OpenInterestSnapshot(symbol=symbol, timeframe=timeframe, open_interest=oldest.open_interest, timestamp=oldest.timestamp),
                        OpenInterestSnapshot(symbol=symbol, timeframe=timeframe, open_interest=newest.open_interest, timestamp=newest.timestamp),
                    ])
                    # 不同 K 线周期使用各自阈值，避免短周期与长周期共用同一灵敏度。
                    if change_percent < config.oi_change_threshold_for(timeframe):
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
                        oi_lookback_minutes=oi_lookback_minutes,
                        oldest_timestamp=oldest.timestamp, newest_timestamp=newest.timestamp,
                        last_price=ticker.last_price, price_change_percent_24h=ticker.price_change_percent,
                        quote_volume_24h=ticker.quote_volume, signal_type="VOLUME_OI_ANOMALY",
                    ))
                    existing_signal_keys.add(signal_key)
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
