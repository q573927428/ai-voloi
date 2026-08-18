"""TradingView 图表 K 线与 EMA 数据组装服务。"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.schemas import SignalChartCandle
from app.services.cache.technical_indicators import DEFAULT_EMA_PERIODS, ema_series


def latest_ema_values(
    closes: list[Decimal],
    periods: tuple[int, ...] = DEFAULT_EMA_PERIODS,
) -> dict[int, Decimal | None]:
    """计算各周期在最后一个收盘价处的 EMA，数据不足的周期返回 None。"""
    return {
        period: values[-1] if (values := ema_series(closes, period)) else None
        for period in periods
    }


def build_chart_candles(klines: Sequence[Any]) -> list[SignalChartCandle]:
    """按时间排序 K 线并计算六组逐根 EMA，供历史快照和实时推送共用。"""
    ordered = sorted(klines, key=lambda item: item.open_time)
    closes = [item.close for item in ordered]
    ema_values = {period: ema_series(closes, period) for period in DEFAULT_EMA_PERIODS}
    candles: list[SignalChartCandle] = []
    for index, item in enumerate(ordered):
        candles.append(SignalChartCandle(
            time=int(item.open_time.timestamp()),
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.volume,
            quote_volume=item.quote_volume,
            emas={
                period: ema_values[period][index - period + 1]
                if index >= period - 1 else None
                for period in DEFAULT_EMA_PERIODS
            },
        ))
    return candles
