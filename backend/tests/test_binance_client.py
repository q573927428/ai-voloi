"""Binance REST 客户端交易对筛选规则测试。"""

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
