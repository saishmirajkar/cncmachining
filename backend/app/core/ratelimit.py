"""Fixed-window rate limiter. Uses Redis when reachable, otherwise a per-process in-memory window.

The in-memory fallback is only adequate for a single API process; production deployments use Redis.
"""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mem: dict[str, tuple[int, int]] = {}
        self._redis = None
        self._redis_checked = False

    def _client(self):
        if not self._redis_checked:
            self._redis_checked = True
            if get_settings().env == "test":
                return None
            try:
                import redis

                c = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5)
                c.ping()
                self._redis = c
            except Exception:  # pragma: no cover - depends on environment
                log.warning("rate_limiter_redis_unavailable_using_memory")
        return self._redis

    def hit(self, key: str, limit: int, window_s: int = 60) -> bool:
        window = int(time.time()) // window_s
        bucket = f"rl:{key}:{window}"
        client = self._client()
        if client is not None:
            n = client.incr(bucket)
            if n == 1:
                client.expire(bucket, window_s + 1)
            return n <= limit
        with self._lock:
            w, n = self._mem.get(key, (window, 0))
            if w != window:
                w, n = window, 0
            n += 1
            self._mem[key] = (w, n)
            return n <= limit

    def reset(self) -> None:
        with self._lock:
            self._mem.clear()


limiter = RateLimiter()


def rate_limit(scope: str, per_minute_setting: str):
    def dependency(request: Request) -> None:
        limit = getattr(get_settings(), per_minute_setting)
        ident = request.client.host if request.client else "unknown"
        if not limiter.hit(f"{scope}:{ident}", limit):
            raise HTTPException(status_code=429, detail="Rate limit exceeded; retry later")

    return dependency
