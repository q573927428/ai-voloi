"""Signal 后固定观察点价格变化与最大涨跌幅回填服务。"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Signal, SignalFuturePerformance
from app.services.binance.client import BinanceClient

logger = logging.getLogger(__name__)
HORIZONS = {
    "price_change_5m_percent": 5,
    "price_change_15m_percent": 15,
    "price_change_30m_percent": 30,
    "price_change_1h_percent": 60,
    "price_change_4h_percent": 240,
    "price_change_8h_percent": 480,
    "price_change_12h_percent": 720,
    "price_change_16h_percent": 960,
    "price_change_1d_percent": 1440,
    "price_change_2d_percent": 2880,
}


class PerformanceTracker:
    """
    回填 Signal 后固定观察点的价格变化。

    最大涨幅和最大跌幅只聚合已经计算的固定观察点，不额外拉取观察点之间的
    1m K 线。所有结果均相对 Signal 价格计算，不表达持仓盈利或亏损。
    """

    def __init__(
        self,
        client: BinanceClient,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        self.client = client
        self.session_factory = session_factory

    async def _price_change_at(
        self,
        signal: Signal,
        target_time: datetime,
        now: datetime,
    ) -> Decimal | None:
        """读取目标分钟可用的最近完整 1m K 线，计算相对 Signal 价格的涨跌幅。"""
        minute_start = target_time.replace(second=0, microsecond=0)
        klines = await self.client.klines(
            signal.symbol,
            "1m",
            1,
            # 只给 endTime 时 Binance 返回该时间之前最近的 K 线，可覆盖 TradFi 休市观察点。
            end_ms=int((minute_start + timedelta(minutes=1)).timestamp() * 1000) - 1,
        )
        if not klines:
            return None
        kline = klines[0]
        if not (
            kline.is_closed
            and kline.open_time <= minute_start
            and kline.close_time <= now
        ):
            # 目标分钟尚未收盘或上游未返回对应 K 线时，本轮不写值，留待后续重试。
            return None
        return (kline.close - signal.current_price) / signal.current_price * 100

    async def update(self, now: datetime | None = None) -> None:
        """补齐所有已到期价格观察点，并维护观察点中的最大涨幅和最大跌幅。"""
        now = now or datetime.now(timezone.utc)
        due_conditions = [
            and_(
                getattr(SignalFuturePerformance, field).is_(None),
                Signal.detected_at <= now - timedelta(minutes=minutes),
            )
            for field, minutes in HORIZONS.items()
        ]
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(Signal, SignalFuturePerformance).join(SignalFuturePerformance)
                .where(or_(*due_conditions))
                .order_by(Signal.detected_at.desc())
            )).all()
            changed = False
            for signal, performance in rows:
                if signal.current_price <= 0:
                    continue
                for field, minutes in HORIZONS.items():
                    target_time = signal.detected_at + timedelta(minutes=minutes)
                    if target_time > now or getattr(performance, field) is not None:
                        continue
                    try:
                        result = await self._price_change_at(signal, target_time, now)
                    except Exception:
                        # 单个市场的历史数据失败不能阻止其他 Signal 和周期继续回填。
                        logger.exception(
                            "Failed to load historical performance price for %s at %s",
                            signal.symbol,
                            target_time,
                        )
                        continue
                    if result is not None:
                        setattr(performance, field, result)
                        changed = True

                available = [
                    getattr(performance, field)
                    for field in HORIZONS
                    if getattr(performance, field) is not None
                ]
                if available:
                    # 严格保留观察点最大值和最小值；全涨或全跌时两者可同号。
                    performance.max_rise_percent = max(available)
                    performance.max_drop_percent = min(available)
            if changed:
                await session.commit()
