"""Shared test doubles: a fake clock, a scriptable fake inference provider, and helpers."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from batch_engine.config import Settings
from batch_engine.retry import RetryPolicy

FAST_429 = RetryPolicy(max_retries=8, base_delay_s=0.001, max_delay_s=0.005)
FAST_5XX = RetryPolicy(max_retries=3, base_delay_s=0.001, max_delay_s=0.005)


class FakeClock:
    """Deterministic time: sleeping advances `now` instantly and records the requested delay."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


Rule = Callable[[str, int], "int | str | httpx.Response"]


class FakeProvider:
    """Imitates POST /chat/completions.

    `rule(prompt, call_number)` decides each response: an HTTP status code,
    "timeout", "connect_error", "bad_json", or a ready-made httpx.Response.
    call_number starts at 1 for each prompt.
    """

    def __init__(self, rule: Rule | None = None, *, latency: float = 0.0, retry_after: str | None = None) -> None:
        self.rule: Rule = rule or (lambda prompt, n: 200)
        self.latency = latency
        self.retry_after = retry_after
        self.calls: Counter[str] = Counter()
        self.requests: list[dict[str, Any]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"body": body, "headers": dict(request.headers), "url": str(request.url)})
        prompt = body["messages"][-1]["content"]
        self.calls[prompt] += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.latency:
                await asyncio.sleep(self.latency)
            outcome = self.rule(prompt, self.calls[prompt])
        finally:
            self.in_flight -= 1
        if isinstance(outcome, httpx.Response):
            return outcome
        if outcome == "timeout":
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if outcome == "connect_error":
            raise httpx.ConnectError("simulated connection failure", request=request)
        if outcome == "bad_json":
            return httpx.Response(200, json={"unexpected": True})
        if outcome == 200:
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": f"echo: {prompt}"}}]})
        headers = {"Retry-After": self.retry_after} if outcome == 429 and self.retry_after is not None else {}
        return httpx.Response(int(outcome), json={"error": f"simulated {outcome}"}, headers=headers)

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def write_input(path: Path, rows: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def prompts(n: int, start: int = 0) -> list[dict[str, str]]:
    return [{"id": f"p{i}", "prompt": f"prompt number {i}"} for i in range(start, start + n)]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        inference_base_url="http://fake-llm/v1",
        inference_api_key="test-key",
        data_dir=tmp_path / "data",
        db_path=tmp_path / "var" / "test.db",
        chunk_size=10,
        workers=4,
        queue_max_chunks=2,
        result_queue_max=2,
        max_in_flight=8,
        rate_limit_policy=FAST_429,
        server_error_policy=FAST_5XX,
    )
