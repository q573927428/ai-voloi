"""Scanner 成交量先筛选、OI 后查询的业务链路单元测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.schemas import ConfigValues, KlineData, OIPoint, TickerData
from app.services.cache.kline_cache import KlineCache
from app.services.scanner.scanner import OI_HISTORY_PERIOD, Scanner


class FakeBinanceClient:
    """记录 OI 请求并返回稳定增长序列的测试客户端。"""

    def __init__(self, start: datetime):
        self.start = start
        self.calls: list[tuple[str, str, int]] = []

    async def open_interest(self, symbol: str, period: str, start_ms: int):
        """模拟 5 分钟粒度，返回当前 K 线形成期间的两个 OI 点。"""
        self.calls.append((symbol, period, start_ms))
        return [
            OIPoint(timestamp=self.start, open_interest=Decimal("1000")),
            OIPoint(timestamp=self.start + timedelta(minutes=5), open_interest=Decimal("1001")),
        ]


class FakeSession:
    """提供 Scanner 所需最小异步会话协议，并记录待持久化对象。"""

    def __init__(self):
        self.rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def add_all(self, rows) -> None:
        self.rows.extend(rows)

    def add(self, row) -> None:
        self.rows.append(row)

    async def commit(self) -> None:
        return None

    async def refresh(self, row) -> None:
        # SQLAlchemy 的 UUID default 通常在 flush 时填充，测试会话在此模拟该行为。
        if row.id is None:
            row.id = uuid4()


class FakeSessionFactory:
    """每次调用返回同一测试会话，便于断言持久化结果。"""

    def __init__(self):
        self.session = FakeSession()

    def __call__(self):
        return self.session


def make_kline(
    start: datetime,
    volume: str,
    closed: bool,
    timeframe: str = "15m",
    duration_minutes: int = 15,
) -> KlineData:
    """构造指定周期的当前或完整 K 线。"""
    return KlineData(
        symbol="BTCUSDT", timeframe=timeframe, open_time=start,
        close_time=start + timedelta(minutes=duration_minutes), open=100, high=102, low=99, close=101,
        volume=volume, quote_volume=Decimal(volume) * 100, is_closed=closed,
    )


@pytest.mark.asyncio
async def test_oi_is_not_requested_when_volume_does_not_pass() -> None:
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=7)
    cache = KlineCache()
    history = [make_kline(start - timedelta(minutes=15 * index), "100", True) for index in range(60, 0, -1)]
    await cache.initialize("BTCUSDT", "15m", history + [make_kline(start, "10", False)])
    client = FakeBinanceClient(start)
    scanner = Scanner(cache, client, FakeSessionFactory(), lambda signal: None)

    run = await scanner.scan({"BTCUSDT"}, {}, ConfigValues(timeframes=["15m"]))

    assert client.calls == []
    assert run.candidate_count == 0
    assert run.signal_count == 0


@pytest.mark.asyncio
async def test_expired_current_kline_cannot_generate_signal() -> None:
    """缓存滞后时，即使旧 K 线仍标记为未收盘，也不能按 100% 进度参与扫描。"""
    now = datetime.now(timezone.utc)
    stale_start = now - timedelta(minutes=20)
    cache = KlineCache()
    history = [
        make_kline(stale_start - timedelta(minutes=15 * index), "100", True)
        for index in range(60, 0, -1)
    ]
    # 模拟 WebSocket 停滞：缓存对象还是 current，但它的时间区间实际已经结束。
    await cache.initialize(
        "BTCUSDT",
        "15m",
        history + [make_kline(stale_start, "1000", False)],
    )
    client = FakeBinanceClient(stale_start)
    scanner = Scanner(cache, client, FakeSessionFactory(), lambda signal: None)
    tickers = {
        "BTCUSDT": TickerData(
            symbol="BTCUSDT",
            last_price=101,
            price_change_percent=2,
            quote_volume=20_000_000,
        )
    }

    run = await scanner.scan(
        {"BTCUSDT"},
        tickers,
        ConfigValues(timeframes=["15m"]),
    )

    assert client.calls == []
    assert run.candidate_count == 0
    assert run.signal_count == 0


@pytest.mark.asyncio
async def test_signal_requires_both_volume_and_oi_and_is_published() -> None:
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    cache = KlineCache()
    history = [
        make_kline(
            start - timedelta(minutes=30 * index),
            "100",
            True,
            timeframe="30m",
            duration_minutes=30,
        )
        for index in range(60, 0, -1)
    ]
    await cache.initialize(
        "BTCUSDT",
        "30m",
        history + [
            make_kline(start, "200", False, timeframe="30m", duration_minutes=30)
        ],
    )
    client = FakeBinanceClient(start)
    published = []

    async def publish(signal) -> None:
        """记录 Scanner 推送的 Signal。"""
        published.append(signal)

    scanner = Scanner(cache, client, FakeSessionFactory(), publish)
    tickers = {"BTCUSDT": TickerData(symbol="BTCUSDT", last_price=101, price_change_percent=2, quote_volume=20_000_000)}

    run = await scanner.scan({"BTCUSDT"}, tickers, ConfigValues(timeframes=["30m"]))

    assert client.calls == [
        ("BTCUSDT", OI_HISTORY_PERIOD, int(start.timestamp() * 1000))
    ]
    assert run.candidate_count == 1
    assert run.signal_count == 1
    assert published[0].signal_type == "VOLUME_OI_ANOMALY"
    assert published[0].ema14 is not None
    assert published[0].ema50 is not None
    assert published[0].rsi14 is not None
    assert published[0].adx14 is not None
    assert published[0].atr14 is not None
