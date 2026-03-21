from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Message

from .config import get_settings

try:
    import redis
except Exception:  # pragma: no cover - optional dependency at runtime
    redis = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
settings = get_settings()

IDEMPOTENCY_HEADER = "Idempotency-Key"
_KEY_RE = re.compile(r"^[A-Za-z0-9:_-]{8,128}$")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class IdempotencyRecord:
    fingerprint: str
    status_code: int
    headers: Dict[str, str]
    body: bytes


class IdempotencyStore(Protocol):
    def get(self, scope_key: str, idem_key: str) -> Optional[IdempotencyRecord]:
        ...

    def set(self, scope_key: str, idem_key: str, record: IdempotencyRecord, ttl_seconds: int) -> None:
        ...

    def acquire_lock(self, scope_key: str, idem_key: str, ttl_seconds: int) -> bool:
        ...

    def release_lock(self, scope_key: str, idem_key: str) -> None:
        ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._records: Dict[str, Tuple[float, IdempotencyRecord]] = {}
        self._locks: Dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _record_key(scope_key: str, idem_key: str) -> str:
        return f"{scope_key}:{idem_key}"

    @staticmethod
    def _lock_key(scope_key: str, idem_key: str) -> str:
        return f"{scope_key}:{idem_key}:lock"

    def _cleanup(self, now: float) -> None:
        expired_records = [k for k, (exp, _) in self._records.items() if exp <= now]
        for key in expired_records:
            self._records.pop(key, None)

        expired_locks = [k for k, exp in self._locks.items() if exp <= now]
        for key in expired_locks:
            self._locks.pop(key, None)

    def get(self, scope_key: str, idem_key: str) -> Optional[IdempotencyRecord]:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            key = self._record_key(scope_key, idem_key)
            payload = self._records.get(key)
            if not payload:
                return None
            _, record = payload
            return record

    def set(self, scope_key: str, idem_key: str, record: IdempotencyRecord, ttl_seconds: int) -> None:
        now = time.time()
        expiry = now + max(1, ttl_seconds)
        with self._lock:
            self._cleanup(now)
            self._records[self._record_key(scope_key, idem_key)] = (expiry, record)

    def acquire_lock(self, scope_key: str, idem_key: str, ttl_seconds: int) -> bool:
        now = time.time()
        expiry = now + max(1, ttl_seconds)
        lock_key = self._lock_key(scope_key, idem_key)
        with self._lock:
            self._cleanup(now)
            if lock_key in self._locks:
                return False
            self._locks[lock_key] = expiry
            return True

    def release_lock(self, scope_key: str, idem_key: str) -> None:
        with self._lock:
            self._locks.pop(self._lock_key(scope_key, idem_key), None)


class RedisIdempotencyStore:
    def __init__(self, redis_url: str, prefix: str, socket_timeout_seconds: float = 1.0):
        if redis is None:
            raise RuntimeError("redis package is not installed")
        self._prefix = prefix.rstrip(":")
        self._client = redis.Redis.from_url(  # type: ignore[attr-defined]
            redis_url,
            decode_responses=False,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=socket_timeout_seconds,
            retry_on_timeout=True,
        )

    def ping(self) -> None:
        self._client.ping()

    def _record_key(self, scope_key: str, idem_key: str) -> str:
        return f"{self._prefix}:record:{scope_key}:{idem_key}"

    def _lock_key(self, scope_key: str, idem_key: str) -> str:
        return f"{self._prefix}:lock:{scope_key}:{idem_key}"

    def get(self, scope_key: str, idem_key: str) -> Optional[IdempotencyRecord]:
        raw = self._client.get(self._record_key(scope_key, idem_key))
        if not raw:
            return None
        payload = json.loads(raw.decode("utf-8"))
        body = base64.b64decode(payload["body_b64"].encode("ascii"))
        return IdempotencyRecord(
            fingerprint=str(payload["fingerprint"]),
            status_code=int(payload["status_code"]),
            headers={str(k): str(v) for k, v in dict(payload.get("headers", {})).items()},
            body=body,
        )

    def set(self, scope_key: str, idem_key: str, record: IdempotencyRecord, ttl_seconds: int) -> None:
        payload = {
            "fingerprint": record.fingerprint,
            "status_code": record.status_code,
            "headers": record.headers,
            "body_b64": base64.b64encode(record.body).decode("ascii"),
        }
        key = self._record_key(scope_key, idem_key)
        self._client.setex(key, max(1, ttl_seconds), json.dumps(payload, separators=(",", ":")))

    def acquire_lock(self, scope_key: str, idem_key: str, ttl_seconds: int) -> bool:
        key = self._lock_key(scope_key, idem_key)
        lock_value = str(time.time())
        return bool(
            self._client.set(
                key,
                lock_value.encode("utf-8"),
                ex=max(1, ttl_seconds),
                nx=True,
            )
        )

    def release_lock(self, scope_key: str, idem_key: str) -> None:
        self._client.delete(self._lock_key(scope_key, idem_key))


def _build_store() -> IdempotencyStore:
    backend = (settings.idempotency_backend or "memory").strip().lower()
    if backend != "redis":
        logger.info("Idempotency backend: memory")
        return InMemoryIdempotencyStore()

    if not (settings.redis_url or "").strip():
        logger.warning("Idempotency backend is redis but REDIS_URL is empty; fallback to memory")
        return InMemoryIdempotencyStore()

    try:
        store = RedisIdempotencyStore(
            redis_url=settings.redis_url,
            prefix=settings.idempotency_key_prefix or "pigtex:idempotency",
            socket_timeout_seconds=max(0.1, settings.rate_limit_redis_socket_timeout_seconds),
        )
        store.ping()
        logger.info("Idempotency backend: redis")
        return store
    except Exception as exc:
        logger.warning("Failed to initialize redis idempotency backend; fallback to memory: %s", exc)
        return InMemoryIdempotencyStore()


