"""Sliding window rate limiter with Redis fallback."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException

from app.config import settings

try:
    import redis

    if settings.redis_url:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
        _redis.ping()
        _use_redis = True
    else:
        _redis = None
        _use_redis = False
except Exception:
    _redis = None
    _use_redis = False

_memory_windows: dict[str, deque] = defaultdict(deque)

WINDOW_SECONDS = 60


def _raise_limit(limit: int, window_seconds: int, retry_after: int) -> None:
    raise HTTPException(
        status_code=429,
        detail={
            "error": "Rate limit exceeded",
            "limit": limit,
            "window_seconds": window_seconds,
            "retry_after_seconds": retry_after,
        },
        headers={
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
            "Retry-After": str(retry_after),
        },
    )


def check_rate_limit(user_id: str) -> dict:
    """Check rate limit for a user id.

    Returns a dict with limit/remaining/reset_at.
    """
    now = time.time()
    limit = settings.rate_limit_per_minute

    if _use_redis and _redis is not None:
        key = f"rate:{user_id}"
        member = f"{now:.6f}-{uuid.uuid4().hex}"
        pipe = _redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - WINDOW_SECONDS)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, WINDOW_SECONDS + 5)
        _, _, count, _ = pipe.execute()

        if count > limit:
            _redis.zrem(key, member)
            oldest = _redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(oldest[0][1] + WINDOW_SECONDS - now) + 1
            else:
                retry_after = WINDOW_SECONDS
            _raise_limit(limit, WINDOW_SECONDS, retry_after)

        remaining = max(0, limit - count)
        reset_at = int(now) + WINDOW_SECONDS
        return {"limit": limit, "remaining": remaining, "reset_at": reset_at}

    window = _memory_windows[user_id]
    while window and window[0] < now - WINDOW_SECONDS:
        window.popleft()

    if len(window) >= limit:
        retry_after = int(window[0] + WINDOW_SECONDS - now) + 1
        _raise_limit(limit, WINDOW_SECONDS, retry_after)

    window.append(now)
    remaining = max(0, limit - len(window))
    reset_at = int(now) + WINDOW_SECONDS
    return {"limit": limit, "remaining": remaining, "reset_at": reset_at}
