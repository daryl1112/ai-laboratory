"""One-socket hub. Typed messages:
  log            {exp, stream, line}
  status         {exp, status}
  progress       {exp, pct, message}
  trace          {exp, event}
  plan_ready     {exp}
  stats_system   {...}
  stats_container{exp, ...}
  tools          {tools: [...]}
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, type_: str, payload: dict[str, Any]) -> None:
        message = json.dumps({"type": type_, **payload}, default=str)
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:
                await self.disconnect(ws)


hub = Hub()
