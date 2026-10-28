"""The queue between the ingestion reader and the worker pool.

`JobQueue` is the seam for a durable broker (e.g. Redis Streams): the API and
workers only depend on this interface, so swapping `InMemoryQueue` for a
broker-backed class does not touch them.
"""

from __future__ import annotations

import asyncio
from typing import Generic, Protocol, TypeVar

T = TypeVar("T")


class JobQueue(Protocol[T]):
    async def put(self, item: T) -> None: ...

    async def get(self) -> T | None:
        """Next item, or None once the producer has closed the queue."""
        ...

    async def close(self, consumers: int) -> None:
        """Signal end-of-stream to `consumers` consumers."""
        ...


class InMemoryQueue(Generic[T]):
    """Bounded asyncio queue. `put` waits while the queue is full: that is the backpressure."""

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1 (an unbounded queue defeats backpressure)")
        self._q: asyncio.Queue[T | None] = asyncio.Queue(maxsize=maxsize)
        self.maxsize = maxsize
        self.high_water = 0

    async def put(self, item: T) -> None:
        await self._q.put(item)
        self.high_water = max(self.high_water, self._q.qsize())

    async def get(self) -> T | None:
        return await self._q.get()

    async def close(self, consumers: int) -> None:
        for _ in range(consumers):
            await self._q.put(None)

    def qsize(self) -> int:
        return self._q.qsize()
