"""实时图表 EMA 组装与分组广播测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.schemas import KlineData
from app.services.broadcast import KlineBroadcaster
from app.services.chart import build_chart_candles


class FakeWebSocket:
    """记录广播器接受状态和发送消息的最小 WebSocket 替身。"""

    def __init__(self, fail: bool = False):
        self.accepted = False
        self.fail = fail
        self.messages: list[dict] = []

    async def accept(self) -> None:
        """记录连接已被接受。"""
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        """记录消息；失败连接用于验证自动清理。"""
        if self.fail:
            raise RuntimeError("disconnected")
        self.messages.append(message)


def make_chart_klines(count: int = 60) -> list[KlineData]:
    """构造稳定上涨的完整 K 线序列。"""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        KlineData(
            symbol="BTCUSDT",
            timeframe="15m",
            open_time=start + timedelta(minutes=index * 15),
            close_time=start + timedelta(minutes=(index + 1) * 15),
            open=Decimal(100 + index),
            high=Decimal(102 + index),
            low=Decimal(99 + index),
            close=Decimal(101 + index),
            volume=Decimal(1000 + index),
            quote_volume=Decimal(100_000 + index),
            is_closed=True,
        )
        for index in range(count)
    ]


def test_build_chart_candles_contains_all_selectable_ema_values() -> None:
    candles = build_chart_candles(list(reversed(make_chart_klines(220))))

    assert len(candles) == 220
    for period in (9, 14, 21, 50, 100, 200):
        assert period in candles[-1].emas
        assert candles[period - 2].emas[period] is None
        assert candles[period - 1].emas[period] is not None
    assert candles[-1].quote_volume == Decimal(100_219)
    assert candles[-1].time > candles[0].time


@pytest.mark.asyncio
async def test_kline_broadcaster_only_publishes_to_matching_market() -> None:
    broadcaster = KlineBroadcaster()
    matching = FakeWebSocket()
    other = FakeWebSocket()
    candle = build_chart_candles(make_chart_klines(220))[-1]
    await broadcaster.connect(matching, "BTCUSDT", "15m")
    await broadcaster.connect(other, "ETHUSDT", "15m")

    await broadcaster.publish("BTCUSDT", "15m", candle)

    assert matching.accepted is True
    assert matching.messages[0]["type"] == "kline"
    assert all(
        matching.messages[0]["data"]["emas"][str(period)] is not None
        for period in (9, 14, 21, 50, 100, 200)
    )
    assert other.messages == []
