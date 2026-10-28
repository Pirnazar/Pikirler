"""Redis connection shared by the rate limiter and the job queue."""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings

_redis: Redis[Any] | None = None


def get_redis() -> Redis[Any]:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def set_redis(client: Redis[Any] | None) -> None:
    global _redis
    _redis = client


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()  # type: ignore[attr-defined]
        _redis = None
