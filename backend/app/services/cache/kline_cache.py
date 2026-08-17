"""并发安全的列式 K 线环形缓存与扫描快照服务。"""

import asyncio
from array import array
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from app.schemas import KlineData
from app.services.cache.incremental_indicators import IncrementalTechnicalIndicators
from app.services.cache.technical_indicators import TechnicalIndicators


class ColumnarKlineRing:
    """单市场固定容量列式 Float64 环形缓存。

    时间使用微秒 Unix int64，OHLCV 使用 Float64；交易对和周期只在桶级保存一次。
    正常收盘和窗口淘汰常数时间推进指标；只有覆盖历史时才重建指标状态。
    """

    _FLOAT_COLUMN_COUNT = 6
    _INT_COLUMN_COUNT = 2

    def __init__(self, symbol: str, timeframe: str, capacity: int):
        self.symbol = symbol
        self.timeframe = timeframe
        self.capacity = capacity
        self.start = 0
        self.count = 0
        self.open_times = array("q", [0]) * capacity
        self.close_times = array("q", [0]) * capacity
        self.opens = array("d", [0.0]) * capacity
        self.highs = array("d", [0.0]) * capacity
        self.lows = array("d", [0.0]) * capacity
        self.closes = array("d", [0.0]) * capacity
        self.volumes = array("d", [0.0]) * capacity
        self.quote_volumes = array("d", [0.0]) * capacity
        self.indicator_state = IncrementalTechnicalIndicators()

    @property
    def allocated_bytes(self) -> int:
        """返回底层固定数值缓冲区字节数，不含少量 Python 容器元数据。"""
        return (
            self.capacity * self.open_times.itemsize * self._INT_COLUMN_COUNT
            + self.capacity * self.opens.itemsize * self._FLOAT_COLUMN_COUNT
        )

    @staticmethod
    def _timestamp_micros(value: datetime) -> int:
        """将时区时间转换为微秒 Unix 时间戳。"""
        return int(value.timestamp() * 1_000_000)

    @staticmethod
    def _datetime(value: int) -> datetime:
        """将微秒 Unix 时间戳恢复为 UTC datetime。"""
        return datetime.fromtimestamp(value / 1_000_000, timezone.utc)

    def _physical_index(self, logical_index: int) -> int:
        """把从旧到新的逻辑下标映射到底层环形数组。"""
        return (self.start + logical_index) % self.capacity

    def _write(self, index: int, kline: KlineData) -> None:
        """把领域 K 线压缩写入指定物理位置。"""
        self.open_times[index] = self._timestamp_micros(kline.open_time)
        self.close_times[index] = self._timestamp_micros(kline.close_time)
        self.opens[index] = float(kline.open)
        self.highs[index] = float(kline.high)
        self.lows[index] = float(kline.low)
        self.closes[index] = float(kline.close)
        self.volumes[index] = float(kline.volume)
        self.quote_volumes[index] = float(kline.quote_volume)

    def _read(self, index: int) -> KlineData:
        """把指定物理位置的紧凑数据恢复为系统边界使用的领域 K 线。"""
        return KlineData(
            symbol=self.symbol,
            timeframe=self.timeframe,
            open_time=self._datetime(self.open_times[index]),
            close_time=self._datetime(self.close_times[index]),
            open=Decimal(str(self.opens[index])),
            high=Decimal(str(self.highs[index])),
            low=Decimal(str(self.lows[index])),
            close=Decimal(str(self.closes[index])),
            volume=Decimal(str(self.volumes[index])),
            quote_volume=Decimal(str(self.quote_volumes[index])),
            is_closed=True,
        )

    def load(self, klines: list[KlineData]) -> None:
        """用完整历史初始化环形数组和增量指标状态。"""
        unique = {
            self._timestamp_micros(item.open_time): item
            for item in klines if item.is_closed
        }
        ordered = [unique[key] for key in sorted(unique)][-self.capacity:]
        self.start = 0
        self.count = len(ordered)
        for index, kline in enumerate(ordered):
            self._write(index, kline)
        # 初始化直接使用原始 Decimal，避免首次压缩产生不必要的指标舍入差异。
        self.indicator_state = IncrementalTechnicalIndicators.from_klines(ordered)

    def upsert(self, kline: KlineData) -> str:
        """写入完整 K 线，返回 appended、replaced 或 ignored。"""
        if not kline.is_closed:
            return "ignored"
        open_time = self._timestamp_micros(kline.open_time)
        if self.count == 0:
            self._write(0, kline)
            self.count = 1
            self.indicator_state.update(kline)
            return "appended"

        latest_index = self._physical_index(self.count - 1)
        latest_open_time = self.open_times[latest_index]
        if open_time > latest_open_time:
            if self.count < self.capacity:
                index = self._physical_index(self.count)
                self.count += 1
                self._write(index, kline)
                self.indicator_state.update(kline)
            else:
                # 递归类指标按标准持续推进；OBV 仍严格对应当前 1000 根窗口。
                oldest = self._read(self.start)
                next_oldest = self._read(self._physical_index(1))
                self.indicator_state.discard_oldest_obv_transition(oldest, next_oldest)
                index = self.start
                self.start = (self.start + 1) % self.capacity
                self._write(index, kline)
                self.indicator_state.update(kline)
            return "appended"

        for logical_index in range(self.count - 1, -1, -1):
            index = self._physical_index(logical_index)
            candidate = self.open_times[index]
            if candidate == open_time:
                self._write(index, kline)
                self._rebuild_indicators()
                return "replaced"
            if candidate < open_time:
                break
        # 缓存窗口外的迟到数据仍可持久化到数据库，但不能破坏当前有序窗口。
        return "ignored"

    def _rebuild_indicators(self) -> None:
        """从当前紧凑窗口重建被历史覆盖影响的指标状态。"""
        self.indicator_state = IncrementalTechnicalIndicators.from_klines(self.to_klines())

    def to_klines(self) -> list[KlineData]:
        """按时间顺序展开当前窗口，供图表与兼容调用使用。"""
        return [self._read(self._physical_index(index)) for index in range(self.count)]

    def latest(self) -> KlineData | None:
        """返回最新完整 K 线。"""
        if self.count == 0:
            return None
        return self._read(self._physical_index(self.count - 1))

    def volume_ema(self, period: int) -> Decimal | None:
        """用最近 period 根紧凑成交量计算兼容旧口径的 EMA。"""
        if self.count < period:
            return None
        start = self.count - period
        values = [
            Decimal(str(self.volumes[self._physical_index(index)]))
            for index in range(start, self.count)
        ]
        alpha = Decimal(2) / Decimal(period + 1)
        ema = values[0]
        for value in values[1:]:
            ema = value * alpha + ema * (Decimal(1) - alpha)
        return ema

    def indicators(self) -> TechnicalIndicators | None:
        """返回最新完整 K 线对应的增量指标快照。"""
        return self.indicator_state.result(self.count)


