"""OI 时间戳匹配单元测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.schemas import OIPoint
from app.services.scanner.scanner import oi_history_period, select_oi_range


def test_selects_point_nearest_kline_start_and_latest_available() -> None:
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    points = [OIPoint(timestamp=start + timedelta(minutes=minute), open_interest=Decimal(100 + minute)) for minute in (-5, 1, 6, 11, 16)]
    oldest, newest = select_oi_range(points, start, start + timedelta(minutes=12))
    assert oldest.timestamp == start + timedelta(minutes=1)
    assert newest.timestamp == start + timedelta(minutes=11)


def test_rejects_invalid_or_insufficient_range() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert select_oi_range([], start, start) is None
    same = [OIPoint(timestamp=start, open_interest=Decimal(100)), OIPoint(timestamp=start, open_interest=Decimal(110))]
    assert select_oi_range(same, start, start) is None


def test_selects_oi_sampling_period_that_covers_long_window() -> None:
    """单次最多 100 点时，日线回看应使用 15m 粒度覆盖到最新时刻。"""
    assert oi_history_period(240) == "5m"
    assert oi_history_period(1440) == "15m"
    assert oi_history_period(10080) == "2h"
