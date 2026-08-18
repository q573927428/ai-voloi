"""实时图表 EMA 组装与分组广播测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from app.api.routes import kline_stream, realtime_chart
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


class FakeChartStreamRuntime:
    """记录前端图表连接生命周期，不要求交易对存在于 ticker 快照。"""

    def __init__(self):
        self.config = SimpleNamespace(timeframes=["15m"])
        self.opened: list[tuple[str, str]] = []
        self.closed: list[tuple[str, str]] = []

    async def open_chart_stream(self, symbol: str, timeframe: str) -> None:
        """记录临时行情被打开。"""
        self.opened.append((symbol, timeframe))

    async def close_chart_stream(self, symbol: str, timeframe: str) -> None:
        """记录临时行情被释放。"""
        self.closed.append((symbol, timeframe))


class FakeChartClientWebSocket(FakeWebSocket):
    """模拟建立后立即离开的前端图表 WebSocket。"""

    def __init__(self, runtime: FakeChartStreamRuntime, broadcaster: KlineBroadcaster):
        super().__init__()
        self.app = SimpleNamespace(state=SimpleNamespace(
            runtime=runtime,
            kline_broadcaster=broadcaster,
        ))
        self.close_events: list[tuple[int, str]] = []

    async def receive_text(self) -> str:
        """用标准断开异常结束路由接收循环。"""
        raise WebSocketDisconnect()

    async def close(self, code: int, reason: str) -> None:
        """记录策略拒绝，正常路径不应调用。"""
        self.close_events.append((code, reason))


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


class FakeOnDemandKlineClient:
    """记录非活跃市场按需 REST 请求，不提供任何运行时订阅能力。"""

    def __init__(self, klines: list[KlineData]):
        self.klines_result = klines
        self.requests: list[tuple[str, str, int]] = []

    async def klines(self, symbol: str, timeframe: str, limit: int) -> list[KlineData]:
        """返回预设窗口并记录请求参数。"""
        self.requests.append((symbol, timeframe, limit))
        return self.klines_result


@pytest.mark.asyncio
async def test_realtime_chart_uses_rest_window_for_inactive_market() -> None:
    """非活跃交易对应直接返回 REST 窗口，且不得临时加入全局活跃池。"""
    client = FakeOnDemandKlineClient(make_chart_klines(220))
    runtime = SimpleNamespace(
        active_symbols={"ETHUSDT"},
        config=SimpleNamespace(timeframes=["15m"]),
        client=client,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=runtime)))

    response = await realtime_chart(
        symbol="btcusdt",
        timeframe="15m",
        request=request,
        history_limit=500,
        session=None,
    )

    assert len(response.candles) == 220
    assert client.requests == [("BTCUSDT", "15m", 500)]
    assert runtime.active_symbols == {"ETHUSDT"}


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


@pytest.mark.asyncio
async def test_kline_stream_allows_symbol_outside_ticker_snapshot() -> None:
    """非活跃交易对由 REST 初始化确认有效，不得因 ticker 快照缺失而提前拒绝。"""
    runtime = FakeChartStreamRuntime()
    broadcaster = KlineBroadcaster()
    websocket = FakeChartClientWebSocket(runtime, broadcaster)

    await kline_stream(websocket, "btcusdt", "15m")

    assert websocket.accepted is True
    assert websocket.close_events == []
    assert runtime.opened == [("BTCUSDT", "15m")]
    assert runtime.closed == [("BTCUSDT", "15m")]