_store: IdempotencyStore = _build_store()
_store_state_lock = threading.Lock()


def _degrade_store_to_memory(reason: str, exc: Exception) -> None:
    global _store
    with _store_state_lock:
        if isinstance(_store, InMemoryIdempotencyStore):
            return
        logger.warning("Degrading idempotency backend to memory (%s): %s", reason, exc)
        _store = InMemoryIdempotencyStore()


def _is_idempotency_candidate(request: Request) -> bool:
    return request.method.upper() in _WRITE_METHODS and request.url.path.startswith("/api/")


def _normalize_idempotency_key(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is empty")
    if not _KEY_RE.fullmatch(value):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must be 8-128 chars of [A-Za-z0-9:_-]",
        )
    return value


def _request_scope_key(request: Request) -> str:
    auth_header = (request.headers.get("authorization") or "").strip()
    auth_hash = (
        hashlib.sha256(auth_header.encode("utf-8")).hexdigest()
        if auth_header
        else "anon"
    )
    scope_input = f"{request.method.upper()}|{request.url.path}|{request.url.query}|{auth_hash}"
    return hashlib.sha256(scope_input.encode("utf-8")).hexdigest()


def _request_fingerprint(request: Request, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    auth_header = (request.headers.get("authorization") or "").strip()
    auth_hash = (
        hashlib.sha256(auth_header.encode("utf-8")).hexdigest()
        if auth_header
        else "anon"
    )
    raw = f"{request.method.upper()}|{request.url.path}|{request.url.query}|{auth_hash}|{body_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clone_request_with_body(request: Request, body: bytes) -> Request:
    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(request.scope, receive)


def _is_streaming_response(response: Response) -> bool:
    content_type = (response.headers.get("content-type") or "").lower()
    return content_type.startswith("text/event-stream")


def _extract_cacheable_headers(response: Response) -> Dict[str, str]:
    headers = {}
    for key, value in response.headers.items():
        lower = key.lower()
        if lower in {"content-length", "date", "server"}:
            continue
        headers[key] = value
    return headers


def _build_replay_response(record: IdempotencyRecord) -> Response:
    headers = dict(record.headers)
    headers["X-Idempotency-Replayed"] = "true"
    return Response(
        content=record.body,
        status_code=record.status_code,
        headers=headers,
    )


async def _materialize_response(response: Response) -> Tuple[Response, bytes]:
    body = b""
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            body += chunk
        else:
            body += str(chunk).encode("utf-8")

    headers = dict(response.headers)
    headers.pop("content-length", None)
    rebuilt = Response(
        content=body,
        status_code=response.status_code,
        headers=headers,
        media_type=response.media_type,
        background=response.background,
    )
    return rebuilt, body


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.enable_idempotency:
            return await call_next(request)

        if not _is_idempotency_candidate(request):
            return await call_next(request)

        raw_key = request.headers.get(IDEMPOTENCY_HEADER)
        if raw_key is None:
            return await call_next(request)
        try:
            idem_key = _normalize_idempotency_key(raw_key)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers or {},
            )

        body = await request.body()
        request = _clone_request_with_body(request, body)

        scope_key = _request_scope_key(request)
        fingerprint = _request_fingerprint(request, body)
        store = _store

        try:
            record = store.get(scope_key, idem_key)
        except Exception as exc:
            logger.warning("Idempotency store get failed: %s", exc)
            _degrade_store_to_memory("get", exc)
            store = _store
            record = store.get(scope_key, idem_key)

        if record:
            if record.fingerprint != fingerprint:
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={
                        "detail": {
                            "error": "idempotency_key_reused_with_different_request",
                            "message": "Idempotency-Key already used with a different request payload",
                        }
                    },
                )
            return _build_replay_response(record)

        try:
            lock_acquired = store.acquire_lock(
                scope_key,
                idem_key,
                max(1, settings.idempotency_lock_ttl_seconds),
            )
        except Exception as exc:
            logger.warning("Idempotency lock acquire failed: %s", exc)
            _degrade_store_to_memory("acquire_lock", exc)
            store = _store
            lock_acquired = store.acquire_lock(
                scope_key,
                idem_key,
                max(1, settings.idempotency_lock_ttl_seconds),
            )

        if not lock_acquired:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "detail": {
                        "error": "idempotency_request_in_progress",
                        "message": "Another request with the same Idempotency-Key is in progress",
                    }
                },
            )

        try:
            response = await call_next(request)

            if _is_streaming_response(response):
                response.headers["X-Idempotency-Status"] = "skipped_streaming_response"
                return response

            buffered_response, response_body = await _materialize_response(response)
            if (
                200 <= buffered_response.status_code < 300
                and len(response_body) <= max(1, settings.idempotency_max_cached_response_bytes)
            ):
                record = IdempotencyRecord(
                    fingerprint=fingerprint,
                    status_code=buffered_response.status_code,
                    headers=_extract_cacheable_headers(buffered_response),
                    body=response_body,
                )
                try:
                    store.set(
                        scope_key,
                        idem_key,
                        record,
                        max(1, settings.idempotency_ttl_seconds),
                    )
                    buffered_response.headers["X-Idempotency-Status"] = "stored"
                except Exception as exc:
                    logger.warning("Idempotency store set failed: %s", exc)
                    _degrade_store_to_memory("set", exc)
                    buffered_response.headers["X-Idempotency-Status"] = "store_error"
            else:
                buffered_response.headers["X-Idempotency-Status"] = "not_cached"

            return buffered_response
        finally:
            try:
                store.release_lock(scope_key, idem_key)
            except Exception as exc:
                logger.warning("Idempotency lock release failed: %s", exc)
