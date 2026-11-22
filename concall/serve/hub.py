from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.recent: list[dict[str, Any]] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def publish(self, event: dict[str, Any]) -> None:
        self.recent.append(event)
        self.recent = self.recent[-50:]
        payload = json.dumps(event, default=str)
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)
