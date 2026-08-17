"""按完整 K 线增量维护 Signal 扫描所需技术指标状态。"""

from collections import deque
from decimal import Decimal

from app.schemas import KlineData
from app.services.cache.technical_indicators import (
    DEFAULT_EMA_PERIODS,
    HUNDRED,
    BollingerIndicator,
    EmaIndicator,
    MacdIndicator,
    TechnicalIndicators,
)


class _IncrementalEma:
    """使用首周期 SMA 为种子的单周期增量 EMA。"""

    def __init__(self, period: int):
        self.period = period
        self.alpha = Decimal(2) / Decimal(period + 1)
        self.count = 0
        self.seed_sum = Decimal(0)
        self.value: Decimal | None = None
        self.previous: Decimal | None = None

    def update(self, value: Decimal) -> Decimal | None:
        """写入一个观察值并返回当前 EMA。"""
        self.count += 1
        if self.count <= self.period:
            self.seed_sum += value
            if self.count == self.period:
                self.value = self.seed_sum / Decimal(self.period)
            return self.value
        self.previous = self.value
        if self.value is None:
            return None
        self.value = value * self.alpha + self.value * (Decimal(1) - self.alpha)
        return self.value

    def slope_percent(self) -> Decimal | None:
        """返回最近两个 EMA 观察点之间的百分比变化。"""
        if self.value is None or self.previous is None:
            return None
        return (
            (self.value - self.previous) / abs(self.previous) * HUNDRED
            if self.previous else Decimal(0)
        )


