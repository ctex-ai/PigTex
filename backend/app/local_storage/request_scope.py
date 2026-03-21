from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Iterable


LOCAL_DEVICE_SCOPE_HEADER = "X-PigTex-Device-Scope"
LOCAL_LEGACY_ACCOUNTS_HEADER = "X-PigTex-Legacy-Accounts"
LOCAL_DEVICE_SCOPE_PREFIX = "device-"

_REQUEST_DEVICE_SCOPE_ID: ContextVar[str | None] = ContextVar(
    "pigtex_request_device_scope_id",
    default=None,
)
_REQUEST_LEGACY_ACCOUNT_IDS: ContextVar[tuple[str, ...]] = ContextVar(
    "pigtex_request_legacy_account_ids",
    default=(),
)


def normalize_request_scope_id(value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    return candidate


def normalize_device_scope_id(value: str | None) -> str | None:
    candidate = normalize_request_scope_id(value)
    if not candidate:
        return None
    if not candidate.startswith(LOCAL_DEVICE_SCOPE_PREFIX):
        return None
    return candidate


def parse_legacy_account_ids_header(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()

    seen: set[str] = set()
    normalized: list[str] = []
    for raw_item in str(value).split(","):
        candidate = normalize_request_scope_id(raw_item)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)

    return tuple(normalized)


def bind_request_local_scope(
    device_scope_id: str | None,
    legacy_account_ids: Iterable[str] | None = None,
) -> tuple[Token[str | None], Token[tuple[str, ...]]]:
    normalized_legacy = tuple(
        item
        for item in (
            normalize_request_scope_id(raw_item)
            for raw_item in (legacy_account_ids or ())
        )
        if item
    )
    return (
        _REQUEST_DEVICE_SCOPE_ID.set(normalize_device_scope_id(device_scope_id)),
        _REQUEST_LEGACY_ACCOUNT_IDS.set(normalized_legacy),
    )


def reset_request_local_scope(tokens: tuple[Token[str | None], Token[tuple[str, ...]]]) -> None:
    device_token, legacy_token = tokens
    _REQUEST_DEVICE_SCOPE_ID.reset(device_token)
    _REQUEST_LEGACY_ACCOUNT_IDS.reset(legacy_token)


def get_request_device_scope_id() -> str | None:
    return _REQUEST_DEVICE_SCOPE_ID.get()


def get_request_legacy_account_ids() -> tuple[str, ...]:
    return _REQUEST_LEGACY_ACCOUNT_IDS.get()
