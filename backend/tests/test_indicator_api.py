"""市场技术指标 API 参数解析与响应测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.routes import market_indicators, parse_ema_periods
from app.schemas import KlineData


class FakeScalars:
    """模拟 SQLAlchemy scalars 结果。"""

    def __init__(self, rows: list[KlineData]):
        self.rows = rows

    def all(self) -> list[KlineData]:
        """返回预设 K 线。"""
        return self.rows


class FakeResult:
    """模拟 SQLAlchemy 查询结果。"""

    def __init__(self, rows: list[KlineData]):
        self.rows = rows

    def scalars(self) -> FakeScalars:
        """返回可提取模型序列的结果对象。"""
        return FakeScalars(self.rows)


class FakeSession:
    """为指标路由提供倒序历史 K 线的异步会话替身。"""

    def __init__(self, rows: list[KlineData]):
        self.rows = rows

    async def execute(self, _statement) -> FakeResult:
        """忽略 SQL 表达式并返回预设结果。"""
        return FakeResult(self.rows)


def make_klines(count: int = 250) -> list[KlineData]:
    """构造按时间倒序返回的稳定上涨完整 K 线。"""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        KlineData(
            symbol="BTCUSDT",
            timeframe="15m",
            open_time=start + timedelta(minutes=15 * index),
            close_time=start + timedelta(minutes=15 * (index + 1)),
            open=Decimal(100 + index),
            high=Decimal(102 + index),
            low=Decimal(99 + index),
            close=Decimal(101 + index),
            volume=Decimal(1000 + index),
            quote_volume=Decimal(100_000 + index),
            is_closed=True,
        )
        for index in range(count)
    ]
    return list(reversed(rows))


def test_parse_ema_periods_deduplicates_and_validates_range() -> None:
    """EMA 参数应保持顺序去重，并拒绝超出成本边界的周期。"""
    assert parse_ema_periods("9, 21,9,200") == (9, 21, 200)
    with pytest.raises(HTTPException) as error:
        parse_ema_periods("1,21")
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_market_indicator_endpoint_returns_requested_ema_and_groups() -> None:
    """市场指标接口应归一化交易对并返回两期指标分组。"""
    response = await market_indicators(
        symbol="btcusdt",
        timeframe="15m",
        ema_periods="9,21,200",
        at=None,
        session=FakeSession(make_klines()),
    )

    assert response.symbol == "BTCUSDT"
    assert set(response.trend.ema) == {"9", "21", "200"}
    assert response.trend.ema_alignment == "bullish"
    assert response.trend.adx.plus_di > response.trend.adx.minus_di
    assert response.momentum.macd.histogram is not None
    assert response.volatility.atr.percent > 0
    assert response.volume.obv > 0
