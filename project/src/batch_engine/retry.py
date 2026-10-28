"""Retry policies, error classification and back-off maths."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Callable

# A provider asking for longer than this is treated as asking for this long.
MAX_RETRY_AFTER_S = 300.0


class ErrorClass(StrEnum):
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    NON_RETRYABLE = "non_retryable"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    base_delay_s: float
    max_delay_s: float
    multiplier: float = 2.0

    def backoff(self, retry: int, rng: Callable[[], float] = random.random) -> float:
        """Full-jitter delay before retry number `retry` (0-based)."""
        ceiling = min(self.max_delay_s, self.base_delay_s * self.multiplier**retry)
        return rng() * ceiling

    def merged(self, **overrides: float | int | None) -> RetryPolicy:
        """Copy with any non-None overrides applied."""
        return replace(self, **{k: v for k, v in overrides.items() if v is not None})


def classify_status(status: int | None) -> ErrorClass:
    """Map an HTTP status (None = timeout / connection error) to a retry class."""
    if status == 429:
        return ErrorClass.RATE_LIMITED
    if status is None or status == 408 or status >= 500:
        return ErrorClass.SERVER_ERROR
    return ErrorClass.NON_RETRYABLE


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if math.isnan(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_S)


def retry_after_delay(seconds: float, rng: Callable[[], float] = random.random) -> float:
    """Honor Retry-After, plus up to 10% jitter so workers don't resume in lockstep."""
    return seconds * (1.0 + 0.1 * rng())
