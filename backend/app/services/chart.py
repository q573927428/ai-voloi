"""TradingView 图表 K 线与 EMA 数据组装服务。"""

from collections.abc import Sequence
from typing import Any

from app.schemas import SignalChartCandle
from app.services.cache.technical_indicators import ema_series


def build_chart_candles(klines: Sequence[Any]) -> list[SignalChartCandle]:
    """按时间排序 K 线并计算逐根 EMA14、EMA50，供历史快照和实时推送共用。"""
    ordered = sorted(klines, key=lambda item: item.open_time)
    closes = [item.close for item in ordered]
    ema14_values = ema_series(closes, 14)
    ema50_values = ema_series(closes, 50)
    candles: list[SignalChartCandle] = []
    for index, item in enumerate(ordered):
        ema14_index = index - 13
        ema50_index = index - 49
        candles.append(SignalChartCandle(
            time=int(item.open_time.timestamp()),
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.volume,
            ema14=ema14_values[ema14_index] if ema14_index >= 0 else None,
            ema50=ema50_values[ema50_index] if ema50_index >= 0 else None,
        ))
    return candles
