"""Completion webhook (extension). Best effort: a failed delivery never changes the job outcome."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import httpx

log = logging.getLogger(__name__)


async def send_webhook(
    http: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    *,
    attempts: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """POST the payload, retrying on 429/5xx/connection errors. Returns True on a 2xx."""
    for attempt in range(attempts):
        try:
            response = await http.post(url, json=payload)
            if response.is_success:
                return True
            if response.status_code != 429 and response.status_code < 500:
                log.warning("webhook %s rejected with HTTP %s", url, response.status_code)
                return False
        except httpx.HTTPError as exc:
            log.warning("webhook %s failed: %r", url, exc)
        if attempt < attempts - 1:
            await sleep(2**attempt)
    log.warning("webhook %s gave up after %d attempts", url, attempts)
    return False
