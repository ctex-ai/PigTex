from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Protocol, Tuple

from fastapi import Depends, HTTPException, Request, Response, status

from .config import get_settings
from .models import User
from .routes.auth_utils import get_current_user

try:
    import redis
except Exception:  # pragma: no cover - optional dependency at runtime
    redis = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(frozen=True)
class RateLimitSpec:
    name: str
    limit: int
    window_seconds: int


class RateLimiterBackend(Protocol):
    def check(self, bucket_key: str, spec: RateLimitSpec) -> Tuple[bool, int, int, int]:
        """
        Returns:
        - allowed
        - remaining requests in current window
        - retry_after (seconds)
        - reset_after (seconds)
        """


class InMemorySlidingWindowLimiter:
    """
    Lightweight in-memory sliding-window limiter.
    Suitable for single-process deployments.
    """

    def __init__(self) -> None:
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, events: Deque[float], now: float, window_seconds: int) -> None:
        cutoff = now - window_seconds
        while events and events[0] <= cutoff:
            events.popleft()

    def check(self, bucket_key: str, spec: RateLimitSpec) -> Tuple[bool, int, int, int]:
        now = time.monotonic()
        with self._lock:
            events = self._buckets.setdefault(bucket_key, deque())
            self._prune(events, now, spec.window_seconds)

            if len(events) >= spec.limit:
                oldest = events[0]
                retry_after = max(1, int(oldest + spec.window_seconds - now))
                return False, 0, retry_after, retry_after

            events.append(now)
            remaining = max(0, spec.limit - len(events))
            if events:
                reset_after = max(1, int(events[0] + spec.window_seconds - now))
            else:
                reset_after = spec.window_seconds
            return True, remaining, 0, reset_after


class RedisSlidingWindowLimiter:
    """
    Redis-backed sliding-window limiter for multi-instance deployments.
    Uses ZSET + Lua for atomicity.
    """

    _CHECK_SCRIPT = """
    local key = KEYS[1]
    local now_ms = tonumber(ARGV[1])
    local window_ms = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local member = ARGV[4]

    local cutoff = now_ms - window_ms
    redis.call("ZREMRANGEBYSCORE", key, "-inf", cutoff)

    local current = redis.call("ZCARD", key)
    if current >= limit then
      local oldest = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
      local retry_ms = window_ms
      if oldest[2] then
        retry_ms = math.max(1, math.ceil(oldest[2] + window_ms - now_ms))
      end
      redis.call("PEXPIRE", key, window_ms)
      return {0, current, retry_ms, retry_ms}
    end

    redis.call("ZADD", key, now_ms, member)
    redis.call("PEXPIRE", key, window_ms)

    local new_count = redis.call("ZCARD", key)
    local oldest2 = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
    local reset_ms = window_ms
    if oldest2[2] then
      reset_ms = math.max(1, math.ceil(oldest2[2] + window_ms - now_ms))
    end

    return {1, new_count, 0, reset_ms}
    """

    def __init__(self, redis_url: str, key_prefix: str, socket_timeout_seconds: float = 1.0):
        if redis is None:
            raise RuntimeError("redis package is not installed")
        self._prefix = key_prefix.rstrip(":")
        self._client = redis.Redis.from_url(  # type: ignore[attr-defined]
            redis_url,
            decode_responses=False,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=socket_timeout_seconds,
            retry_on_timeout=True,
        )
        self._script = self._client.register_script(self._CHECK_SCRIPT)

    def _key(self, bucket_key: str) -> str:
        return f"{self._prefix}:{bucket_key}"

    @staticmethod
    def _ms_to_seconds(value_ms: int) -> int:
        return max(1, int((value_ms + 999) / 1000))

    def check(self, bucket_key: str, spec: RateLimitSpec) -> Tuple[bool, int, int, int]:
        now_ms = int(time.time() * 1000)
        window_ms = max(1000, spec.window_seconds * 1000)
        member = f"{now_ms}:{uuid.uuid4().hex}"
        key = self._key(bucket_key)

        result = self._script(
            keys=[key],
            args=[now_ms, window_ms, spec.limit, member],
        )
        allowed_flag, count_used, retry_ms, reset_ms = [int(v) for v in result]
        allowed = allowed_flag == 1
        remaining = max(0, spec.limit - count_used)
        retry_after = self._ms_to_seconds(retry_ms)
        reset_after = self._ms_to_seconds(reset_ms)
        return allowed, remaining, retry_after, reset_after


