"""成交量指标与 K 线进度单元测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.schemas import KlineData
from app.services.cache.kline_cache import kline_progress, volume_ema


def make_kline(volume: str, offset: int = 0) -> KlineData:
    """构造已收盘 K 线测试数据。"""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * offset)
    return KlineData(symbol="BTCUSDT", timeframe="15m", open_time=start, close_time=start + timedelta(minutes=15), open=1, high=1, low=1, close=1, volume=volume, quote_volume=volume, is_closed=True)


def test_volume_ema_uses_latest_closed_period_only() -> None:
    values = [make_kline(str(value), value) for value in range(1, 15)]
    result = volume_ema(values, 12)
    alpha = Decimal(2) / Decimal(13)
    expected = Decimal(3)
    for value in range(4, 15):
        expected = Decimal(value) * alpha + expected * (1 - alpha)
    assert result == expected


def test_volume_ema_requires_full_period() -> None:
    assert volume_ema([make_kline("10")] * 11, 12) is None


def test_progress_is_clamped() -> None:
    item = make_kline("10")
    assert kline_progress(item, item.open_time + timedelta(minutes=7, seconds=30)) == Decimal("0.5")
    assert kline_progress(item, item.open_time - timedelta(seconds=1)) == 0
    assert kline_progress(item, item.close_time + timedelta(seconds=1)) == 1
