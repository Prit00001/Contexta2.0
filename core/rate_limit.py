"""Rate limiting for the public ``/ask`` endpoints and the workspace endpoints.

Redis when REDIS_URL is set - required so limits hold across multiple API instances, unlike
in-memory state (P2-FR-07). Without REDIS_URL, or while Redis is unreachable, an in-memory
limiter is used, so the app still runs - same degrade-gracefully approach as the rest of the
codebase (fastembed's hash fallback, the generator's extractive fallback).

The Redis path is a fixed window (INCR + EXPIRE), not a true sliding window. Upgrade path: a
small Lua script if burst-at-window-boundary abuse becomes a real problem.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Optional

log = logging.getLogger("contexta.ratelimit")

_MAX_TRACKED_KEYS = 5000


class InMemoryLimiter:
    """Sliding-window limiter per key, local to this process."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        if len(self._hits) > _MAX_TRACKED_KEYS:  # bound memory
            self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
        return True


class RedisLimiter:
    """Fixed-window limiter shared by every instance pointing at the same Redis.

    If Redis errors, the request is limited by a local in-memory window instead of failing:
    rate limiting protects provider quota, it must not take the API down with it.
    """

    def __init__(self, redis_client, per_minute: int, prefix: str = "contexta:ratelimit:"):
        self.redis = redis_client
        self.per_minute = per_minute
        self.prefix = prefix
        self._fallback = InMemoryLimiter(per_minute)

    async def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        redis_key = f"{self.prefix}{key}"
        try:
            count = await self.redis.incr(redis_key)
            if count == 1:
                await self.redis.expire(redis_key, 60)
        except Exception as exc:  # noqa: BLE001 - any Redis failure degrades to the local window
            log.warning(
                "Redis rate limiter unavailable (%s); using in-memory limit",
                type(exc).__name__,
                extra={"event": "ratelimit.redis", "status": "error", "category": "internal"},
            )
            self._fallback.per_minute = self.per_minute
            return await self._fallback.allow(key)
        return count <= self.per_minute


def make_redis(redis_url: Optional[str]):
    """One asyncio Redis client per process (connections are pooled lazily), or None."""
    if not redis_url:
        return None
    import redis.asyncio as aioredis

    return aioredis.from_url(redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)


def make_limiter(redis_client, per_minute: int, prefix: str = "contexta:ratelimit:"):
    if redis_client is not None:
        return RedisLimiter(redis_client, per_minute, prefix)
    return InMemoryLimiter(per_minute)
