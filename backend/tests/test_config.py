"""运行参数边界校验单元测试。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import ConfigValues


def test_document_defaults() -> None:
    config = ConfigValues()
    assert config.timeframes == ["15m", "30m", "1h", "4h", "1d"]
    assert config.volume_ema_period == 12
    assert config.oi_lookback_minutes_by_timeframe == {
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }
    assert set(config.oi_change_threshold_percent_by_timeframe.values()) == {Decimal("0.05")}


def test_invalid_progress_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfigValues(min_progress_percent=101)


def test_legacy_default_oi_window_is_upgraded_to_timeframe_defaults() -> None:
    """旧版默认 15 分钟配置升级后应启用新的逐周期默认窗口。"""
    config = ConfigValues.model_validate({"oi_lookback_minutes": 15})

    assert config.oi_lookback_for("30m") == 30
    assert config.oi_lookback_for("1d") == 1440


def test_legacy_custom_oi_window_is_preserved_for_every_timeframe() -> None:
    """用户主动设置过的旧版全局窗口不能在升级时被静默覆盖。"""
    config = ConfigValues.model_validate({"oi_lookback_minutes": 45})

    assert set(config.oi_lookback_minutes_by_timeframe.values()) == {45}


def test_invalid_timeframe_oi_window_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfigValues(oi_lookback_minutes_by_timeframe={"1h": 4})


def test_legacy_oi_threshold_is_preserved_for_every_timeframe() -> None:
    """旧版全局 OI 阈值升级后应应用到所有已配置周期。"""
    config = ConfigValues.model_validate({
        "timeframes": ["15m", "1h"],
        "oi_change_threshold_percent": "0.25",
    })

    assert config.oi_change_threshold_for("15m") == Decimal("0.25")
    assert config.oi_change_threshold_for("1h") == Decimal("0.25")


def test_timeframe_oi_threshold_can_be_configured_independently() -> None:
    """每个 K 线周期应读取自己的 OI 变化阈值。"""
    config = ConfigValues(oi_change_threshold_percent_by_timeframe={
        "15m": Decimal("0.05"),
        "1h": Decimal("0.2"),
    })

    assert config.oi_change_threshold_for("15m") == Decimal("0.05")
    assert config.oi_change_threshold_for("1h") == Decimal("0.2")
