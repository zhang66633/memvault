"""Sprint 11: EventManager (in-process pub/sub for WebSocket push)."""
from __future__ import annotations

import asyncio
import sys
import threading
import time

sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parents[1])

from memvault.events import EventManager


def test_publish_to_one_subscriber():
    async def case():
        bus = EventManager()
        bus.bind_loop()
        q = await bus.subscribe()
        assert bus.subscriber_count == 1
        bus.publish("memory.updated", {"id": "m1"})
        evt = await asyncio.wait_for(q.get(), timeout=1)
        assert evt["type"] == "memory.updated"
        assert evt["data"]["id"] == "m1"
        assert evt["ts"]
        bus.unsubscribe(q)
        assert bus.subscriber_count == 0

    asyncio.run(case())


def test_publish_to_multiple_subscribers():
    async def case():
        bus = EventManager()
        bus.bind_loop()
        qs = [await bus.subscribe() for _ in range(3)]
        bus.publish("memory.added", {"results": []})
        for q in qs:
            evt = await asyncio.wait_for(q.get(), timeout=1)
            assert evt["type"] == "memory.added"

    asyncio.run(case())


def test_publish_from_another_thread_reaches_loop_queue():
    async def case():
        bus = EventManager()
        bus.bind_loop()
        q = await bus.subscribe()

        def worker():
            time.sleep(0.05)
            bus.publish("memory.deleted", {"memory_id": "x"})

        threading.Thread(target=worker, daemon=True).start()
        evt = await asyncio.wait_for(q.get(), timeout=2)
        assert evt["data"]["memory_id"] == "x"

    asyncio.run(case())


def test_no_subscribers_is_safe_noop():
    async def case():
        bus = EventManager()
        bus.bind_loop()
        bus.publish("memory.added")  # no exception

    asyncio.run(case())


def test_full_queue_drops_oldest_never_blocks():
    async def case():
        bus = EventManager(maxsize=2)
        bus.bind_loop()
        q = await bus.subscribe()
        for i in range(5):
            bus.publish("block.updated", {"n": i})
        await asyncio.sleep(0.05)
        got = []
        while not q.empty():
            got.append((await q.get())["data"]["n"])
        # bounded to 2, oldest dropped; publisher never blocked
        assert got == [3, 4]

    asyncio.run(case())
