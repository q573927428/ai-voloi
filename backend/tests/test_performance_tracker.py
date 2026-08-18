"""Signal 未来表现历史价格回填测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.schemas import KlineData
from app.services.performance.tracker import HORIZONS, PerformanceTracker


class FakeHistoricalKlineClient:
    """按目标分钟生成不同收盘价，并记录历史 K 线请求参数。"""

    def __init__(self, closes: list[Decimal] | None = None) -> None:
        self.calls: list[tuple[str, str, int, int | None]] = []
        self.observation_calls: list[int] = []
        self.closes = closes or [
            Decimal("101"), Decimal("99"), Decimal("103"), Decimal("96"),
            Decimal("105"), Decimal("94"), Decimal("107"), Decimal("92"),
            Decimal("109"), Decimal("90"),
        ]

    async def klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[KlineData]:
        """返回覆盖请求目标分钟的完整 1m K 线。"""
        self.calls.append((symbol, timeframe, limit, end_ms))
        assert start_ms is None
        assert end_ms is not None
        self.observation_calls.append(end_ms)
        open_time = (
            datetime.fromtimestamp((end_ms + 1) / 1000, timezone.utc)
            - timedelta(minutes=1)
        )
        close = self.closes[len(self.observation_calls) - 1]
        return [
            KlineData(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal("1"),
                quote_volume=close,
                is_closed=True,
            )
        ]


class FakeResult:
    """提供 PerformanceTracker 所需的查询结果接口。"""

    def __init__(self, rows: list[tuple[object, object]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[object, object]]:
        """返回预设 Signal 与未来表现记录。"""
        return self.rows


class FakeSession:
    """模拟异步数据库会话并记录事务提交。"""

    def __init__(self, rows: list[tuple[object, object]]) -> None:
        self.rows = rows
        self.committed = False

    async def __aenter__(self):
        """进入异步会话上下文。"""
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """退出异步会话上下文。"""

    async def execute(self, statement) -> FakeResult:
        """忽略 SQL 细节并返回预设记录。"""
        return FakeResult(self.rows)

    async def commit(self) -> None:
        """标记未来表现已提交。"""
        self.committed = True


@pytest.mark.asyncio
async def test_delayed_update_uses_each_horizons_historical_minute() -> None:
    """服务延迟恢复时，每个到期周期仍应使用自己的目标分钟，而非共享实时价格。"""
    detected_at = datetime(2026, 8, 10, 1, 2, 30, tzinfo=timezone.utc)
    now = detected_at + timedelta(days=3)
    signal = SimpleNamespace(
        symbol="BTCUSDT",
        detected_at=detected_at,
        current_price=Decimal("100"),
    )
    performance = SimpleNamespace(
        **{field: None for field in HORIZONS},
        max_rise_percent=None,
        max_drop_percent=None,
    )
    session = FakeSession([(signal, performance)])
    client = FakeHistoricalKlineClient()
    tracker = PerformanceTracker(client, lambda: session)

    await tracker.update(now)

    assert session.committed
    assert [getattr(performance, field) for field in HORIZONS] == [
        Decimal(value) for value in (1, -1, 3, -4, 5, -6, 7, -8, 9, -10)
    ]
    # 最大涨跌幅来自固定观察点，正负号直接表达价格变化方向。
    assert performance.max_rise_percent == Decimal("9")
    assert performance.max_drop_percent == Decimal("-10")
    assert client.observation_calls == [
        int(
            (
                (detected_at + timedelta(minutes=minutes)).replace(second=0)
                + timedelta(minutes=1)
            ).timestamp()
            * 1000
        )
        - 1
        for minutes in HORIZONS.values()
    ]


@pytest.mark.parametrize(
    ("closes", "expected_maximum", "expected_minimum"),
    [
        ([Decimal(value) for value in range(101, 111)], Decimal("10"), Decimal("1")),
        ([Decimal(value) for value in range(99, 89, -1)], Decimal("-1"), Decimal("-10")),
    ],
)
@pytest.mark.asyncio
async def test_extremes_are_strict_maximum_and_minimum(
    closes: list[Decimal],
    expected_maximum: Decimal,
    expected_minimum: Decimal,
) -> None:
    """全涨或全跌时也只取观察点最大值和最小值，不按方向截断为零。"""
    detected_at = datetime(2026, 8, 10, 1, 2, 30, tzinfo=timezone.utc)
    signal = SimpleNamespace(
        symbol="BTCUSDT",
        detected_at=detected_at,
        current_price=Decimal("100"),
    )
    performance = SimpleNamespace(
        **{field: None for field in HORIZONS},
        max_rise_percent=None,
        max_drop_percent=None,
    )
    session = FakeSession([(signal, performance)])
    tracker = PerformanceTracker(
        FakeHistoricalKlineClient(closes),
        lambda: session,
    )

    await tracker.update(detected_at + timedelta(days=3))

    assert performance.max_rise_percent == expected_maximum
    assert performance.max_drop_percent == expected_minimum


def test_horizons_include_added_hour_and_two_day_points() -> None:
    """未来表现应包含新增的 8h、12h、16h 和 2d 观察点。"""
    assert HORIZONS["price_change_8h_percent"] == 480
    assert HORIZONS["price_change_12h_percent"] == 720
    assert HORIZONS["price_change_16h_percent"] == 960
    assert HORIZONS["price_change_2d_percent"] == 2880
