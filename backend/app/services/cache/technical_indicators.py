"""基于已收盘 K 线计算趋势、动量、波动率与量价技术指标。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.schemas import (
    AdxIndicatorRead,
    AtrIndicatorRead,
    BollingerIndicatorRead,
    EmaIndicatorRead,
    KlineData,
    MacdIndicatorRead,
    MarketIndicatorsRead,
    MomentumIndicatorsRead,
    TrendIndicatorsRead,
    VolatilityIndicatorsRead,
    VolumeIndicatorsRead,
)

HUNDRED = Decimal("100")
DEFAULT_EMA_PERIODS = (9, 14, 21, 50, 100, 200)


@dataclass(frozen=True)
class EmaIndicator:
    """单个 EMA 观察点，包含价格、单根斜率及收盘价距离。"""

    period: int
    value: Decimal | None
    slope_percent: Decimal | None
    distance_percent: Decimal | None


@dataclass(frozen=True)
class MacdIndicator:
    """标准 MACD(12, 26, 9) 的最新观察点。"""

    line: Decimal | None
    signal: Decimal | None
    histogram: Decimal | None


@dataclass(frozen=True)
class BollingerIndicator:
    """布林带(20, 2) 的最新价格边界与标准化位置。"""

    upper: Decimal | None
    middle: Decimal | None
    lower: Decimal | None
    bandwidth_percent: Decimal | None
    percent_b: Decimal | None


@dataclass(frozen=True)
class TechnicalIndicators:
    """Signal 检测时刻的不可变完整技术指标快照。"""

    source_close: Decimal
    as_of: datetime
    candle_count: int
    ema: dict[int, EmaIndicator]
    ema_alignment: str
    rsi14: Decimal | None
    adx14: Decimal | None
    plus_di14: Decimal | None
    minus_di14: Decimal | None
    atr14: Decimal | None
    atr14_percent: Decimal | None
    adx_slope: Decimal | None
    macd: MacdIndicator
    bollinger: BollingerIndicator
    mfi14: Decimal | None
    obv: Decimal | None

    @property
    def warmup_complete(self) -> bool:
        """历史达到最长默认 EMA 两倍且全部有值时返回 True。"""
        return self.candle_count >= max(DEFAULT_EMA_PERIODS) * 2 and all(
            self.ema[period].value is not None for period in DEFAULT_EMA_PERIODS
        )

    def ema_value(self, period: int) -> Decimal | None:
        """返回指定周期 EMA，未知周期返回 None。"""
        metric = self.ema.get(period)
        return metric.value if metric else None

    def ema_slope(self, period: int) -> Decimal | None:
        """返回指定周期 EMA 百分比斜率。"""
        metric = self.ema.get(period)
        return metric.slope_percent if metric else None

    @property
    def ema14(self) -> Decimal | None:
        """兼容旧 Signal 列的 EMA14 值。"""
        return self.ema_value(14)

    @property
    def ema50(self) -> Decimal | None:
        """兼容旧 Signal 列的 EMA50 值。"""
        return self.ema_value(50)

    @property
    def ema14_slope_percent(self) -> Decimal | None:
        """兼容旧 Signal 列的 EMA14 斜率。"""
        return self.ema_slope(14)

    @property
    def ema50_slope_percent(self) -> Decimal | None:
        """兼容旧 Signal 列的 EMA50 斜率。"""
        return self.ema_slope(50)


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


def _ema_indicator(closes: list[Decimal], period: int) -> EmaIndicator:
    """计算单个 EMA 及相对最新完整 K 线收盘价的标准化数据。"""
    series = ema_series(closes, period)
    if not series:
        return EmaIndicator(period, None, None, None)
    value = series[-1]
    slope = None
    if len(series) >= 2:
        previous = series[-2]
        slope = (value - previous) / abs(previous) * HUNDRED if previous else Decimal(0)
    distance = (closes[-1] - value) / abs(value) * HUNDRED if value else Decimal(0)
    return EmaIndicator(period, value, slope, distance)


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


def _atr_and_adx(
    klines: list[KlineData], period: int
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal] | None:
    """使用 Wilder 平滑计算 ATR、ADX、ADX 斜率及最新正负方向指标。"""
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

    def directional_values() -> tuple[Decimal, Decimal, Decimal]:
        """根据当前 Wilder 平滑值生成 +DI、-DI 和 DX。"""
        if atr == 0:
            return Decimal(0), Decimal(0), Decimal(0)
        plus_di = HUNDRED * smoothed_positive / atr
        minus_di = HUNDRED * smoothed_negative / atr
        denominator = plus_di + minus_di
        dx = HUNDRED * abs(plus_di - minus_di) / denominator if denominator else Decimal(0)
        return plus_di, minus_di, dx

    plus_di, minus_di, dx = directional_values()
    dx_values.append(dx)
    for true_range, plus_dm, minus_dm in zip(
        true_ranges[period:], positive_dm[period:], negative_dm[period:]
    ):
        atr = (atr * Decimal(period - 1) + true_range) / Decimal(period)
        smoothed_positive = (smoothed_positive * Decimal(period - 1) + plus_dm) / Decimal(period)
        smoothed_negative = (smoothed_negative * Decimal(period - 1) + minus_dm) / Decimal(period)
        plus_di, minus_di, dx = directional_values()
        dx_values.append(dx)

    if len(dx_values) < period + 1:
        return None
    adx = sum(dx_values[:period], Decimal(0)) / Decimal(period)
    adx_series = [adx]
    for dx in dx_values[period:]:
        adx = (adx * Decimal(period - 1) + dx) / Decimal(period)
        adx_series.append(adx)
    return atr, adx_series[-1], adx_series[-1] - adx_series[-2], plus_di, minus_di


def _macd(closes: list[Decimal]) -> MacdIndicator:
    """计算标准 MACD(12, 26, 9)，所有均线均采用 SMA 种子。"""
    fast = ema_series(closes, 12)
    slow = ema_series(closes, 26)
    if not slow:
        return MacdIndicator(None, None, None)
    macd_values = [
        fast[index - 11] - slow[index - 25]
        for index in range(25, len(closes))
    ]
    signal_values = ema_series(macd_values, 9)
    if not signal_values:
        return MacdIndicator(macd_values[-1], None, None)
    line = macd_values[-1]
    signal = signal_values[-1]
    return MacdIndicator(line, signal, line - signal)


def _bollinger(closes: list[Decimal], period: int = 20) -> BollingerIndicator:
    """计算布林带，标准差使用当前窗口总体标准差。"""
    if len(closes) < period:
        return BollingerIndicator(None, None, None, None, None)
    window = closes[-period:]
    middle = sum(window, Decimal(0)) / Decimal(period)
    variance = sum(((value - middle) ** 2 for value in window), Decimal(0)) / Decimal(period)
    deviation = variance.sqrt()
    upper = middle + Decimal(2) * deviation
    lower = middle - Decimal(2) * deviation
    width = upper - lower
    bandwidth = width / abs(middle) * HUNDRED if middle else Decimal(0)
    percent_b = (closes[-1] - lower) / width if width else Decimal("0.5")
    return BollingerIndicator(upper, middle, lower, bandwidth, percent_b)


def _mfi(klines: list[KlineData], period: int = 14) -> Decimal | None:
    """按典型价格与成交量计算最新 Money Flow Index。"""
    if len(klines) < period + 1:
        return None
    typical = [(item.high + item.low + item.close) / Decimal(3) for item in klines]
    positive = Decimal(0)
    negative = Decimal(0)
    for index in range(len(klines) - period, len(klines)):
        flow = typical[index] * klines[index].volume
        if typical[index] > typical[index - 1]:
            positive += flow
        elif typical[index] < typical[index - 1]:
            negative += flow
    if negative == 0:
        return HUNDRED if positive > 0 else Decimal("50")
    ratio = positive / negative
    return HUNDRED - HUNDRED / (Decimal(1) + ratio)


def _obv(klines: list[KlineData]) -> Decimal | None:
    """以当前输入历史窗口首根为零点计算 On-Balance Volume。"""
    if not klines:
        return None
    value = Decimal(0)
    for previous, current in zip(klines, klines[1:]):
        if current.close > previous.close:
            value += current.volume
        elif current.close < previous.close:
            value -= current.volume
    return value


def _ema_alignment(ema: dict[int, EmaIndicator]) -> str:
    """根据默认 EMA 集合判断多头、空头或交错排列。"""
    values = [ema[period].value for period in DEFAULT_EMA_PERIODS]
    if any(value is None for value in values):
        return "insufficient_data"
    comparable = [value for value in values if value is not None]
    if all(left > right for left, right in zip(comparable, comparable[1:])):
        return "bullish"
    if all(left < right for left, right in zip(comparable, comparable[1:])):
        return "bearish"
    return "mixed"


def _ema_alignment_for_periods(
    ema: dict[int, EmaIndicator], periods: tuple[int, ...]
) -> str:
    """按响应实际包含的快慢 EMA 周期计算排列，避免隐藏周期影响接口结果。"""
    ordered = sorted(dict.fromkeys(periods))
    values = [ema[period].value for period in ordered]
    if any(value is None for value in values):
        return "insufficient_data"
    comparable = [value for value in values if value is not None]
    if all(left > right for left, right in zip(comparable, comparable[1:])):
        return "bullish"
    if all(left < right for left, right in zip(comparable, comparable[1:])):
        return "bearish"
    return "mixed"


def calculate_technical_indicators(
    closed_klines: list[KlineData],
    ema_periods: tuple[int, ...] = DEFAULT_EMA_PERIODS,
) -> TechnicalIndicators | None:
    """只使用完整 K 线计算指标；核心 EMA50 或 ADX 数据不足时返回 None。"""
    klines = sorted((item for item in closed_klines if item.is_closed), key=lambda item: item.open_time)
    if not klines:
        return None
    closes = [item.close for item in klines]
    requested_periods = tuple(dict.fromkeys((*DEFAULT_EMA_PERIODS, *ema_periods)))
    ema = {period: _ema_indicator(closes, period) for period in requested_periods}
    atr_adx = _atr_and_adx(klines, 14)
    # Signal 的历史兼容字段依赖 EMA14/EMA50，维持既有的最低数据完整性门槛。
    if ema[14].slope_percent is None or ema[50].slope_percent is None or atr_adx is None:
        return None
    atr14, adx14, adx_slope, plus_di14, minus_di14 = atr_adx
    source_close = closes[-1]
    return TechnicalIndicators(
        source_close=source_close,
        as_of=klines[-1].close_time,
        candle_count=len(klines),
        ema=ema,
        ema_alignment=_ema_alignment(ema),
        rsi14=_rsi(closes, 14),
        adx14=adx14,
        plus_di14=plus_di14,
        minus_di14=minus_di14,
        atr14=atr14,
        atr14_percent=atr14 / abs(source_close) * HUNDRED if source_close else Decimal(0),
        adx_slope=adx_slope,
        macd=_macd(closes),
        bollinger=_bollinger(closes),
        mfi14=_mfi(klines),
        obv=_obv(klines),
    )


def build_indicator_response(
    symbol: str,
    timeframe: str,
    indicators: TechnicalIndicators,
    ema_periods: tuple[int, ...] = DEFAULT_EMA_PERIODS,
) -> MarketIndicatorsRead:
    """把内部计算结果转换为稳定、分组的公共 API 与 Signal 快照结构。"""
    ema = {
        str(period): EmaIndicatorRead(
            period=period,
            value=indicators.ema[period].value,
            slope_percent=indicators.ema[period].slope_percent,
            distance_percent=indicators.ema[period].distance_percent,
        )
        for period in ema_periods
    }
    return MarketIndicatorsRead(
        symbol=symbol,
        timeframe=timeframe,
        as_of=indicators.as_of,
        source_close=indicators.source_close,
        candle_count=indicators.candle_count,
        # 两倍最长周期能明显降低首个 SMA 种子对 EMA 最新值的影响。
        warmup_complete=(
            indicators.candle_count >= max(ema_periods) * 2
            and all(metric.value is not None for metric in ema.values())
        ),
        trend=TrendIndicatorsRead(
            ema=ema,
            ema_alignment=_ema_alignment_for_periods(indicators.ema, ema_periods),
            adx=AdxIndicatorRead(
                value=indicators.adx14,
                plus_di=indicators.plus_di14,
                minus_di=indicators.minus_di14,
                slope=indicators.adx_slope,
            ),
        ),
        momentum=MomentumIndicatorsRead(
            rsi14=indicators.rsi14,
            macd=MacdIndicatorRead(
                line=indicators.macd.line,
                signal=indicators.macd.signal,
                histogram=indicators.macd.histogram,
            ),
        ),
        volatility=VolatilityIndicatorsRead(
            atr=AtrIndicatorRead(value=indicators.atr14, percent=indicators.atr14_percent),
            bollinger=BollingerIndicatorRead(
                upper=indicators.bollinger.upper,
                middle=indicators.bollinger.middle,
                lower=indicators.bollinger.lower,
                bandwidth_percent=indicators.bollinger.bandwidth_percent,
                percent_b=indicators.bollinger.percent_b,
            ),
        ),
        volume=VolumeIndicatorsRead(mfi14=indicators.mfi14, obv=indicators.obv),
    )
