"""运行概览时间统计单元测试。"""

from datetime import datetime, timezone

from app.api.routes import utc_plus_8_day_range


def test_utc_plus_8_day_range_uses_beijing_midnight() -> None:
    """今日 Signal 应按 UTC+8 零点切日，而不是按 UTC 零点切日。"""
    start, end = utc_plus_8_day_range(datetime(2026, 8, 18, 1, 30, tzinfo=timezone.utc))

    assert start == datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)


def test_utc_plus_8_day_range_rolls_over_at_16_utc() -> None:
    """UTC 16:00 到来时应进入 UTC+8 的下一个自然日。"""
    start, end = utc_plus_8_day_range(datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc))

    assert start == datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)
