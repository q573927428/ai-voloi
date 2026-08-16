"""进程内 Signal WebSocket 广播器。"""

import asyncio
from collections import defaultdict

from fastapi import WebSocket

from app.schemas import SignalChartCandle, SignalRead


class SignalBroadcaster:
    """维护前端连接集合并推送新生成的 Signal 快照。"""
    def __init__(self):
        self.connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """接受并登记一个前端 WebSocket。"""
        await websocket.accept()
        async with self._lock:
            self.connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """移除断开的前端 WebSocket。"""
        async with self._lock:
            self.connections.discard(websocket)

    async def publish(self, signal) -> None:
        """向全部连接广播快照，并清理发送失败的连接。"""
        message = {"type": "signal", "data": SignalRead.model_validate(signal).model_dump(mode="json")}
        dead = []
        for connection in list(self.connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        async with self._lock:
            for connection in dead:
                self.connections.discard(connection)


class KlineBroadcaster:
    """按交易对和周期维护图表连接，并增量推送最新 K 线及 EMA。"""

    def __init__(self):
        self.connections: dict[tuple[str, str], set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, symbol: str, timeframe: str) -> None:
        """接受连接并登记其唯一行情订阅键。"""
        await websocket.accept()
        async with self._lock:
            self.connections[(symbol, timeframe)].add(websocket)

    async def disconnect(self, websocket: WebSocket, symbol: str, timeframe: str) -> None:
        """移除连接，并清理不再使用的订阅集合。"""
        key = (symbol, timeframe)
        async with self._lock:
            self.connections[key].discard(websocket)
            if not self.connections[key]:
                self.connections.pop(key, None)

    async def has_subscribers(self, symbol: str, timeframe: str) -> bool:
        """判断对应市场是否存在实时图表订阅，避免无订阅时重复计算 EMA。"""
        async with self._lock:
            return bool(self.connections.get((symbol, timeframe)))

    async def subscription_keys(self) -> list[tuple[str, str]]:
        """返回当前实际有人查看的市场键，供断流兜底任务按需拉取。"""
        async with self._lock:
            return [key for key, connections in self.connections.items() if connections]

    async def publish(self, symbol: str, timeframe: str, candle: SignalChartCandle) -> None:
        """向同一交易对和周期的连接推送增量 K 线，并清理失效连接。"""
        key = (symbol, timeframe)
        async with self._lock:
            targets = list(self.connections.get(key, set()))
        message = {"type": "kline", "data": candle.model_dump(mode="json")}
        dead = []
        for connection in targets:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        if dead:
            async with self._lock:
                for connection in dead:
                    self.connections[key].discard(connection)
                if not self.connections[key]:
                    self.connections.pop(key, None)
