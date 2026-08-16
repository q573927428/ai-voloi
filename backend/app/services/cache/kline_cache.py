"""并发安全的内存 K 线缓存与成交量指标函数。"""

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal

from app.schemas import KlineData


class KlineCache:
    """分别维护当前 K 线和有限长度的完整 K 线序列。"""
    def __init__(self, max_closed: int = 1000):
        self._closed: dict[str, dict[str, deque[KlineData]]] = defaultdict(dict)
        self._current: dict[str, dict[str, KlineData]] = defaultdict(dict)
        self._max_closed = max_closed
        self._lock = asyncio.Lock()

    async def initialize(self, symbol: str, timeframe: str, klines: list[KlineData]) -> None:
        """用 REST 历史窗口初始化一个交易对周期。"""
        async with self._lock:
            closed = [item for item in klines if item.is_closed]
            self._closed[symbol][timeframe] = deque(closed[-self._max_closed :], maxlen=self._max_closed)
            current = next((item for item in reversed(klines) if not item.is_closed), None)
            if current:
                self._current[symbol][timeframe] = current

    async def update(self, kline: KlineData) -> bool:
        """写入实时事件，返回该事件是否产生了完整 K 线。"""
        async with self._lock:
            if not kline.is_closed:
                self._current[kline.symbol][kline.timeframe] = kline
                return False
            bucket = self._closed[kline.symbol].setdefault(kline.timeframe, deque(maxlen=self._max_closed))
            if not bucket or bucket[-1].open_time != kline.open_time:
                bucket.append(kline)
            else:
                bucket[-1] = kline
            current = self._current[kline.symbol].get(kline.timeframe)
            if current and current.open_time == kline.open_time:
                self._current[kline.symbol].pop(kline.timeframe, None)
            return True

    async def snapshot(self, symbol: str, timeframe: str) -> tuple[KlineData | None, list[KlineData]]:
        """返回当前 K 线与完整历史的隔离副本。"""
        async with self._lock:
            return self._current[symbol].get(timeframe), list(self._closed[symbol].get(timeframe, []))

    async def symbols(self) -> set[str]:
        """返回缓存当前覆盖的全部交易对。"""
        async with self._lock:
            return set(self._closed) | set(self._current)


def kline_progress(kline: KlineData, now: datetime | None = None) -> Decimal:
    """计算并钳制当前 K 线的 0-1 形成进度。"""
    now = now or datetime.now(timezone.utc)
    total = (kline.close_time - kline.open_time).total_seconds()
    elapsed = (now - kline.open_time).total_seconds()
    if total <= 0:
        return Decimal(0)
    return min(Decimal(1), max(Decimal(0), Decimal(str(elapsed / total))))


def volume_ema(closed_klines: list[KlineData], period: int) -> Decimal | None:
    """只用最近 period 根完整 K 线计算成交量 EMA。"""
    if len(closed_klines) < period:
        return None
    values = [item.volume for item in closed_klines[-period:]]
    alpha = Decimal(2) / Decimal(period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = value * alpha + ema * (Decimal(1) - alpha)
    return ema
