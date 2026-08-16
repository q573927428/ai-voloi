"""为迁移前生成的 Signal 回填完整 K 线技术指标快照。"""

import asyncio

from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.models import Kline, Signal
from app.schemas import KlineData
from app.services.cache.technical_indicators import calculate_technical_indicators


async def backfill() -> int:
    """回算所有缺少 EMA14 的 Signal，返回成功补齐的记录数。"""
    updated = 0
    async with SessionLocal() as session:
        signals = (await session.execute(select(Signal).where(Signal.ema14.is_(None)))).scalars().all()
        for signal in signals:
            rows = (await session.execute(
                select(Kline)
                .where(
                    Kline.symbol == signal.symbol,
                    Kline.timeframe == signal.timeframe,
                    Kline.is_closed.is_(True),
                    Kline.open_time < signal.open_time,
                )
                .order_by(Kline.open_time.desc())
                .limit(100)
            )).scalars().all()
            klines = [
                KlineData(
                    symbol=row.symbol,
                    timeframe=row.timeframe,
                    open_time=row.open_time,
                    close_time=row.close_time,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    quote_volume=row.quote_volume,
                    is_closed=row.is_closed,
                )
                for row in reversed(rows)
            ]
            indicators = calculate_technical_indicators(klines)
            if not indicators:
                continue
            for field, value in indicators.__dict__.items():
                setattr(signal, field, value)
            updated += 1
        await session.commit()
    return updated


async def main() -> None:
    """执行回填并释放数据库连接池。"""
    try:
        print(f"backfilled={await backfill()}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
