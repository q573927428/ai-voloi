"""为迁移前生成的 Signal 回填完整 K 线技术指标快照。"""

import asyncio

from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.models import Kline, Signal
from app.schemas import KlineData
from app.services.cache.technical_indicators import (
    build_indicator_response,
    calculate_technical_indicators,
)


async def backfill() -> int:
    """回算所有缺少结构化指标快照的 Signal，返回成功补齐的记录数。"""
    updated = 0
    async with SessionLocal() as session:
        signals = (
            await session.execute(select(Signal).where(Signal.technical_indicators.is_(None)))
        ).scalars().all()
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
                .limit(498)
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
            # 旧扁平字段继续补齐，结构化快照承载第一、二期全部指标。
            signal.ema14 = indicators.ema14
            signal.ema50 = indicators.ema50
            signal.rsi14 = indicators.rsi14
            signal.adx14 = indicators.adx14
            signal.atr14 = indicators.atr14
            signal.adx_slope = indicators.adx_slope
            signal.ema14_slope_percent = indicators.ema14_slope_percent
            signal.ema50_slope_percent = indicators.ema50_slope_percent
            signal.technical_indicators = build_indicator_response(
                signal.symbol, signal.timeframe, indicators
            ).model_dump(mode="json")
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
