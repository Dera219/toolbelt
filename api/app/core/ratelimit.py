"""Rate limiting.

Fixed-window counters behind a provider seam: an in-process limiter for dev and
tests, Redis for production (multiple API workers must share one counter, or the
limit is silently multiplied by the worker count).

Used as a FastAPI dependency:

    @router.post("/auth/login", dependencies=[Depends(RateLimit("login", 10, 60))])
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings


class RateLimitBackend(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Count one request. Returns (allowed, seconds_until_reset)."""
        ...


class InMemoryRateLimiter:
    """Per-process counters. Correct for a single worker; tests reset it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            count, expires_at = self._buckets.get(key, (0, now + window_seconds))
            if now >= expires_at:
                count, expires_at = 0, now + window_seconds
            count += 1
            self._buckets[key] = (count, expires_at)
            return count <= limit, max(1, int(expires_at - now))

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class RedisRateLimiter:
    """Shared counters across workers. INCR + EXPIRE is atomic enough here: the
    first hit in a window sets the TTL, so a window can never outlive its key."""

    def __init__(self, url: str) -> None:
        import redis  # imported lazily so dev/test never need the dependency

        self._redis = redis.Redis.from_url(url, decode_responses=True)

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if ttl < 0:  # key had no expiry (first hit, or a race) — set one
            self._redis.expire(key, window_seconds)
            ttl = window_seconds
        return count <= limit, max(1, int(ttl))


_memory_limiter = InMemoryRateLimiter()
_redis_limiter: RedisRateLimiter | None = None


def get_rate_limiter() -> RateLimitBackend:
    global _redis_limiter
    settings = get_settings()
    if settings.redis_url:
        if _redis_limiter is None:
            _redis_limiter = RedisRateLimiter(settings.redis_url)
        return _redis_limiter
    return _memory_limiter


def reset_rate_limits() -> None:
    """Test helper — clears in-process counters between cases."""
    _memory_limiter.reset()


def _client_ip(request: Request) -> str:
    # Behind a proxy, the first X-Forwarded-For entry is the real client. Only
    # trust it when a proxy is actually in front of us.
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimit:
    """Dependency factory. Keys by authenticated user when a bearer token is
    present, otherwise by client IP."""

    def __init__(self, scope: str, limit: int, window_seconds: int) -> None:
        self.scope = scope
        self.limit = limit
        self.window_seconds = window_seconds

    def __call__(self, request: Request) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        auth = request.headers.get("authorization", "")
        # The token itself identifies the caller without a DB lookup here; the
        # endpoint's own auth dependency still validates it.
        identity = auth[-32:] if auth.startswith("Bearer ") else _client_ip(request)
        key = f"rl:{self.scope}:{identity}"
        allowed, retry_after = get_rate_limiter().hit(key, self.limit, self.window_seconds)
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests — slow down and try again shortly",
                headers={"Retry-After": str(retry_after)},
            )


def rate_limit(scope: str, limit: int, window_seconds: int):
    """Convenience wrapper so routers read as Depends(rate_limit(...))."""
    return Depends(RateLimit(scope, limit, window_seconds))
