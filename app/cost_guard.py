"""Cost guard with Redis-backed storage."""
from __future__ import annotations

from datetime import datetime, timezone

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

_memory_costs: dict[str, float] = {}

INPUT_COST_PER_1K = 0.00015
OUTPUT_COST_PER_1K = 0.0006


def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _redis_key(user_id: str) -> str:
    return f"budget:{user_id}:{_day_key()}"


def _get_cost(user_id: str) -> float:
    if _use_redis and _redis is not None:
        value = _redis.get(_redis_key(user_id))
        return float(value or 0.0)
    return _memory_costs.get(f"{user_id}:{_day_key()}", 0.0)


def _set_cost(user_id: str, cost: float) -> None:
    if _use_redis and _redis is not None:
        key = _redis_key(user_id)
        _redis.set(key, cost)
        _redis.expire(key, 2 * 24 * 3600)
    else:
        _memory_costs[f"{user_id}:{_day_key()}"] = cost


def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1000) * INPUT_COST_PER_1K + (output_tokens / 1000) * OUTPUT_COST_PER_1K


def check_budget(user_id: str) -> None:
    """Raise 402 if user daily budget is exceeded."""
    current = _get_cost(user_id)
    if current >= settings.daily_budget_usd:
        raise HTTPException(status_code=402, detail="Daily budget exceeded")


def record_usage(user_id: str, input_tokens: int, output_tokens: int) -> dict:
    """Record usage cost and return current usage stats."""
    cost = calculate_cost(input_tokens, output_tokens)
    if _use_redis and _redis is not None:
        key = _redis_key(user_id)
        _redis.incrbyfloat(key, cost)
        _redis.expire(key, 2 * 24 * 3600)
    else:
        current = _get_cost(user_id)
        _set_cost(user_id, current + cost)

    usage = get_usage(user_id)
    usage["last_cost_usd"] = round(cost, 6)
    return usage


def get_usage(user_id: str) -> dict:
    """Return usage stats for the current day."""
    current = _get_cost(user_id)
    budget = settings.daily_budget_usd
    remaining = max(0.0, budget - current)
    used_pct = round((current / budget) * 100, 1) if budget > 0 else 0.0
    return {
        "date": _day_key(),
        "cost_usd": round(current, 6),
        "budget_usd": budget,
        "budget_remaining_usd": round(remaining, 6),
        "budget_used_pct": used_pct,
    }
