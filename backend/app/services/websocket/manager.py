"""Binance 组合流分片、心跳、自动重连与重新订阅。"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets

from app.core.config import Settings
from app.schemas import KlineData

logger = logging.getLogger(__name__)


class BinanceWebSocketManager:
    """按单连接流上限拆分并管理多条 Binance WebSocket 连接。"""
    def __init__(self, settings: Settings, on_kline: Callable[[KlineData], Awaitable[None]]):
        self.settings = settings
        self.on_kline = on_kline
        self.status = "disconnected"
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def start(self, symbols: set[str], timeframes: list[str]) -> None:
        """根据最新活跃池重建全部组合流订阅。"""
        await self.stop()
        self._stop = asyncio.Event()
        streams = [f"{symbol.lower()}@kline_{timeframe}" for symbol in sorted(symbols) for timeframe in timeframes]
        size = self.settings.ws_streams_per_connection
        self._tasks = [
            asyncio.create_task(self._run_chunk(streams[i : i + size]), name=f"binance-ws-{i // size}")
            for i in range(0, len(streams), size)
        ]
        self.status = "connecting" if streams else "disconnected"

    async def stop(self) -> None:
        """取消连接任务并恢复断开状态。"""
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.status = "disconnected"

    async def _run_chunk(self, streams: list[str]) -> None:
        """维护一组组合流，断线后指数退避并自动重订阅。"""
        delay = 1
        url = f"{self.settings.binance_ws_url}?streams={'/'.join(streams)}"
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
                    self.status = "connected"
                    delay = 1
                    async for raw in ws:
                        payload = json.loads(raw)
                        event = payload.get("data", payload)
                        if event.get("e") != "kline":
                            continue
                        k = event["k"]
                        await self.on_kline(KlineData(
                            symbol=k["s"], timeframe=k["i"], open_time=k["t"], close_time=k["T"] + 1,
                            open=k["o"], high=k["h"], low=k["l"], close=k["c"], volume=k["v"],
                            quote_volume=k["q"], is_closed=k["x"],
                        ))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = "reconnecting"
                logger.exception("Binance WebSocket disconnected; reconnecting")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
