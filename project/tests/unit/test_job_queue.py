import asyncio

import pytest

from batch_engine.job_queue import InMemoryQueue


async def test_put_blocks_when_full_until_a_consumer_takes_an_item():
    q: InMemoryQueue[int] = InMemoryQueue(maxsize=2)
    await q.put(1)
    await q.put(2)
    blocked = asyncio.create_task(q.put(3))
    await asyncio.sleep(0.01)
    assert not blocked.done(), "producer must wait while the queue is full"
    assert await q.get() == 1
    await asyncio.wait_for(blocked, timeout=1)
    assert q.qsize() == 2


async def test_fifo_and_close_sends_one_sentinel_per_consumer():
    q: InMemoryQueue[str] = InMemoryQueue(maxsize=10)
    await q.put("a")
    await q.put("b")
    await q.close(consumers=3)
    assert [await q.get() for _ in range(5)] == ["a", "b", None, None, None]


async def test_high_water_mark_never_exceeds_maxsize():
    q: InMemoryQueue[int] = InMemoryQueue(maxsize=3)

    async def producer():
        for i in range(50):
            await q.put(i)
        await q.close(1)

    async def consumer():
        while await q.get() is not None:
            await asyncio.sleep(0)

    await asyncio.gather(producer(), consumer())
    assert 1 <= q.high_water <= 3


def test_unbounded_queue_is_rejected():
    with pytest.raises(ValueError):
        InMemoryQueue(maxsize=0)
