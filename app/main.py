"""
Production AI Agent — Full Day 12 implementation

Checklist:
  - Config from environment (12-factor)
  - JSON structured logging
  - API Key authentication
  - Rate limiting
  - Cost guard
  - Input validation
  - Health + readiness probes
  - Graceful shutdown
  - Security headers + CORS
  - Stateless history with Redis
"""
import time
import signal
import logging
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request, Response, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from app.config import settings
from app.auth import verify_api_key
from app.rate_limiter import check_rate_limit
from app.cost_guard import check_budget, record_usage, get_usage

from utils.mock_llm import ask as llm_ask

try:
    import redis

    if settings.redis_url:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
        _redis.ping()
        USE_REDIS = True
    else:
        _redis = None
        USE_REDIS = False
except Exception:
    _redis = None
    USE_REDIS = False

_memory_sessions: dict[str, list[dict[str, str]]] = {}


def _history_key(user_id: str) -> str:
    return f"history:{user_id}"


def load_history(user_id: str) -> list[dict[str, str]]:
    if USE_REDIS and _redis is not None:
        raw = _redis.lrange(_history_key(user_id), 0, -1)
        return [json.loads(item) for item in raw]
    return _memory_sessions.get(user_id, [])


def append_history(user_id: str, role: str, content: str) -> int:
    entry = {
        "role": role,
        "content": content,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if USE_REDIS and _redis is not None:
        key = _history_key(user_id)
        _redis.rpush(key, json.dumps(entry))
        _redis.ltrim(key, -20, -1)
        _redis.expire(key, 3600)
        return int(_redis.llen(key))
    history = _memory_sessions.get(user_id, [])
    history.append(entry)
    history = history[-20:]
    _memory_sessions[user_id] = history
    return len(history)


# ─────────────────────────────────────────────────────────
# Logging — JSON structured
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_request_count = 0
_error_count = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready
    logger.info(json.dumps({
        "event": "startup",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }))
    time.sleep(0.1)
    _is_ready = True
    logger.info(json.dumps({"event": "ready"}))

    yield

    _is_ready = False
    logger.info(json.dumps({"event": "shutdown"}))


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _request_count, _error_count
    start = time.time()
    _request_count += 1
    try:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if "server" in response.headers:
            del response.headers["server"]
        duration = round((time.time() - start) * 1000, 1)
        logger.info(json.dumps({
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": duration,
        }))
        return response
    except Exception:
        _error_count += 1
        raise


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          description="Your question for the agent")
    user_id: str | None = Field(default=None, description="Optional user id")


class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    timestamp: str
    user_id: str
    history_count: int
    storage: str


class RootResponse(BaseModel):
    app: str
    version: str
    environment: str
    endpoints: dict[str, str]


class HealthChecks(BaseModel):
    llm: str
    redis_connected: bool | str


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    uptime_seconds: float
    total_requests: int
    checks: HealthChecks
    timestamp: str


class ReadyResponse(BaseModel):
    ready: bool


class MetricsResponse(BaseModel):
    uptime_seconds: float
    total_requests: int
    error_count: int
    daily_cost_usd: float
    daily_budget_usd: float
    budget_used_pct: float


ROOT_EXAMPLE = {
    "app": "Production AI Agent API",
    "version": "1.0.0",
    "environment": "development",
    "endpoints": {
        "ask": "POST /ask (requires X-API-Key)",
        "health": "GET /health",
        "ready": "GET /ready",
    },
}

ASK_REQUEST_EXAMPLE = {
    "question": "What is this API used for?",
    "user_id": "demo-user-01",
}

ASK_RESPONSE_EXAMPLE = {
    "question": "What is this API used for?",
    "answer": "This API demonstrates a production-ready AI agent pattern.",
    "model": "mock-llm",
    "timestamp": "2026-04-18T09:30:00+00:00",
    "user_id": "demo-user-01",
    "history_count": 2,
    "storage": "memory",
}

HEALTH_EXAMPLE = {
    "status": "ok",
    "version": "1.0.0",
    "environment": "development",
    "uptime_seconds": 128.6,
    "total_requests": 42,
    "checks": {
        "llm": "mock",
        "redis_connected": "not_configured",
    },
    "timestamp": "2026-04-18T09:31:12+00:00",
}

READY_EXAMPLE = {"ready": True}

METRICS_EXAMPLE = {
    "uptime_seconds": 128.6,
    "total_requests": 42,
    "error_count": 1,
    "daily_cost_usd": 0.0024,
    "daily_budget_usd": 1.0,
    "budget_used_pct": 0.24,
}

ERROR_EXAMPLE = {"detail": "Unauthorized"}