class IncrementalTechnicalIndicators:
    """为单个交易对周期维护固定指标集的增量计算状态。

    正常新增及环形窗口淘汰时只更新常数规模状态；仅历史覆盖修正需要由缓存层
    使用当前窗口重建。递归指标跨窗口持续平滑，OBV、布林带和 MFI 保持滚动口径。
    """

    def __init__(self):
        self.count = 0
        self.last_kline: KlineData | None = None
        self._ema = {period: _IncrementalEma(period) for period in DEFAULT_EMA_PERIODS}
        self._rsi_change_count = 0
        self._gain_sum = Decimal(0)
        self._loss_sum = Decimal(0)
        self._average_gain: Decimal | None = None
        self._average_loss: Decimal | None = None
        self._movement_count = 0
        self._tr_sum = Decimal(0)
        self._positive_dm_sum = Decimal(0)
        self._negative_dm_sum = Decimal(0)
        self._atr: Decimal | None = None
        self._smoothed_positive: Decimal | None = None
        self._smoothed_negative: Decimal | None = None
        self._plus_di: Decimal | None = None
        self._minus_di: Decimal | None = None
        self._dx_count = 0
        self._dx_sum = Decimal(0)
        self._adx: Decimal | None = None
        self._previous_adx: Decimal | None = None
        self._macd_fast = _IncrementalEma(12)
        self._macd_slow = _IncrementalEma(26)
        self._macd_signal = _IncrementalEma(9)
        self._macd_line: Decimal | None = None
        self._bollinger_window: deque[Decimal] = deque(maxlen=20)
        self._previous_typical: Decimal | None = None
        self._money_flows: deque[tuple[Decimal, Decimal]] = deque(maxlen=14)
        self._obv = Decimal(0)

    @classmethod
    def from_klines(cls, klines: list[KlineData]) -> "IncrementalTechnicalIndicators":
        """按时间顺序从完整历史重建指标状态。"""
        state = cls()
        complete = (item for item in klines if item.is_closed)
        for kline in sorted(complete, key=lambda item: item.open_time):
            state.update(kline)
        return state

    def update(self, kline: KlineData) -> None:
        """写入一根新的完整 K 线并更新全部固定指标。"""
        if not kline.is_closed:
            return
        previous = self.last_kline
        self.count += 1
        for ema in self._ema.values():
            ema.update(kline.close)
        self._update_rsi(previous, kline)
        self._update_atr_adx(previous, kline)
        self._update_macd(kline.close)
        self._bollinger_window.append(kline.close)
        self._update_money_flow(kline)
        if previous is not None:
            if kline.close > previous.close:
                self._obv += kline.volume
            elif kline.close < previous.close:
                self._obv -= kline.volume
        self.last_kline = kline

    def discard_oldest_obv_transition(
        self, oldest: KlineData, next_oldest: KlineData
    ) -> None:
        """环形窗口淘汰首根时移除它到下一根对窗口 OBV 的贡献。"""
        if next_oldest.close > oldest.close:
            self._obv -= next_oldest.volume
        elif next_oldest.close < oldest.close:
            self._obv += next_oldest.volume

    def _update_rsi(self, previous: KlineData | None, current: KlineData) -> None:
        """维护 Wilder RSI14 的平均涨跌幅。"""
        if previous is None:
            return
        change = current.close - previous.close
        gain = max(change, Decimal(0))
        loss = max(-change, Decimal(0))
        self._rsi_change_count += 1
        if self._rsi_change_count <= 14:
            self._gain_sum += gain
            self._loss_sum += loss
            if self._rsi_change_count == 14:
                self._average_gain = self._gain_sum / Decimal(14)
                self._average_loss = self._loss_sum / Decimal(14)
            return
        if self._average_gain is None or self._average_loss is None:
            return
        self._average_gain = (self._average_gain * Decimal(13) + gain) / Decimal(14)
        self._average_loss = (self._average_loss * Decimal(13) + loss) / Decimal(14)

    def _update_atr_adx(self, previous: KlineData | None, current: KlineData) -> None:
        """维护 Wilder ATR、方向分量、DX 和 ADX14。"""
        if previous is None:
            return
        true_range = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        upward = current.high - previous.high
        downward = previous.low - current.low
        positive_dm = upward if upward > downward and upward > 0 else Decimal(0)
        negative_dm = downward if downward > upward and downward > 0 else Decimal(0)
        self._movement_count += 1
        if self._movement_count <= 14:
            self._tr_sum += true_range
            self._positive_dm_sum += positive_dm
            self._negative_dm_sum += negative_dm
            if self._movement_count < 14:
                return
            self._atr = self._tr_sum / Decimal(14)
            self._smoothed_positive = self._positive_dm_sum / Decimal(14)
            self._smoothed_negative = self._negative_dm_sum / Decimal(14)
        else:
            if (
                self._atr is None
                or self._smoothed_positive is None
                or self._smoothed_negative is None
            ):
                return
            self._atr = (self._atr * Decimal(13) + true_range) / Decimal(14)
            self._smoothed_positive = (
                self._smoothed_positive * Decimal(13) + positive_dm
            ) / Decimal(14)
            self._smoothed_negative = (
                self._smoothed_negative * Decimal(13) + negative_dm
            ) / Decimal(14)
        self._update_dx()

    def _update_dx(self) -> None:
        """根据当前方向平滑值推进 DX 和 ADX 状态。"""
        if self._atr is None or self._smoothed_positive is None or self._smoothed_negative is None:
            return
        if self._atr == 0:
            self._plus_di = Decimal(0)
            self._minus_di = Decimal(0)
            dx = Decimal(0)
        else:
            self._plus_di = HUNDRED * self._smoothed_positive / self._atr
            self._minus_di = HUNDRED * self._smoothed_negative / self._atr
            denominator = self._plus_di + self._minus_di
            dx = (
                HUNDRED * abs(self._plus_di - self._minus_di) / denominator
                if denominator else Decimal(0)
            )
        self._dx_count += 1
        if self._dx_count <= 14:
            self._dx_sum += dx
            if self._dx_count == 14:
                self._adx = self._dx_sum / Decimal(14)
            return
        self._previous_adx = self._adx
        if self._adx is not None:
            self._adx = (self._adx * Decimal(13) + dx) / Decimal(14)

    def _update_macd(self, close: Decimal) -> None:
        """维护 MACD(12, 26, 9) 的快慢线和信号线。"""
        fast = self._macd_fast.update(close)
        slow = self._macd_slow.update(close)
        if fast is None or slow is None:
            return
        self._macd_line = fast - slow
        self._macd_signal.update(self._macd_line)

    def _update_money_flow(self, kline: KlineData) -> None:
        """维护 MFI14 所需的正负原始资金流窗口。"""
        typical = (kline.high + kline.low + kline.close) / Decimal(3)
        if self._previous_typical is not None:
            raw_flow = typical * kline.volume
            positive = raw_flow if typical > self._previous_typical else Decimal(0)
            negative = raw_flow if typical < self._previous_typical else Decimal(0)
            self._money_flows.append((positive, negative))
        self._previous_typical = typical

    def _rsi(self) -> Decimal | None:
        """根据当前 Wilder 平滑状态返回 RSI14。"""
        if self._average_gain is None or self._average_loss is None:
            return None
        if self._average_loss == 0:
            return HUNDRED if self._average_gain > 0 else Decimal("50")
        relative_strength = self._average_gain / self._average_loss
        return HUNDRED - HUNDRED / (Decimal(1) + relative_strength)

    def _bollinger(self) -> BollingerIndicator:
        """根据最近 20 根收盘价返回布林带。"""
        if len(self._bollinger_window) < 20:
            return BollingerIndicator(None, None, None, None, None)
        middle = sum(self._bollinger_window, Decimal(0)) / Decimal(20)
        variance = sum(
            ((value - middle) ** 2 for value in self._bollinger_window), Decimal(0)
        ) / Decimal(20)
        deviation = variance.sqrt()
        upper = middle + Decimal(2) * deviation
        lower = middle - Decimal(2) * deviation
        width = upper - lower
        bandwidth = width / abs(middle) * HUNDRED if middle else Decimal(0)
        latest = self.last_kline.close if self.last_kline else Decimal(0)
        percent_b = (latest - lower) / width if width else Decimal("0.5")
        return BollingerIndicator(upper, middle, lower, bandwidth, percent_b)

    def _mfi(self) -> Decimal | None:
        """根据最近 14 个资金流方向返回 MFI14。"""
        if len(self._money_flows) < 14:
            return None
        positive = sum((item[0] for item in self._money_flows), Decimal(0))
        negative = sum((item[1] for item in self._money_flows), Decimal(0))
        if negative == 0:
            return HUNDRED if positive > 0 else Decimal("50")
        ratio = positive / negative
        return HUNDRED - HUNDRED / (Decimal(1) + ratio)

    def result(self, candle_count: int | None = None) -> TechnicalIndicators | None:
        """生成与批量计算器同结构的最新不可变指标快照。"""
        if self.last_kline is None:
            return None
        ema = {
            period: EmaIndicator(
                period=period,
                value=state.value,
                slope_percent=state.slope_percent(),
                distance_percent=(
                    (self.last_kline.close - state.value) / abs(state.value) * HUNDRED
                    if state.value else (Decimal(0) if state.value == 0 else None)
                ),
            )
            for period, state in self._ema.items()
        }
        if ema[14].slope_percent is None or ema[50].slope_percent is None:
            return None
        if self._adx is None or self._previous_adx is None or self._atr is None:
            return None
        macd_signal = self._macd_signal.value
        macd = MacdIndicator(
            line=self._macd_line,
            signal=macd_signal,
            histogram=(
                self._macd_line - macd_signal
                if self._macd_line is not None and macd_signal is not None else None
            ),
        )
        comparable = [ema[period].value for period in DEFAULT_EMA_PERIODS]
        if any(value is None for value in comparable):
            alignment = "insufficient_data"
        else:
            values = [value for value in comparable if value is not None]
            if all(left > right for left, right in zip(values, values[1:])):
                alignment = "bullish"
            elif all(left < right for left, right in zip(values, values[1:])):
                alignment = "bearish"
            else:
                alignment = "mixed"
        return TechnicalIndicators(
            source_close=self.last_kline.close,
            as_of=self.last_kline.close_time,
            candle_count=candle_count if candle_count is not None else self.count,
            ema=ema,
            ema_alignment=alignment,
            rsi14=self._rsi(),
            adx14=self._adx,
            plus_di14=self._plus_di,
            minus_di14=self._minus_di,
            atr14=self._atr,
            atr14_percent=(
                self._atr / abs(self.last_kline.close) * HUNDRED
                if self.last_kline.close else Decimal(0)
            ),
            adx_slope=self._adx - self._previous_adx,
            macd=macd,
            bollinger=self._bollinger(),
            mfi14=self._mfi(),
            obv=self._obv,
        )