class KlineCache:
    """维护全部活跃市场的紧凑完整历史、当前 K 线与增量指标。"""

    def __init__(self, max_closed: int = 1000):
        self._closed: dict[str, dict[str, ColumnarKlineRing]] = defaultdict(dict)
        self._current: dict[str, dict[str, KlineData]] = defaultdict(dict)
        self._max_closed = max_closed
        self._allowed_markets: set[tuple[str, str]] | None = None
        self._lock = asyncio.Lock()

    async def initialize(self, symbol: str, timeframe: str, klines: list[KlineData]) -> None:
        """用 REST 或数据库历史初始化单个市场周期。"""
        async with self._lock:
            if (
                self._allowed_markets is not None
                and (symbol, timeframe) not in self._allowed_markets
            ):
                return
            bucket = ColumnarKlineRing(symbol, timeframe, self._max_closed)
            bucket.load(klines)
            self._closed[symbol][timeframe] = bucket
            current = next((item for item in reversed(klines) if not item.is_closed), None)
            if current:
                self._current[symbol][timeframe] = current
            else:
                self._current.get(symbol, {}).pop(timeframe, None)

    async def update(self, kline: KlineData) -> bool:
        """写入实时事件，返回该事件是否为允许市场的完整 K 线。"""
        async with self._lock:
            key = (kline.symbol, kline.timeframe)
            if self._allowed_markets is not None and key not in self._allowed_markets:
                return False
            if not kline.is_closed:
                self._current[kline.symbol][kline.timeframe] = kline
                return False
            bucket = self._closed[kline.symbol].get(kline.timeframe)
            if bucket is None:
                bucket = ColumnarKlineRing(kline.symbol, kline.timeframe, self._max_closed)
                self._closed[kline.symbol][kline.timeframe] = bucket
            bucket.upsert(kline)
            current = self._current.get(kline.symbol, {}).get(kline.timeframe)
            if current and current.open_time == kline.open_time:
                self._current[kline.symbol].pop(kline.timeframe, None)
                if not self._current[kline.symbol]:
                    del self._current[kline.symbol]
            return True

    async def snapshot(
        self, symbol: str, timeframe: str
    ) -> tuple[KlineData | None, list[KlineData]]:
        """返回当前 K 线和按需展开的完整历史隔离副本。"""
        async with self._lock:
            current = self._current.get(symbol, {}).get(timeframe)
            bucket = self._closed.get(symbol, {}).get(timeframe)
            return current, bucket.to_klines() if bucket else []

    async def scan_snapshot(
        self, symbol: str, timeframe: str, volume_period: int
    ) -> tuple[KlineData | None, Decimal | None, TechnicalIndicators | None]:
        """直接返回扫描器需要的当前 K 线、成交量 EMA 和增量指标。"""
        async with self._lock:
            current = self._current.get(symbol, {}).get(timeframe)
            bucket = self._closed.get(symbol, {}).get(timeframe)
            if bucket is None:
                return current, None, None
            return current, bucket.volume_ema(volume_period), bucket.indicators()

    async def latest_closed(self, symbol: str, timeframe: str) -> KlineData | None:
        """返回指定市场最新完整 K 线，避免为缺口判断展开整个窗口。"""
        async with self._lock:
            bucket = self._closed.get(symbol, {}).get(timeframe)
            return bucket.latest() if bucket else None

    async def current(self, symbol: str, timeframe: str) -> KlineData | None:
        """返回指定市场当前未收盘 K 线，不展开完整历史。"""
        async with self._lock:
            return self._current.get(symbol, {}).get(timeframe)

    async def retain(self, symbols: set[str], timeframes: set[str]) -> None:
        """仅保留当前活跃交易对与启用周期，并拒绝退出市场的迟到事件。"""
        desired = {(symbol, timeframe) for symbol in symbols for timeframe in timeframes}
        async with self._lock:
            self._allowed_markets = desired
            for symbol in list(self._closed):
                for timeframe in list(self._closed[symbol]):
                    if (symbol, timeframe) not in desired:
                        del self._closed[symbol][timeframe]
                if not self._closed[symbol]:
                    del self._closed[symbol]
            for symbol in list(self._current):
                for timeframe in list(self._current[symbol]):
                    if (symbol, timeframe) not in desired:
                        del self._current[symbol][timeframe]
                if not self._current[symbol]:
                    del self._current[symbol]

    async def market_keys(self) -> set[tuple[str, str]]:
        """返回已经初始化完整历史的市场周期键。"""
        async with self._lock:
            return {
                (symbol, timeframe)
                for symbol, buckets in self._closed.items()
                for timeframe in buckets
            }

    async def symbols(self) -> set[str]:
        """返回缓存当前覆盖的全部交易对。"""
        async with self._lock:
            return set(self._closed) | set(self._current)

    async def allocated_bytes(self) -> int:
        """返回全部列式数值缓冲区分配字节数。"""
        async with self._lock:
            return sum(
                bucket.allocated_bytes
                for buckets in self._closed.values()
                for bucket in buckets.values()
            )


def kline_progress(kline: KlineData, now: datetime | None = None) -> Decimal:
    """计算并钳制当前 K 线的 0-1 形成进度。"""
    now = now or datetime.now(timezone.utc)
    total = (kline.close_time - kline.open_time).total_seconds()
    elapsed = (now - kline.open_time).total_seconds()
    if total <= 0:
        return Decimal(0)
    return min(Decimal(1), max(Decimal(0), Decimal(str(elapsed / total))))


def volume_ema(closed_klines: list[KlineData], period: int) -> Decimal | None:
    """兼容独立调用：只用最近 period 根完整 K 线计算成交量 EMA。"""
    if len(closed_klines) < period:
        return None
    values = [item.volume for item in closed_klines[-period:]]
    alpha = Decimal(2) / Decimal(period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = value * alpha + ema * (Decimal(1) - alpha)
    return ema