@app.get(
    "/",
    tags=["Info"],
    summary="Service metadata",
    description="Returns API name, version, environment, and key endpoints.",
    response_model=RootResponse,
    responses={
        200: {
            "description": "Basic service information.",
            "content": {"application/json": {"example": ROOT_EXAMPLE}},
        }
    },
)
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "endpoints": {
            "ask": "POST /ask (requires X-API-Key)",
            "health": "GET /health",
            "ready": "GET /ready",
        },
    }


@app.post(
    "/ask",
    response_model=AskResponse,
    tags=["Agent"],
    summary="Ask the AI agent",
    description="Sends a question to the agent and returns a mocked answer.",
    responses={
        200: {
            "description": "Mocked AI answer.",
            "content": {"application/json": {"example": ASK_RESPONSE_EXAMPLE}},
        },
        401: {
            "description": "Missing or invalid API key.",
            "content": {"application/json": {"example": ERROR_EXAMPLE}},
        },
        429: {
            "description": "Rate limit or budget exceeded.",
            "content": {
                "application/json": {"example": {"detail": "Rate limit exceeded"}}
            },
        },
    },
)
async def ask_agent(
    request: Request,
    body: AskRequest = Body(..., example=ASK_REQUEST_EXAMPLE),
    api_key: str = Depends(verify_api_key),
):
    """Send a question to the AI agent. Requires X-API-Key header."""
    user_id = body.user_id or api_key[:8]

    check_rate_limit(user_id)
    check_budget(user_id)

    history_count = append_history(user_id, "user", body.question)

    logger.info(json.dumps({
        "event": "agent_call",
        "q_len": len(body.question),
        "user_id": user_id,
        "client": str(request.client.host) if request.client else "unknown",
    }))

    answer = llm_ask(body.question)
    history_count = append_history(user_id, "assistant", answer)

    input_tokens = len(body.question.split()) * 2
    output_tokens = len(answer.split()) * 2
    record_usage(user_id, input_tokens, output_tokens)

    return AskResponse(
        question=body.question,
        answer=answer,
        model=settings.llm_model,
        timestamp=datetime.now(timezone.utc).isoformat(),
        user_id=user_id,
        history_count=history_count,
        storage="redis" if USE_REDIS else "memory",
    )


@app.get(
    "/health",
    tags=["Operations"],
    summary="Health check",
    description="Liveness endpoint with dependency checks.",
    response_model=HealthResponse,
    responses={
        200: {
            "description": "Service is alive.",
            "content": {"application/json": {"example": HEALTH_EXAMPLE}},
        }
    },
)
def health():
    redis_ok = None
    if settings.redis_url:
        try:
            if _redis is None:
                redis_ok = False
            else:
                _redis.ping()
                redis_ok = True
        except Exception:
            redis_ok = False

    checks = {
        "llm": "mock" if not settings.openai_api_key else "openai",
        "redis_connected": redis_ok if settings.redis_url else "not_configured",
    }
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get(
    "/ready",
    tags=["Operations"],
    summary="Readiness check",
    description="Readiness probe used by deployment platforms.",
    response_model=ReadyResponse,
    responses={
        200: {
            "description": "Service can receive traffic.",
            "content": {"application/json": {"example": READY_EXAMPLE}},
        },
        503: {
            "description": "Service is not ready.",
            "content": {
                "application/json": {"example": {"detail": "Redis not available"}}
            },
        },
    },
)
def ready():
    if not _is_ready:
        raise HTTPException(503, "Not ready")
    if settings.redis_url:
        try:
            if _redis is None:
                raise RuntimeError("Redis not configured")
            _redis.ping()
        except Exception:
            raise HTTPException(503, "Redis not available")
    return {"ready": True}


@app.get(
    "/metrics",
    tags=["Operations"],
    summary="Runtime metrics",
    description="Returns request counters and budget usage for the current API key.",
    response_model=MetricsResponse,
    responses={
        200: {
            "description": "Current runtime and budget metrics.",
            "content": {"application/json": {"example": METRICS_EXAMPLE}},
        },
        401: {
            "description": "Missing or invalid API key.",
            "content": {"application/json": {"example": ERROR_EXAMPLE}},
        },
    },
)
def metrics(api_key: str = Depends(verify_api_key)):
    usage = get_usage(api_key[:8])
    return {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "error_count": _error_count,
        "daily_cost_usd": usage["cost_usd"],
        "daily_budget_usd": usage["budget_usd"],
        "budget_used_pct": usage["budget_used_pct"],
    }


def _handle_signal(signum, _frame):
    global _is_ready
    _is_ready = False
    logger.info(json.dumps({"event": "signal", "signum": signum}))


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} on {settings.host}:{settings.port}")
    logger.info(f"API Key: {settings.agent_api_key[:4]}****")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=30,
    )
