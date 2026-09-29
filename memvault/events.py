"""In-process event bus for WebSocket push (Sprint 11).

Scope of v0.3: one MemVault HTTP process (REST API + dashboard). The
FastAPI endpoints for memory ops are *sync* functions and run in a
worker thread, so publishing must hop back to the bound event loop via
``call_soon_threadsafe``.

A standalone MCP stdio server is a different process and does NOT share
this bus (no in-memory queue can cross processes); cross-process push is
left to DB polling / an external pub/sub in a later version.

Slow consumers never block writers: per-subscriber queues are bounded;
when full the oldest event is dropped for that subscriber.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from .storage import now_iso

QUEUE_MAX = 100


class EventManager:
    def __init__(self, maxsize: int = QUEUE_MAX) -> None:
        self._default_loop: Optional[asyncio.AbstractEventLoop] = None
        # queue -> the event loop it was created on
        self._queues: dict[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop] = {}
        self._maxsize = maxsize

    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._default_loop = loop or asyncio.get_running_loop()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a bounded queue on the running event loop."""
        loop = self._default_loop if (
            self._default_loop is not None and not self._default_loop.is_closed()
        ) else asyncio.get_running_loop()
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._maxsize)
        self._queues[q] = loop
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.pop(q, None)

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    def _live_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Any live subscriber loop, else a live bound default loop."""
        for loop in self._queues.values():
            if not loop.is_closed():
                return loop
        if self._default_loop is not None and not self._default_loop.is_closed():
            return self._default_loop
        return None

    def publish(self, event_type: str, data: Optional[dict[str, Any]] = None) -> None:
        """Thread-safe fire-and-forget. No-op with no live loop/queues."""
        loop = self._live_loop()
        if loop is None:
            return
        event = {"type": event_type, "ts": now_iso(), "data": data or {}}

        def _dispatch() -> None:
            for q, qloop in tuple(self._queues.items()):
                if qloop.is_closed():
                    self._queues.pop(q, None)
                    continue
                if q.full():
                    try:
                        q.get_nowait()  # drop oldest for the slow subscriber
                    except asyncio.QueueEmpty:
                        pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

        loop.call_soon_threadsafe(_dispatch)
