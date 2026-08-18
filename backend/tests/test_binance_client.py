"""Binance REST 客户端交易对筛选规则测试。"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.binance.client import BinanceClient


@pytest.mark.asyncio
async def test_exchange_symbols_includes_equity_tradifi_perpetual() -> None:
    """美股和商品 TradFi 永续应进入系统，但非 USDT 合约不应混入。"""
    client = BinanceClient.__new__(BinanceClient)

    async def fake_get(path: str) -> dict:
        assert path == "/fapi/v1/exchangeInfo"
        return {
            "symbols": [
                {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT"},
                {"symbol": "TSLAUSDT", "contractType": "TRADIFI_PERPETUAL", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "EQUITY"},
                {"symbol": "XAUUSDT", "contractType": "TRADIFI_PERPETUAL", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "COMMODITY"},
                {"symbol": "SPCXUSD1", "contractType": "TRADIFI_PERPETUAL", "status": "TRADING", "quoteAsset": "USD1", "underlyingType": "EQUITY"},
            ]
        }

    client._get = fake_get

    symbols = await client.exchange_symbols()

    assert [item["symbol"] for item in symbols] == ["BTCUSDT", "TSLAUSDT", "XAUUSDT"]


@pytest.mark.asyncio
async def test_funding_rates_parses_bulk_premium_index() -> None:
    """批量 premium index 应解析资金费率，并忽略没有费率的非标准合约。"""
    client = BinanceClient.__new__(BinanceClient)

    async def fake_get(path: str) -> list[dict]:
        assert path == "/fapi/v1/premiumIndex"
        return [
            {"symbol": "BTCUSDT", "lastFundingRate": "0.00010000"},
            {"symbol": "ETHUSDT", "lastFundingRate": "-0.00025000"},
            {"symbol": "XAUUSDT"},
        ]

    client._get = fake_get

    rates = await client.funding_rates()

    assert rates["BTCUSDT"].funding_rate == Decimal("0.00010000")
    assert rates["ETHUSDT"].funding_rate == Decimal("-0.00025000")
    assert "XAUUSDT" not in rates


@pytest.mark.asyncio
async def test_klines_passes_historical_end_time() -> None:
    """历史观察点查询必须把 endTime 传给 Binance K 线接口。"""
    client = BinanceClient.__new__(BinanceClient)
    expected_end_ms = 1_700_000_000_000

    async def fake_get(path: str, params: dict) -> list[list]:
        """验证历史 K 线请求参数并返回一根完整 K 线。"""
        assert path == "/fapi/v1/klines"
        assert params == {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "limit": 1,
            "endTime": expected_end_ms,
        }
        open_ms = expected_end_ms - 60_000
        return [[open_ms, "100", "102", "99", "101", "5", expected_end_ms - 1, "505"]]

    client._get = fake_get

    klines = await client.klines("BTCUSDT", "1m", 1, end_ms=expected_end_ms)

    assert len(klines) == 1
    assert klines[0].close == 101
    assert klines[0].close_time == datetime.fromtimestamp(expected_end_ms / 1000, timezone.utc)


@pytest.mark.asyncio
async def test_fund_flow_klines_parses_taker_buy_quote_volume() -> None:
    """资金流 K 线必须保留总成交额和主动买入报价成交额。"""
    client = BinanceClient.__new__(BinanceClient)

    async def fake_get(path: str, params: dict) -> list[list]:
        assert path == "/fapi/v1/klines"
        assert params == {"symbol": "BTCUSDT", "interval": "1h", "limit": 120}
        return [[
            1_700_000_000_000, "100", "102", "99", "101", "5",
            1_700_003_599_999, "505", 42, "2", "202", "0",
        ]]

    client._get = fake_get

    klines = await client.fund_flow_klines("BTCUSDT", "1h", 120)

    assert klines[0].quote_volume == Decimal("505")
    assert klines[0].taker_buy_quote_volume == Decimal("202")


@pytest.mark.asyncio
async def test_open_interest_parses_notional_value() -> None:
    """OI 历史应同时保留持仓数量和报价资产名义价值。"""
    client = BinanceClient.__new__(BinanceClient)

    async def fake_get(path: str, params: dict) -> list[dict]:
        assert path == "/futures/data/openInterestHist"
        return [{
            "timestamp": 1_700_000_000_000,
            "sumOpenInterest": "1000",
            "sumOpenInterestValue": "101000",
        }]

    client._get = fake_get

    points = await client.open_interest("BTCUSDT", "1h", 1_699_000_000_000)

    assert points[0].open_interest == Decimal("1000")
    assert points[0].open_interest_value == Decimal("101000")
