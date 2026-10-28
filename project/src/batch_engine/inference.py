"""Inference client: one prompt -> one chat-completions call, with per-class retry policies."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from .retry import ErrorClass, RetryPolicy, classify_status, parse_retry_after, retry_after_delay

log = logging.getLogger(__name__)

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class RateLimitGate:
    """Process-wide cool-down: after a 429 with Retry-After, every request waits until it expires."""

    def __init__(self, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep) -> None:
        self._clock = clock
        self._sleep = sleep
        self._until = 0.0

    def pause(self, seconds: float) -> None:
        self._until = max(self._until, self._clock() + seconds)

    @property
    def remaining(self) -> float:
        return max(0.0, self._until - self._clock())

    async def wait(self) -> None:
        while (remaining := self.remaining) > 0:
            await self._sleep(remaining)


@dataclass(slots=True)
class InferenceOutcome:
    ok: bool
    response: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    http_status: int | None = None
    attempts: int = 0
    latency_ms: int = 0
    retries_429: int = 0
    retries_5xx: int = 0


class InferenceClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        model: str,
        max_tokens: int,
        limiter: asyncio.Semaphore,
        gate: RateLimitGate,
        sleep: Sleep = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        clock: Clock = time.monotonic,
    ) -> None:
        self._http = http
        self.model = model
        self._max_tokens = max_tokens
        self._limiter = limiter
        self._gate = gate
        self._sleep = sleep
        self._rng = rng
        self._clock = clock

    async def complete(
        self,
        prompt: str,
        *,
        rate_limit: RetryPolicy,
        server_error: RetryPolicy,
        model: str | None = None,
    ) -> InferenceOutcome:
        payload = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_tokens,
        }
        policies = {ErrorClass.RATE_LIMITED: rate_limit, ErrorClass.SERVER_ERROR: server_error}
        retries = {ErrorClass.RATE_LIMITED: 0, ErrorClass.SERVER_ERROR: 0}
        attempts = 0
        started = self._clock()

        def outcome(**kwargs: object) -> InferenceOutcome:
            return InferenceOutcome(
                attempts=attempts,
                latency_ms=int((self._clock() - started) * 1000),
                retries_429=retries[ErrorClass.RATE_LIMITED],
                retries_5xx=retries[ErrorClass.SERVER_ERROR],
                **kwargs,  # type: ignore[arg-type]
            )

        while True:
            await self._gate.wait()
            response: httpx.Response | None = None
            transport_error = ""
            # The slot is held only for the HTTP call, never during back-off sleeps.
            async with self._limiter:
                attempts += 1
                try:
                    response = await self._http.post("/chat/completions", json=payload)
                except httpx.TimeoutException as exc:
                    transport_error = f"timeout: {exc!r}"
                except httpx.TransportError as exc:
                    transport_error = f"connection error: {exc!r}"

            status = response.status_code if response is not None else None
            if response is not None and response.is_success:
                try:
                    text = response.json()["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    return outcome(ok=False, error_type="bad_response", http_status=status,
                                   error_message=f"unexpected response body: {exc!r}")
                return outcome(ok=True, response=text, http_status=status)

            error_class = classify_status(status)
            message = transport_error or f"HTTP {status}: {_snippet(response)}"
            if error_class is ErrorClass.NON_RETRYABLE:
                return outcome(ok=False, error_type=error_class.value, http_status=status, error_message=message)

            policy = policies[error_class]
            if retries[error_class] >= policy.max_retries:
                return outcome(ok=False, error_type=error_class.value, http_status=status, error_message=message)

            retry_after = parse_retry_after(response.headers.get("Retry-After")) if response is not None else None
            if retry_after is not None:
                delay = retry_after_delay(retry_after, self._rng)
                if error_class is ErrorClass.RATE_LIMITED:
                    self._gate.pause(delay)
            else:
                delay = policy.backoff(retries[error_class], self._rng)
            retries[error_class] += 1
            log.debug("retrying after %s (%s), sleeping %.2fs", status, error_class, delay)
            await self._sleep(delay)


def _snippet(response: httpx.Response | None, limit: int = 200) -> str:
    if response is None:
        return ""
    try:
        return response.text[:limit]
    except Exception:  # undecodable body
        return "<unreadable body>"
