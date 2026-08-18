"""列式 K 线环形缓存、增量指标与活跃池清理测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.schemas import KlineData
from app.services.cache.incremental_indicators import IncrementalTechnicalIndicators
from app.services.cache.kline_cache import ColumnarKlineRing, KlineCache
from app.services.cache.technical_indicators import calculate_technical_indicators


def make_kline(index: int, closed: bool = True) -> KlineData:
    """构造包含非整数价格和成交量的确定性测试 K 线。"""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * index)
    base = Decimal("100") + Decimal(index) / Decimal("10")
    direction = Decimal("0.37") if index % 3 else Decimal("-0.21")
    close = base + direction
    return KlineData(
        symbol="BTCUSDT",
        timeframe="15m",
        open_time=start,
        close_time=start + timedelta(minutes=15),
        open=base,
        high=max(base, close) + Decimal("0.43"),
        low=min(base, close) - Decimal("0.29"),
        close=close,
        volume=Decimal("1000.125") + Decimal(index),
        quote_volume=Decimal("100000.875") + Decimal(index) * Decimal("100"),
        taker_buy_quote_volume=Decimal("60000.525") + Decimal(index) * Decimal("60"),
        is_closed=closed,
    )


def assert_indicators_close(left, right, tolerance: Decimal = Decimal("0.000000001")) -> None:
    """比较批量与增量计算器的核心指标，允许 Float64 重建产生极小误差。"""
    assert left is not None and right is not None
    fields = (
        "rsi14", "adx14", "plus_di14", "minus_di14", "atr14",
        "atr14_percent", "adx_slope", "mfi14", "obv",
    )
    for field in fields:
        assert abs(getattr(left, field) - getattr(right, field)) <= tolerance
    for period in (9, 14, 21, 50, 100, 200):
        assert abs(left.ema[period].value - right.ema[period].value) <= tolerance
        assert abs(left.ema[period].slope_percent - right.ema[period].slope_percent) <= tolerance
    assert abs(left.macd.line - right.macd.line) <= tolerance
    assert abs(left.macd.signal - right.macd.signal) <= tolerance
    assert abs(left.macd.histogram - right.macd.histogram) <= tolerance
    assert abs(left.bollinger.upper - right.bollinger.upper) <= tolerance
    assert abs(left.bollinger.percent_b - right.bollinger.percent_b) <= tolerance


def test_columnar_ring_has_fixed_compact_storage_and_overwrites_oldest() -> None:
    """固定环形数组写满后应覆盖最旧 K 线且不增加底层分配。"""
    ring = ColumnarKlineRing("BTCUSDT", "15m", 3)
    ring.load([make_kline(index) for index in range(3)])

    before = ring.allocated_bytes
    mutation = ring.upsert(make_kline(3))
    items = ring.to_klines()

    assert mutation == "appended"
    expected_open_times = [make_kline(index).open_time for index in range(1, 4)]
    assert [item.open_time for item in items] == expected_open_times
    assert ring.allocated_bytes == before == 3 * 72


def test_incremental_indicators_match_existing_batch_calculator() -> None:
    """正常增量路径必须与原 Decimal 批量算法保持一致。"""
    klines = [make_kline(index) for index in range(400)]

    batch = calculate_technical_indicators(klines)
    incremental = IncrementalTechnicalIndicators.from_klines(klines).result()

    assert_indicators_close(batch, incremental, Decimal(0))


def test_ring_eviction_advances_indicators_and_keeps_window_obv() -> None:
    """窗口淘汰应常数时间推进递归指标，同时保持 OBV 对应当前 498 根。"""
    ring = ColumnarKlineRing("BTCUSDT", "15m", 498)
    ring.load([make_kline(index) for index in range(498)])
    ring.upsert(make_kline(498))

    expanded = ring.to_klines()
    batch = calculate_technical_indicators(expanded)

    assert len(expanded) == 498
    incremental = ring.indicators()
    assert incremental is not None and batch is not None
    assert incremental.candle_count == 498
    assert incremental.source_close == batch.source_close
    assert incremental.obv == batch.obv
    # 递归指标持续使用初始化后的标准状态，与固定窗口重播只允许极小收敛差异。
    assert abs(incremental.ema[200].value - batch.ema[200].value) < Decimal("0.001")
    assert abs(incremental.adx14 - batch.adx14) < Decimal("0.001")


@pytest.mark.asyncio
async def test_scan_snapshot_uses_incremental_state_without_expanding_history() -> None:
    """扫描快照应直接返回指标与成交量 EMA，并保留当前未收盘 K 线。"""
    cache = KlineCache(498)
    closed = [make_kline(index) for index in range(250)]
    current = make_kline(250, closed=False)
    await cache.initialize("BTCUSDT", "15m", [*closed, current])

    cached_current, volume_ema, indicators = await cache.scan_snapshot(
        "BTCUSDT", "15m", 12
    )

    assert cached_current == current
    assert volume_ema is not None
    assert indicators is not None
    assert indicators.candle_count == 250
    assert indicators.source_close == closed[-1].close


@pytest.mark.asyncio
async def test_retain_removes_inactive_markets_and_rejects_late_events() -> None:
    """活跃池退出后应释放完整与当前缓存，并拒绝旧订阅迟到事件重新建桶。"""
    cache = KlineCache(498)
    await cache.initialize("BTCUSDT", "15m", [make_kline(0)])
    eth = make_kline(0).model_copy(update={"symbol": "ETHUSDT"})
    await cache.initialize("ETHUSDT", "15m", [eth])

    await cache.retain({"BTCUSDT"}, {"15m"})
    accepted = await cache.update(
        make_kline(1).model_copy(update={"symbol": "ETHUSDT"})
    )

    assert accepted is False
    assert await cache.market_keys() == {("BTCUSDT", "15m")}
    assert await cache.symbols() == {"BTCUSDT"}
    assert await cache.allocated_bytes() == 498 * 72
