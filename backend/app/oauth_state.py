from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Protocol

from .config import get_settings

try:
    import redis
except Exception:  # pragma: no cover - optional dependency at runtime
    redis = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
settings = get_settings()

OAuthStatePayload = Dict[str, Any]


class OAuthStateStoreUnavailableError(RuntimeError):
    """Raised when the configured OAuth state backend is required but unavailable."""


class OAuthStateStore(Protocol):
    def get(self, state: str) -> Optional[OAuthStatePayload]:
        ...

    def set(self, state: str, payload: OAuthStatePayload, ttl_seconds: int) -> None:
        ...

    def delete(self, state: str) -> None:
        ...


class InMemoryOAuthStateStore:
    def __init__(self) -> None:
        self._items: Dict[str, tuple[float, OAuthStatePayload]] = {}
        self._lock = threading.Lock()

    def _cleanup(self, now: float) -> None:
        expired_keys = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
        for key in expired_keys:
            self._items.pop(key, None)

    def get(self, state: str) -> Optional[OAuthStatePayload]:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            payload = self._items.get(state)
            if not payload:
                return None
            _, value = payload
            return dict(value)

    def set(self, state: str, payload: OAuthStatePayload, ttl_seconds: int) -> None:
        now = time.time()
        expiry = now + max(1, int(ttl_seconds))
        with self._lock:
            self._cleanup(now)
            self._items[state] = (expiry, dict(payload))

    def delete(self, state: str) -> None:
        with self._lock:
            self._items.pop(state, None)


class RedisOAuthStateStore:
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

    def ping(self) -> None:
        self._client.ping()

    def _key(self, state: str) -> str:
        return f"{self._prefix}:{state}"

    def get(self, state: str) -> Optional[OAuthStatePayload]:
        raw = self._client.get(self._key(state))
        if not raw:
            return None
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else None

    def set(self, state: str, payload: OAuthStatePayload, ttl_seconds: int) -> None:
        self._client.setex(
            self._key(state),
            max(1, int(ttl_seconds)),
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        )

    def delete(self, state: str) -> None:
        self._client.delete(self._key(state))


class UnavailableOAuthStateStore:
    def __init__(self, reason: str):
        self.reason = reason

    def get(self, state: str) -> Optional[OAuthStatePayload]:
        del state
        raise OAuthStateStoreUnavailableError(self.reason)

    def set(self, state: str, payload: OAuthStatePayload, ttl_seconds: int) -> None:
        del state, payload, ttl_seconds
        raise OAuthStateStoreUnavailableError(self.reason)

    def delete(self, state: str) -> None:
        del state
        raise OAuthStateStoreUnavailableError(self.reason)


def _oauth_providers_configured() -> bool:
    return bool(
        (
            settings.google_client_id.strip()
            and settings.google_client_secret.strip()
        )
        or (
            settings.github_client_id.strip()
            and settings.github_client_secret.strip()
        )
    )


def _resolve_backend_name() -> str:
    configured = (settings.oauth_state_backend or "auto").strip().lower() or "auto"
    if configured not in {"auto", "memory", "redis"}:
        logger.warning("Unknown oauth state backend '%s'; using auto", configured)
        configured = "auto"
    if configured == "auto":
        return "redis" if (settings.redis_url or "").strip() else "memory"
    return configured


def _is_fail_closed_required() -> bool:
    resolved_backend = _resolve_backend_name()
    configured_backend = (settings.oauth_state_backend or "auto").strip().lower() or "auto"
    if resolved_backend != "redis":
        return False
    if configured_backend == "redis":
        return True
    return settings.is_production and _oauth_providers_configured()


