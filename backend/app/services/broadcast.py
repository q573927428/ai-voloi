"""进程内 Signal WebSocket 广播器。"""

import asyncio
from fastapi import WebSocket

from app.schemas import SignalRead


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
