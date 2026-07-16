"""In-memory pub/sub bridging blocking container output to async WebSockets.

A background thread reads the container's blocking log iterator and publishes
typed WSMessages. Each experiment keeps a bounded replay buffer so a client
that connects mid-run still sees recent history before live tailing.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque

from schemas import WSMessage


class LogBus:
    def __init__(self, replay: int = 500):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._buffers: dict[str, deque] = defaultdict(lambda: deque(maxlen=replay))
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # --- producer side (may be called from worker threads) ------------------
    def publish(self, exp_id: str, msg: WSMessage) -> None:
        self._buffers[exp_id].append(msg)
        if self._loop is None:
            return
        for q in list(self._subs.get(exp_id, ())):
            self._loop.call_soon_threadsafe(q.put_nowait, msg)

    # --- consumer side (WebSocket handlers) ---------------------------------
    async def subscribe(self, exp_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        # replay recent history first
        for msg in list(self._buffers.get(exp_id, ())):
            q.put_nowait(msg)
        self._subs[exp_id].add(q)
        return q

    def unsubscribe(self, exp_id: str, q: asyncio.Queue) -> None:
        self._subs.get(exp_id, set()).discard(q)


bus = LogBus()
