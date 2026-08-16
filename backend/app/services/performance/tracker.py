"""Signal 未来收益观察点的定时回填服务。"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Signal, SignalFuturePerformance
from app.services.cache.kline_cache import KlineCache

logger = logging.getLogger(__name__)
HORIZONS = {"return_5m": 5, "return_15m": 15, "return_30m": 30, "return_1h": 60, "return_4h": 240, "return_1d": 1440}


class PerformanceTracker:
    """使用实时缓存价格回填到期收益，并维护最大盈利和亏损。"""
    def __init__(self, cache: KlineCache, session_factory: async_sessionmaker[AsyncSession]):
        self.cache = cache
        self.session_factory = session_factory

    async def update(self) -> None:
        """扫描近两日 Signal 并补齐已经到期且尚未写入的观察点。"""
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(Signal, SignalFuturePerformance).join(SignalFuturePerformance)
                .where(Signal.detected_at >= now - timedelta(days=2))
            )).all()
            changed = False
            for signal, performance in rows:
                current, _ = await self.cache.snapshot(signal.symbol, "15m")
                if not current or signal.current_price <= 0:
                    continue
                result = (current.close - signal.current_price) / signal.current_price * 100
                elapsed = (now - signal.detected_at).total_seconds() / 60
                available = []
                for field, minutes in HORIZONS.items():
                    if elapsed >= minutes and getattr(performance, field) is None:
                        setattr(performance, field, result)
                        changed = True
                    value = getattr(performance, field)
                    if value is not None:
                        available.append(value)
                if available:
                    performance.max_profit_percent = max(available)
                    performance.max_loss_percent = min(available)
            if changed:
                await session.commit()
