"""Scanner 成交量先筛选、OI 后查询的业务链路单元测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import Signal
from app.schemas import ConfigValues, FundingRateData, KlineData, OIPoint, TickerData
from app.services.cache.kline_cache import KlineCache
from app.services.scanner.scanner import Scanner


class FakeBinanceClient:
    """记录 OI 请求并返回稳定增长序列的测试客户端。"""

    def __init__(self, start: datetime):
        self.start = start
        self.history_calls: list[tuple[str, str, int]] = []
        self.current_calls: list[str] = []

    async def open_interest(self, symbol: str, period: str, start_ms: int):
        """模拟 5 分钟粒度，返回当前 K 线形成期间的两个 OI 点。"""
        self.history_calls.append((symbol, period, start_ms))
        return [
            OIPoint(timestamp=self.start, open_interest=Decimal("1000")),
            OIPoint(timestamp=self.start + timedelta(minutes=5), open_interest=Decimal("1001")),
        ]

    async def current_open_interest(self, symbol: str) -> OIPoint:
        """模拟检测时刻的实时 OI，数值应晚于历史接口最新采样。"""
        self.current_calls.append(symbol)
        return OIPoint(
            timestamp=self.start + timedelta(minutes=10),
            open_interest=Decimal("1002"),
        )


class FakeSession:
    """提供 Scanner 所需最小异步会话协议，并记录待持久化对象。"""

    def __init__(self):
        self.rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement):
        """返回已持久化 Signal 的 K 线身份，模拟扫描器的批量去重查询。"""
        identities = [
            (row.symbol, row.timeframe, row.open_time)
            for row in self.rows
            if isinstance(row, Signal)
        ]
        return FakeResult(identities)

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


class FakeResult:
    """提供 SQLAlchemy Result 在去重查询中使用的最小接口。"""

    def __init__(self, rows):
        self.rows = rows

    def all(self):
        """返回查询结果行。"""
        return self.rows


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
        volume=volume, quote_volume=Decimal(volume) * 100,
        taker_buy_quote_volume=Decimal(volume) * 60, is_closed=closed,
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

    assert client.history_calls == []
    assert client.current_calls == []
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

    assert client.history_calls == []
    assert client.current_calls == []
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

    funding_rates = {
        "BTCUSDT": FundingRateData(symbol="BTCUSDT", funding_rate=Decimal("0.0001"))
    }
    active_pool_entered_at = start - timedelta(hours=2)
    run = await scanner.scan(
        {"BTCUSDT"},
        tickers,
        ConfigValues(timeframes=["30m"]),
        funding_rates,
        {"BTCUSDT": active_pool_entered_at},
    )

    assert len(client.history_calls) == 1
    symbol, period, requested_start_ms = client.history_calls[0]
    assert (symbol, period) == ("BTCUSDT", "5m")
    assert client.current_calls == ["BTCUSDT"]
    # 30m K 线默认独立回看 30 分钟，不再复用全局 15 分钟窗口。
    requested_start = datetime.fromtimestamp(requested_start_ms / 1000, timezone.utc)
    assert now - timedelta(minutes=31) < requested_start < now - timedelta(minutes=29)
    assert run.candidate_count == 1
    assert run.oi_request_count == 2
    assert run.signal_count == 1
    assert published[0].signal_type == "VOLUME_OI_ANOMALY"
    assert published[0].ema14 is not None
    assert published[0].ema50 is not None
    assert published[0].rsi14 is not None
    assert published[0].adx14 is not None
    assert published[0].atr14 is not None
    assert published[0].technical_indicators["trend"]["ema"]["9"]["value"] is not None
    assert published[0].technical_indicators["momentum"]["macd"]["line"] is not None
    assert published[0].oi_lookback_minutes == 30
    assert published[0].fund_flow_snapshot["net_taker_flow"] == "4000"
    assert published[0].fund_flow_snapshot["taker_buy_ratio_percent"] == "60.0"
    assert published[0].fund_flow_snapshot["regime"] == "new_longs"
    assert published[0].fund_flow_snapshot["version"] == "1.1"
    assert published[0].funding_rate == Decimal("0.0001")
    assert published[0].active_pool_entered_at == active_pool_entered_at
    assert published[0].newest_oi == Decimal("1002")
    assert published[0].newest_timestamp == start + timedelta(minutes=10)


@pytest.mark.asyncio
async def test_same_kline_only_generates_one_signal() -> None:
    """条件持续成立时，同一交易对、周期和开盘时间不能重复生成 Signal。"""
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    cache = KlineCache()
    history = [
        make_kline(start - timedelta(minutes=30 * index), "100", True, "30m", 30)
        for index in range(60, 0, -1)
    ]
    await cache.initialize(
        "BTCUSDT",
        "30m",
        history + [make_kline(start, "200", False, "30m", 30)],
    )
    client = FakeBinanceClient(start)
    session_factory = FakeSessionFactory()

    async def publish(signal) -> None:
        """测试中无需处理推送。"""

    scanner = Scanner(cache, client, session_factory, publish)
    tickers = {
        "BTCUSDT": TickerData(
            symbol="BTCUSDT", last_price=101, price_change_percent=2, quote_volume=20_000_000
        )
    }

    first = await scanner.scan({"BTCUSDT"}, tickers, ConfigValues(timeframes=["30m"]))
    second = await scanner.scan({"BTCUSDT"}, tickers, ConfigValues(timeframes=["30m"]))

    assert first.signal_count == 1
    assert second.candidate_count == 1
    assert second.oi_request_count == 0
    assert second.signal_count == 0
    assert len(client.history_calls) == 1
    assert client.current_calls == ["BTCUSDT"]
