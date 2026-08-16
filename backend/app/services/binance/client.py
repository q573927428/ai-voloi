"""Binance Futures REST 客户端、限速、排队和重试策略。"""

import asyncio
import random
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import Settings
from app.schemas import KlineData, OIPoint, TickerData


class RateLimiter:
    """在所有 REST 调用之间强制最小时间间隔的异步限速器。"""
    def __init__(self, rate: float):
        self.interval = 1 / rate
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """等待直至下一个请求配额可用。"""
        async with self._lock:
            now = asyncio.get_running_loop().time()
            delay = self.interval - (now - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = asyncio.get_running_loop().time()


class BinanceClient:
    """集中管理 Binance REST 请求及领域数据解析。"""
    RETRYABLE = {429, 500, 502, 503}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.limiter = RateLimiter(settings.rest_rate_per_second)
        self.semaphore = asyncio.Semaphore(settings.rest_concurrency)
        self.http = httpx.AsyncClient(base_url=settings.binance_rest_url, timeout=settings.rest_timeout_seconds)

    async def close(self) -> None:
        await self.http.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """执行受限速和并发控制的 GET，并有限重试可恢复错误。"""
        last_error: Exception | None = None
        for attempt in range(self.settings.rest_max_retries + 1):
            try:
                await self.limiter.acquire()
                async with self.semaphore:
                    response = await self.http.get(path, params=params)
                if response.status_code in self.RETRYABLE:
                    retry_after = float(response.headers.get("Retry-After", 0))
                    raise httpx.HTTPStatusError(
                        f"Binance retryable status {response.status_code}", request=response.request, response=response
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in self.RETRYABLE
                if not retryable or attempt >= self.settings.rest_max_retries:
                    raise
                header_delay = float(exc.response.headers.get("Retry-After", 0)) if isinstance(exc, httpx.HTTPStatusError) else 0
                await asyncio.sleep(max(header_delay, 0.5 * (2**attempt) + random.random() * 0.2))
        raise RuntimeError("Binance request failed") from last_error

    async def exchange_symbols(self) -> list[dict[str, str]]:
        data = await self._get("/fapi/v1/exchangeInfo")
        return [
            item for item in data["symbols"]
            if item["contractType"] == "PERPETUAL" and item["status"] == "TRADING" and item["quoteAsset"] == "USDT"
        ]

    async def tickers(self) -> dict[str, TickerData]:
        data = await self._get("/fapi/v1/ticker/24hr")
        return {
            item["symbol"]: TickerData(
                symbol=item["symbol"], last_price=item["lastPrice"],
                price_change_percent=item["priceChangePercent"], quote_volume=item["quoteVolume"]
            ) for item in data
        }

    async def klines(self, symbol: str, timeframe: str, limit: int) -> list[KlineData]:
        data = await self._get("/fapi/v1/klines", {"symbol": symbol, "interval": timeframe, "limit": limit})
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return [
            KlineData(
                symbol=symbol, timeframe=timeframe,
                open_time=datetime.fromtimestamp(row[0] / 1000, timezone.utc),
                close_time=datetime.fromtimestamp((row[6] + 1) / 1000, timezone.utc),
                open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5],
                quote_volume=row[7], is_closed=row[6] < now_ms,
            ) for row in data
        ]

    async def open_interest(self, symbol: str, timeframe: str, start_ms: int, limit: int = 100) -> list[OIPoint]:
        data = await self._get("/futures/data/openInterestHist", {
            "symbol": symbol, "period": timeframe, "startTime": start_ms, "limit": limit
        })
        return [
            OIPoint(timestamp=datetime.fromtimestamp(row["timestamp"] / 1000, timezone.utc), open_interest=row["sumOpenInterest"])
            for row in data
        ]

    async def premium_index(self) -> list[dict[str, Any]]:
        return await self._get("/fapi/v1/premiumIndex")
