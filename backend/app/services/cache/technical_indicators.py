"""基于已收盘 K 线计算价格趋势、动量、波动率与趋势强度指标。"""

from dataclasses import dataclass
from decimal import Decimal

from app.schemas import KlineData

HUNDRED = Decimal("100")


@dataclass(frozen=True)
class TechnicalIndicators:
    """Signal 检测时刻的不可变技术指标快照。"""

    ema14: Decimal
    ema50: Decimal
    rsi14: Decimal
    adx14: Decimal
    atr14: Decimal
    adx_slope: Decimal
    ema14_slope_percent: Decimal
    ema50_slope_percent: Decimal


def ema_series(values: list[Decimal], period: int) -> list[Decimal]:
    """以首个周期的 SMA 为种子生成标准 EMA 序列。"""
    if len(values) < period:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    current = sum(values[:period], Decimal(0)) / Decimal(period)
    result = [current]
    for value in values[period:]:
        current = value * alpha + current * (Decimal(1) - alpha)
        result.append(current)
    return result


def _rsi(closes: list[Decimal], period: int) -> Decimal | None:
    """使用 Wilder 平滑计算最新 RSI。"""
    if len(closes) < period + 1:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, Decimal(0)) for change in changes]
    losses = [max(-change, Decimal(0)) for change in changes]
    average_gain = sum(gains[:period], Decimal(0)) / Decimal(period)
    average_loss = sum(losses[:period], Decimal(0)) / Decimal(period)
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = (average_gain * Decimal(period - 1) + gain) / Decimal(period)
        average_loss = (average_loss * Decimal(period - 1) + loss) / Decimal(period)
    if average_loss == 0:
        return HUNDRED if average_gain > 0 else Decimal("50")
    relative_strength = average_gain / average_loss
    return HUNDRED - HUNDRED / (Decimal(1) + relative_strength)


def _atr_and_adx(klines: list[KlineData], period: int) -> tuple[Decimal, Decimal, Decimal] | None:
    """使用 Wilder 平滑计算最新 ATR、ADX 及相邻 ADX 点数斜率。"""
    if len(klines) < period * 2 + 1:
        return None
    true_ranges: list[Decimal] = []
    positive_dm: list[Decimal] = []
    negative_dm: list[Decimal] = []
    for previous, current in zip(klines, klines[1:]):
        true_ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
        upward = current.high - previous.high
        downward = previous.low - current.low
        positive_dm.append(upward if upward > downward and upward > 0 else Decimal(0))
        negative_dm.append(downward if downward > upward and downward > 0 else Decimal(0))

    atr = sum(true_ranges[:period], Decimal(0)) / Decimal(period)
    smoothed_positive = sum(positive_dm[:period], Decimal(0)) / Decimal(period)
    smoothed_negative = sum(negative_dm[:period], Decimal(0)) / Decimal(period)
    dx_values: list[Decimal] = []

    def append_dx() -> None:
        """根据当前 Wilder 平滑值追加一个 DX 观察点。"""
        if atr == 0:
            dx_values.append(Decimal(0))
            return
        positive_di = HUNDRED * smoothed_positive / atr
        negative_di = HUNDRED * smoothed_negative / atr
        denominator = positive_di + negative_di
        dx_values.append(HUNDRED * abs(positive_di - negative_di) / denominator if denominator else Decimal(0))

    append_dx()
    for true_range, plus_dm, minus_dm in zip(
        true_ranges[period:], positive_dm[period:], negative_dm[period:]
    ):
        atr = (atr * Decimal(period - 1) + true_range) / Decimal(period)
        smoothed_positive = (smoothed_positive * Decimal(period - 1) + plus_dm) / Decimal(period)
        smoothed_negative = (smoothed_negative * Decimal(period - 1) + minus_dm) / Decimal(period)
        append_dx()

    if len(dx_values) < period + 1:
        return None
    adx = sum(dx_values[:period], Decimal(0)) / Decimal(period)
    adx_series = [adx]
    for dx in dx_values[period:]:
        adx = (adx * Decimal(period - 1) + dx) / Decimal(period)
        adx_series.append(adx)
    return atr, adx_series[-1], adx_series[-1] - adx_series[-2]


def calculate_technical_indicators(
    closed_klines: list[KlineData],
    period: int = 14,
    long_ema_period: int = 50,
) -> TechnicalIndicators | None:
    """只使用完整 K 线计算 Signal 所需指标，数据不足时返回 None。"""
    klines = sorted((item for item in closed_klines if item.is_closed), key=lambda item: item.open_time)
    closes = [item.close for item in klines]
    ema14_series = ema_series(closes, period)
    ema50_series = ema_series(closes, long_ema_period)
    rsi14 = _rsi(closes, period)
    atr_adx = _atr_and_adx(klines, period)
    if len(ema14_series) < 2 or len(ema50_series) < 2 or rsi14 is None or atr_adx is None:
        return None
    atr14, adx14, adx_slope = atr_adx
    ema14_previous, ema14 = ema14_series[-2:]
    ema50_previous, ema50 = ema50_series[-2:]
    ema14_slope = (ema14 - ema14_previous) / abs(ema14_previous) * HUNDRED if ema14_previous else Decimal(0)
    ema50_slope = (ema50 - ema50_previous) / abs(ema50_previous) * HUNDRED if ema50_previous else Decimal(0)
    return TechnicalIndicators(
        ema14=ema14,
        ema50=ema50,
        rsi14=rsi14,
        adx14=adx14,
        atr14=atr14,
        adx_slope=adx_slope,
        ema14_slope_percent=ema14_slope,
        ema50_slope_percent=ema50_slope,
    )
