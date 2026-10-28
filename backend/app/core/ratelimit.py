"""Fixed-window rate limiting in Redis."""

from __future__ import annotations

from app.core.exceptions import RateLimited
from app.core.redis import get_redis


async def hit(bucket: str, limit: int, window_seconds: int) -> None:
    """Count one attempt in ``bucket``; raise once the window is exhausted."""
    redis = get_redis()
    key = f"pikirler:rl:{bucket}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = await pipe.execute()
    if int(count) > limit:
        ttl = await redis.ttl(key)
        retry_after = max(int(ttl), 1)
        raise RateLimited(
            "Too many attempts, try again later",
            details={"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


async def reset(bucket: str) -> None:
    await get_redis().delete(f"pikirler:rl:{bucket}")