def _build_limiter_backend() -> RateLimiterBackend:
    backend = (settings.rate_limit_backend or "memory").strip().lower()
    if backend != "redis":
        logger.info("Rate limiter backend: memory")
        return InMemorySlidingWindowLimiter()

    if not (settings.redis_url or "").strip():
        logger.warning("Rate limiter backend is redis but REDIS_URL is empty; fallback to memory")
        return InMemorySlidingWindowLimiter()

    try:
        redis_limiter = RedisSlidingWindowLimiter(
            redis_url=settings.redis_url,
            key_prefix=settings.rate_limit_key_prefix or "pigtex:ratelimit",
            socket_timeout_seconds=max(0.1, settings.rate_limit_redis_socket_timeout_seconds),
        )
        redis_limiter._client.ping()
        logger.info("Rate limiter backend: redis")
        return redis_limiter
    except Exception as exc:
        logger.warning("Failed to initialize redis rate limiter; fallback to memory: %s", exc)
        return InMemorySlidingWindowLimiter()


AUTH_LOGIN_SPEC = RateLimitSpec(
    name="auth_login",
    limit=max(1, settings.rate_limit_auth_login_per_minute),
    window_seconds=60,
)
AUTH_REGISTER_SPEC = RateLimitSpec(
    name="auth_register",
    limit=max(1, settings.rate_limit_auth_register_per_minute),
    window_seconds=60,
)
V1_SPEC = RateLimitSpec(
    name="v1",
    limit=max(1, settings.rate_limit_v1_per_minute),
    window_seconds=60,
)
PROXY_SPEC = RateLimitSpec(
    name="proxy",
    limit=max(1, settings.rate_limit_proxy_per_minute),
    window_seconds=60,
)

_backend = _build_limiter_backend()
_backend_lock = threading.Lock()


def _get_client_ip(request: Request) -> str:
    forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first

    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip

    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_with_backend(bucket_key: str, spec: RateLimitSpec) -> Tuple[bool, int, int, int]:
    global _backend

    try:
        return _backend.check(bucket_key, spec)
    except Exception as exc:
        logger.warning("Rate limiter backend error for %s: %s", bucket_key, exc)
        with _backend_lock:
            # Fail-open with automatic degradation to memory backend.
            if not isinstance(_backend, InMemorySlidingWindowLimiter):
                logger.warning("Degrading rate limiter backend to memory")
                _backend = InMemorySlidingWindowLimiter()
        return _backend.check(bucket_key, spec)


def _apply_rate_limit(
    spec: RateLimitSpec,
    principal: str,
    request: Request,
    response: Response,
) -> None:
    if not settings.enable_rate_limit:
        return

    bucket_key = f"{spec.name}:{principal}"
    allowed, remaining, retry_after, reset_after = _check_with_backend(bucket_key, spec)

    response.headers["X-RateLimit-Limit"] = str(spec.limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_after)

    if allowed:
        return

    logger.warning(
        "Rate limit exceeded spec=%s principal=%s path=%s",
        spec.name,
        principal,
        request.url.path,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": "rate_limited",
            "message": "Too many requests",
            "retry_after_seconds": retry_after,
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(spec.limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset_after),
        },
    )


async def auth_login_rate_limit(request: Request, response: Response) -> None:
    _apply_rate_limit(AUTH_LOGIN_SPEC, f"ip:{_get_client_ip(request)}", request, response)


async def auth_register_rate_limit(request: Request, response: Response) -> None:
    _apply_rate_limit(AUTH_REGISTER_SPEC, f"ip:{_get_client_ip(request)}", request, response)


async def v1_rate_limit(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> None:
    _apply_rate_limit(V1_SPEC, f"user:{current_user.id}", request, response)


async def proxy_rate_limit(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> None:
    _apply_rate_limit(PROXY_SPEC, f"user:{current_user.id}", request, response)
