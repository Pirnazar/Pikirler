"""Shared HTTP plumbing for AI/ASR providers: timeouts, retry with backoff."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from app.core.exceptions import ProviderUnavailable

T = TypeVar("T")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ProviderError(Exception):
    """A provider answered, but the answer is unusable (bad request, bad shape).
    Retrying the same input will not help."""


async def with_retries(
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    provider: str,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
) -> T:
    last: Exception | None = None
    for attempt in range(attempts + 1):
        try:
            return await call()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in RETRYABLE_STATUS:
                raise ProviderError(
                    f"{provider} rejected the request (HTTP {exc.response.status_code})"
                ) from exc
            last = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
        if attempt < attempts:
            delay = min(max_delay, base_delay * 2**attempt) * (0.5 + random.random())
            await asyncio.sleep(delay)
    kind = "timed out" if isinstance(last, httpx.TimeoutException) else "is unavailable"
    raise ProviderUnavailable(f"{provider} {kind}", details={"provider": provider}) from last


def bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}
