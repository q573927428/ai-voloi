"""运行时 K 线数据库恢复与增量补齐测试。"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

import app.services.runtime as runtime_module
from app.schemas import KlineData
from app.services.cache.kline_cache import KlineCache
from app.services.runtime import (
    KLINE_INCREMENTAL_LIMIT,
    MonitorRuntime,
    build_kline_retention_statement,
    has_fresh_closed_history,
    update_active_pool_membership,
)


class FakeKlineClient:
    """记录 K 线请求参数并依次返回预设批次。"""

    def __init__(self, batches: list[list[KlineData]]):
        self.batches = list(batches)
        self.calls: list[tuple[str, str, int, int | None]] = []

    async def klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        start_ms: int | None = None,
    ) -> list[KlineData]:
        """返回下一批 K 线并保留请求游标供断言。"""
        self.calls.append((symbol, timeframe, limit, start_ms))
        return self.batches.pop(0)


class FakeTemporaryWebSocketManager:
    """记录临时 Binance WebSocket 的启动和停止，避免测试访问外部网络。"""

    instances: list["FakeTemporaryWebSocketManager"] = []

    def __init__(self, settings, on_kline):
        self.settings = settings
        self.on_kline = on_kline
        self.starts: list[tuple[set[str], list[str]]] = []
        self.stop_count = 0
        self.instances.append(self)

    async def start(self, symbols: set[str], timeframes: list[str]) -> None:
        """记录临时订阅市场。"""
        self.starts.append((symbols, timeframes))

    async def stop(self) -> None:
        """记录上游连接被释放。"""
        self.stop_count += 1


def make_kline(start: datetime, volume: str, closed: bool = True) -> KlineData:
    """构造固定 15 分钟周期的测试 K 线。"""
    return KlineData(
        symbol="BTCUSDT",
        timeframe="15m",
        open_time=start,
        close_time=start + timedelta(minutes=15),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal(volume),
        quote_volume=Decimal(volume) * 100,
        is_closed=closed,
    )


def make_runtime(stored: list[KlineData], batches: list[list[KlineData]]):
    """构造只包含初始化链路依赖的轻量运行时。"""
    runtime = object.__new__(MonitorRuntime)
    runtime.settings = SimpleNamespace(kline_history_limit=498)
    runtime.cache = KlineCache(498)
    runtime.client = FakeKlineClient(batches)
    persisted: list[list[KlineData]] = []

    async def load_stored(symbol: str, timeframe: str) -> list[KlineData]:
        """模拟数据库已有的完整 K 线。"""
        return stored

    async def persist(items: list[KlineData]) -> None:
        """记录初始化期间需要 upsert 的 Binance 数据。"""
        persisted.append(items)

    runtime._load_stored_klines = load_stored
    runtime._persist_closed_klines = persist
    return runtime, persisted


def test_active_pool_entry_time_tracks_current_membership_cycle() -> None:
    """持续活跃不得重置时间，退池后再次进入必须开始新的活跃周期。"""
    first_entry = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
    second_entry = first_entry + timedelta(hours=2)
    row = SimpleNamespace(is_active=False, active_since=None)

    update_active_pool_membership(row, True, first_entry)
    update_active_pool_membership(row, True, first_entry + timedelta(minutes=15))
    assert row.active_since == first_entry

    update_active_pool_membership(row, False, first_entry + timedelta(hours=1))
    assert row.active_since is None

    update_active_pool_membership(row, True, second_entry)
    assert row.active_since == second_entry


def current_timeframe_open(now: datetime, minutes: int) -> datetime:
    """按 UTC Unix 边界计算测试所需的当前周期开盘时间。"""
    seconds = minutes * 60
    timestamp = int(now.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(timestamp, timezone.utc)


def test_kline_retention_statement_limits_each_market_independently() -> None:
    """数据库淘汰必须按交易对和周期分别排名，且仅处理本批写入涉及的市场。"""
    statement = build_kline_retention_statement(
        {("BTCUSDT", "15m"), ("ETHUSDT", "1h")},
        498,
    )
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = str(compiled)

    assert "PARTITION BY klines.symbol, klines.timeframe" in sql
    assert "ORDER BY klines.open_time DESC, klines.id DESC" in sql
    assert "retention_rank > 498" in sql
    assert "('BTCUSDT', '15m')" in sql
    assert "('ETHUSDT', '1h')" in sql


@pytest.mark.asyncio
async def test_fresh_stored_history_skips_incremental_request() -> None:
    """数据库已覆盖至当前周期起点时，重启不得重复请求 Binance K 线。"""
    current_open = current_timeframe_open(datetime.now(timezone.utc), 15)
    stored = [make_kline(current_open - timedelta(minutes=15), "10")]
    runtime, persisted = make_runtime(stored, [])

    await runtime._initialize_symbol_timeframe("BTCUSDT", "15m")

    assert runtime.client.calls == []
    cached_current, cached_closed = await runtime.cache.snapshot("BTCUSDT", "15m")
    assert cached_closed == stored
    assert cached_current is None
    assert persisted == []


def test_unknown_timeframe_cannot_skip_incremental_request() -> None:
    """无法识别的周期必须保守执行增量校验。"""
    now = datetime.now(timezone.utc)

    assert not has_fresh_closed_history(now, "unsupported", now)


@pytest.mark.asyncio
async def test_existing_history_uses_small_incremental_request_and_repairs_overlap() -> None:
    """数据库有历史时应从末根开始少量补齐，并用 Binance 值修正重叠记录。"""
    current_open = current_timeframe_open(datetime.now(timezone.utc), 15)
    start = current_open - timedelta(minutes=45)
    stored = [make_kline(start, "10"), make_kline(start + timedelta(minutes=15), "20")]
    repaired = make_kline(start + timedelta(minutes=15), "21")
    current = make_kline(start + timedelta(minutes=30), "5", closed=False)
    runtime, persisted = make_runtime(stored, [[repaired, current]])

    await runtime._initialize_symbol_timeframe("BTCUSDT", "15m")

    expected_start = int(stored[-1].open_time.timestamp() * 1000)
    assert runtime.client.calls == [("BTCUSDT", "15m", KLINE_INCREMENTAL_LIMIT, expected_start)]
    cached_current, cached_closed = await runtime.cache.snapshot("BTCUSDT", "15m")
    assert cached_closed[-1].volume == Decimal("21")
    assert cached_current == current
    assert persisted == [[repaired, current]]


@pytest.mark.asyncio
async def test_missing_history_uses_full_initialization_request() -> None:
    """数据库没有对应市场周期时才允许请求完整历史窗口。"""
    start = datetime(2026, 8, 16, tzinfo=timezone.utc)
    first = make_kline(start, "10")
    current = make_kline(start + timedelta(minutes=15), "2", closed=False)
    runtime, persisted = make_runtime([], [[first, current]])

    await runtime._initialize_symbol_timeframe("BTCUSDT", "15m")

    assert runtime.client.calls == [("BTCUSDT", "15m", 498, None)]
    cached_current, cached_closed = await runtime.cache.snapshot("BTCUSDT", "15m")
    assert cached_closed == [first]
    assert cached_current == current
    assert persisted == [[first, current]]


@pytest.mark.asyncio
async def test_long_gap_expands_follow_up_request_until_current_kline() -> None:
    """停机缺口超过小批次时应扩大请求窗口并持续追赶到当前 K 线。"""
    start = datetime(2026, 8, 16, tzinfo=timezone.utc)
    stored = [make_kline(start, "10")]
    first_batch = [
        make_kline(start + timedelta(minutes=15 * index), str(10 + index))
        for index in range(KLINE_INCREMENTAL_LIMIT)
    ]
    current = make_kline(
        start + timedelta(minutes=15 * KLINE_INCREMENTAL_LIMIT),
        "2",
        closed=False,
    )
    runtime, _ = make_runtime(stored, [first_batch, [current]])

    await runtime._initialize_symbol_timeframe("BTCUSDT", "15m")

    first_start = int(stored[-1].open_time.timestamp() * 1000)
    follow_up_start = int(first_batch[-1].close_time.timestamp() * 1000)
    assert runtime.client.calls == [
        ("BTCUSDT", "15m", KLINE_INCREMENTAL_LIMIT, first_start),
        ("BTCUSDT", "15m", 498, follow_up_start),
    ]
    cached_current, cached_closed = await runtime.cache.snapshot("BTCUSDT", "15m")
    assert cached_closed[-1] == first_batch[-1]
    assert cached_current == current


@pytest.mark.asyncio
async def test_chart_repair_processes_overlap_closed_candle_and_current_in_order() -> None:
    """图表修复必须忽略更早历史，同时保留重叠修正、缺失完整 K 线和当前 K 线。"""
    start = datetime(2026, 8, 16, tzinfo=timezone.utc)
    stored = [make_kline(start, "10"), make_kline(start + timedelta(minutes=15), "20")]
    older = make_kline(start, "9")
    overlap = make_kline(start + timedelta(minutes=15), "21")
    missing = make_kline(start + timedelta(minutes=30), "30")
    current = make_kline(start + timedelta(minutes=45), "4", closed=False)
    runtime, _ = make_runtime(stored, [[current, missing, older, overlap]])
    await runtime.cache.initialize("BTCUSDT", "15m", stored)
    processed: list[KlineData] = []

    async def record(item: KlineData) -> None:
        """记录修复入口交给统一 K 线处理链路的数据。"""
        processed.append(item)

    runtime.on_kline = record

    await runtime.repair_market_klines("BTCUSDT", "15m", limit=20)

    assert processed == [overlap, missing, current]
    assert runtime.client.calls == [("BTCUSDT", "15m", 20, None)]


@pytest.mark.asyncio
async def test_inactive_chart_stream_is_shared_and_removed_after_last_viewer(monkeypatch) -> None:
    """非活跃市场只建立一条共享临时连接，最后一个查看者离开后完整释放。"""
    start = datetime(2026, 8, 18, tzinfo=timezone.utc)
    history = [make_kline(start, "10"), make_kline(start + timedelta(minutes=15), "2", False)]
    runtime, _ = make_runtime([], [history])
    runtime.active_symbols = {"ETHUSDT"}
    runtime.config = SimpleNamespace(timeframes=["15m"])
    runtime._chart_stream_lock = asyncio.Lock()
    runtime._chart_stream_subscribers = {}
    runtime._temporary_websockets = {}
    FakeTemporaryWebSocketManager.instances = []
    monkeypatch.setattr(runtime_module, "BinanceWebSocketManager", FakeTemporaryWebSocketManager)

    await runtime.open_chart_stream("BTCUSDT", "15m")
    await runtime.open_chart_stream("BTCUSDT", "15m")

    assert runtime._chart_stream_subscribers[("BTCUSDT", "15m")] == 2
    assert len(FakeTemporaryWebSocketManager.instances) == 1
    manager = FakeTemporaryWebSocketManager.instances[0]
    assert manager.starts == [({"BTCUSDT"}, ["15m"])]
    assert ("BTCUSDT", "15m") in await runtime.cache.market_keys()
    assert runtime.active_symbols == {"ETHUSDT"}

    await runtime.close_chart_stream("BTCUSDT", "15m")
    assert manager.stop_count == 0

    await runtime.close_chart_stream("BTCUSDT", "15m")
    assert manager.stop_count == 1
    assert ("BTCUSDT", "15m") not in await runtime.cache.market_keys()
    assert runtime.active_symbols == {"ETHUSDT"}