def _build_store() -> OAuthStateStore:
    backend = _resolve_backend_name()
    if backend != "redis":
        logger.info("OAuth state backend: memory")
        return InMemoryOAuthStateStore()

    if not (settings.redis_url or "").strip():
        reason = "OAuth state backend resolved to redis but REDIS_URL is empty"
        if _is_fail_closed_required():
            logger.error("%s; fail closed", reason)
            return UnavailableOAuthStateStore(reason)
        logger.warning("%s; fallback to memory", reason)
        return InMemoryOAuthStateStore()

    try:
        store = RedisOAuthStateStore(
            redis_url=settings.redis_url,
            key_prefix=settings.oauth_state_key_prefix or "pigtex:oauthstate",
            socket_timeout_seconds=max(0.1, settings.oauth_state_redis_socket_timeout_seconds),
        )
        store.ping()
        logger.info("OAuth state backend: redis")
        return store
    except Exception as exc:
        reason = f"Failed to initialize redis oauth state backend: {exc}"
        if _is_fail_closed_required():
            logger.error("%s; fail closed", reason)
            return UnavailableOAuthStateStore(reason)
        logger.warning("%s; fallback to memory", reason)
        return InMemoryOAuthStateStore()


_store: OAuthStateStore = _build_store()
_store_lock = threading.Lock()


def _degrade_store_to_memory(reason: str, exc: Exception) -> None:
    global _store
    if _is_fail_closed_required():
        raise OAuthStateStoreUnavailableError(
            f"OAuth state backend is unavailable during {reason}: {exc}"
        ) from exc
    with _store_lock:
        if isinstance(_store, InMemoryOAuthStateStore):
            return
        logger.warning("Degrading oauth state backend to memory (%s): %s", reason, exc)
        _store = InMemoryOAuthStateStore()


def get_oauth_state(state: str) -> Optional[OAuthStatePayload]:
    store = _store
    try:
        return store.get(state)
    except OAuthStateStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("OAuth state store get failed: %s", exc)
        _degrade_store_to_memory("get", exc)
        return _store.get(state)


def set_oauth_state(state: str, payload: OAuthStatePayload, ttl_seconds: int) -> None:
    store = _store
    try:
        store.set(state, payload, ttl_seconds)
    except OAuthStateStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("OAuth state store set failed: %s", exc)
        _degrade_store_to_memory("set", exc)
        _store.set(state, payload, ttl_seconds)


def delete_oauth_state(state: str) -> None:
    store = _store
    try:
        store.delete(state)
    except OAuthStateStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("OAuth state store delete failed: %s", exc)
        _degrade_store_to_memory("delete", exc)
        _store.delete(state)


def get_oauth_state_backend_status() -> dict[str, Any]:
    store = _store
    configured_backend = (settings.oauth_state_backend or "auto").strip().lower() or "auto"
    resolved_backend = _resolve_backend_name()
    fail_closed = _is_fail_closed_required()

    if isinstance(store, UnavailableOAuthStateStore):
        return {
            "configured_backend": configured_backend,
            "resolved_backend": resolved_backend,
            "active_backend": "unavailable",
            "healthy": False,
            "required": fail_closed,
            "detail": store.reason,
        }

    if isinstance(store, RedisOAuthStateStore):
        try:
            store.ping()
            return {
                "configured_backend": configured_backend,
                "resolved_backend": resolved_backend,
                "active_backend": "redis",
                "healthy": True,
                "required": fail_closed,
                "detail": "ok",
            }
        except Exception as exc:
            return {
                "configured_backend": configured_backend,
                "resolved_backend": resolved_backend,
                "active_backend": "redis",
                "healthy": False,
                "required": fail_closed,
                "detail": str(exc),
            }

    return {
        "configured_backend": configured_backend,
        "resolved_backend": resolved_backend,
        "active_backend": "memory",
        "healthy": True,
        "required": fail_closed,
        "detail": "ok",
    }


def ensure_oauth_state_ready() -> None:
    status = get_oauth_state_backend_status()
    if status["required"] and not status["healthy"]:
        raise OAuthStateStoreUnavailableError(str(status["detail"]))
