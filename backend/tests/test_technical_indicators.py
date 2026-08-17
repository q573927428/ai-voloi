"""价格 EMA、RSI、ATR、ADX 及斜率指标单元测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.schemas import KlineData
from app.services.cache.technical_indicators import (
    build_indicator_response,
    calculate_technical_indicators,
)


def price_kline(index: int, close: Decimal) -> KlineData:
    """构造固定振幅的完整价格 K 线。"""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * index)
    return KlineData(
        symbol="BTCUSDT",
        timeframe="15m",
        open_time=start,
        close_time=start + timedelta(minutes=15),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=100,
        quote_volume=10000,
        is_closed=True,
    )


def test_rising_market_has_full_strength_and_positive_ema_slopes() -> None:
    klines = [price_kline(index, Decimal(100 + index)) for index in range(60)]

    result = calculate_technical_indicators(klines)

    assert result is not None
    assert result.rsi14 == Decimal(100)
    assert result.adx14 == Decimal(100)
    assert result.adx_slope == Decimal(0)
    assert result.atr14 == Decimal(2)
    assert result.ema14_slope_percent > 0
    assert result.ema50_slope_percent > 0
    assert result.ema14 > result.ema50


def test_flat_market_has_neutral_rsi_and_zero_trend_strength() -> None:
    klines = [price_kline(index, Decimal(100)) for index in range(60)]

    result = calculate_technical_indicators(klines)

    assert result is not None
    assert result.rsi14 == Decimal(50)
    assert result.adx14 == Decimal(0)
    assert result.adx_slope == Decimal(0)
    assert result.atr14 == Decimal(2)
    assert result.ema14_slope_percent == Decimal(0)
    assert result.ema50_slope_percent == Decimal(0)
    assert result.plus_di14 == Decimal(0)
    assert result.minus_di14 == Decimal(0)
    assert result.macd.line == Decimal(0)
    assert result.macd.signal == Decimal(0)
    assert result.macd.histogram == Decimal(0)
    assert result.bollinger.upper == result.bollinger.middle == result.bollinger.lower
    assert result.bollinger.percent_b == Decimal("0.5")
    assert result.mfi14 == Decimal(50)
    assert result.obv == Decimal(0)


def test_insufficient_history_returns_none() -> None:
    klines = [price_kline(index, Decimal(100 + index)) for index in range(50)]
    assert calculate_technical_indicators(klines) is None


def test_full_history_contains_phase_one_and_phase_two_indicators() -> None:
    """足量上涨行情应产出完整 EMA、方向、波动率、动量和量价指标。"""
    klines = [price_kline(index, Decimal(100 + index)) for index in range(400)]

    result = calculate_technical_indicators(klines)

    assert result is not None
    assert result.warmup_complete is True
    assert result.ema_alignment == "bullish"
    assert set(result.ema) == {9, 14, 21, 50, 100, 200}
    assert result.ema[200].value is not None
    assert result.ema[9].distance_percent > 0
    assert result.plus_di14 > result.minus_di14
    assert result.atr14_percent > 0
    assert result.macd.line is not None
    assert result.macd.signal is not None
    assert result.macd.histogram is not None
    assert result.bollinger.upper > result.bollinger.middle > result.bollinger.lower
    assert result.mfi14 == Decimal(100)
    assert result.obv > 0


def test_indicator_response_only_exposes_requested_ema_periods() -> None:
    """公共响应可自定义 EMA 周期，同时保留其他完整指标分组。"""
    result = calculate_technical_indicators(
        [price_kline(index, Decimal(100 + index)) for index in range(250)],
        (9, 30),
    )

    assert result is not None
    response = build_indicator_response("BTCUSDT", "15m", result, (9, 30))
    assert set(response.trend.ema) == {"9", "30"}
    assert response.warmup_complete is True
    assert response.momentum.macd.line is not None
    assert response.volatility.bollinger.upper is not None
    assert response.volume.mfi14 == Decimal(100)
