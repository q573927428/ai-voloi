"""运行参数边界校验单元测试。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import ConfigValues


def test_document_defaults() -> None:
    config = ConfigValues()
    assert config.timeframes == ["15m", "30m", "1h", "4h", "1d"]
    assert config.volume_ema_period == 12
    assert config.oi_lookback_minutes == 15
    assert config.oi_change_threshold_percent == Decimal("0.05")


def test_invalid_progress_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfigValues(min_progress_percent=101)
