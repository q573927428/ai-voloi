"""Binance Futures REST 客户端、限速、排队和重试策略。"""

import asyncio
import random
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import Settings
from app.schemas import FundingRateData, KlineData, OIPoint, TickerData


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
        """返回系统支持的 USDT 永续合约，包括标准永续和全部 TradFi 永续。"""
        data = await self._get("/fapi/v1/exchangeInfo")
        return [
            item for item in data["symbols"]
            if item["status"] == "TRADING"
            and item["quoteAsset"] == "USDT"
            and (
                item["contractType"] == "PERPETUAL"
                # 美股、ETF、贵金属和能源等 TradFi 合约的行情、K 线与 OI 接口均兼容现有链路。
                or item["contractType"] == "TRADIFI_PERPETUAL"
            )
        ]

    async def tickers(self) -> dict[str, TickerData]:
        data = await self._get("/fapi/v1/ticker/24hr")
        return {
            item["symbol"]: TickerData(
                symbol=item["symbol"], last_price=item["lastPrice"],
                price_change_percent=item["priceChangePercent"], quote_volume=item["quoteVolume"]
            ) for item in data
        }

    async def klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[KlineData]:
        """读取 K 线；起止毫秒时间用于增量补齐或定位历史观察点。"""
        params = {"symbol": symbol, "interval": timeframe, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        data = await self._get("/fapi/v1/klines", params)
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

    async def open_interest(self, symbol: str, period: str, start_ms: int, limit: int = 100) -> list[OIPoint]:
        """读取指定采样粒度的 OI 时点序列；period 不代表 Signal 的 K 线周期。"""
        data = await self._get("/futures/data/openInterestHist", {
            "symbol": symbol, "period": period, "startTime": start_ms, "limit": limit
        })
        return [
            OIPoint(timestamp=datetime.fromtimestamp(row["timestamp"] / 1000, timezone.utc), open_interest=row["sumOpenInterest"])
            for row in data
        ]

    async def funding_rates(self) -> dict[str, FundingRateData]:
        """批量读取全部永续合约最近一次资金费率。"""
        data = await self._get("/fapi/v1/premiumIndex")
        return {
            item["symbol"]: FundingRateData(
                symbol=item["symbol"], funding_rate=item["lastFundingRate"]
            )
            for item in data
            # 个别非标准合约可能不返回费率，不能让其阻断整个市场快照刷新。
            if item.get("symbol") and item.get("lastFundingRate") is not None
        }
