"""Answer cache for the public research endpoints (P2-FR-07).

``RedisAnswerCache`` is shared by every instance pointing at the same Redis, so a repeated question
is answered from cache whichever instance receives it. Without REDIS_URL, or while Redis errors,
``InMemoryAnswerCache`` (per process, LRU-bounded) is used instead - a cache miss is never an error.

Keys are hashed: the question text is never stored in Redis in the clear as part of a key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Optional

log = logging.getLogger("contexta.cache")


def cache_key(parts: tuple) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, default=str).encode()).hexdigest()


class InMemoryAnswerCache:
    backend = "memory"

    def __init__(self, ttl: int, max_items: int = 256):
        self.ttl = ttl
        self.max_items = max_items
        self._items: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    async def get(self, key: str) -> Optional[dict]:
        hit = self._items.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            self._items.move_to_end(key)
            return hit[1]
        return None

    async def set(self, key: str, value: dict) -> None:
        if self.ttl <= 0:
            return
        self._items[key] = (time.monotonic(), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)


class RedisAnswerCache:
    backend = "redis"

    def __init__(self, redis_client, ttl: int, prefix: str = "contexta:answer:"):
        self.redis = redis_client
        self.ttl = ttl
        self.prefix = prefix
        self._fallback = InMemoryAnswerCache(ttl)

    def _warn(self, op: str, exc: Exception) -> None:
        log.warning(
            "Redis answer cache %s failed (%s); using in-memory cache",
            op,
            type(exc).__name__,
            extra={"event": "cache.redis", "status": "error", "category": "internal"},
        )

    async def get(self, key: str) -> Optional[dict]:
        try:
            raw = await self.redis.get(self.prefix + key)
        except Exception as exc:  # noqa: BLE001 - Redis is an optimisation, never a dependency
            self._warn("get", exc)
            return await self._fallback.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: dict) -> None:
        if self.ttl <= 0:
            return
        try:
            await self.redis.set(self.prefix + key, json.dumps(value), ex=self.ttl)
        except Exception as exc:  # noqa: BLE001
            self._warn("set", exc)
            await self._fallback.set(key, value)


def make_answer_cache(redis_client, ttl: int):
    return RedisAnswerCache(redis_client, ttl) if redis_client is not None else InMemoryAnswerCache(ttl)
