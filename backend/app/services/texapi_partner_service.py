from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status

from ..config import Settings, get_settings
from ..models import User

logger = logging.getLogger(__name__)


def _normalize_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class TexApiDelegatedToken:
    token: str
    expires_at: datetime | None
    token_id: str | None = None


class TexApiPartnerService:
    """Thin integration layer for PigTex <-> TexAPI partner APIs."""

    _token_cache: dict[str, TexApiDelegatedToken] = {}
    _locks: dict[str, asyncio.Lock] = {}

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def control_plane_base_url(self) -> str:
        return _normalize_url(self.settings.texapi_partner_control_plane_base_url)

    @property
    def gateway_base_url(self) -> str:
        return _normalize_url(self.settings.texapi_partner_gateway_base_url)

    @property
    def token_ttl_seconds(self) -> int:
        return max(60, int(self.settings.texapi_partner_token_ttl_seconds or 3600))

    @property
    def refresh_buffer_seconds(self) -> int:
        buffer_seconds = int(self.settings.texapi_partner_refresh_buffer_seconds or 600)
        return max(30, min(buffer_seconds, self.token_ttl_seconds - 1))

    @property
    def timeout_seconds(self) -> float:
        return max(3.0, float(self.settings.texapi_partner_timeout_seconds or 15.0))

    def is_enabled(self) -> bool:
        return (
            bool(self.settings.texapi_partner_enabled)
            and bool((self.settings.texapi_partner_client_id or "").strip())
            and bool((self.settings.texapi_partner_client_secret or "").strip())
            and bool(self.control_plane_base_url)
            and bool(self.gateway_base_url)
        )

    def build_external_customer_id(self, user_or_user_id: User | str) -> str:
        user_id = user_or_user_id.id if isinstance(user_or_user_id, User) else str(user_or_user_id)
        return f"pigtex:{str(user_id).strip()}"

    def is_managed_gateway_selected(self, base_url: str | None, *, api_key: str | None = None) -> bool:
        if not self.is_enabled():
            return False
        if (api_key or "").strip():
            return False
        return _normalize_url(base_url or "") == self.gateway_base_url

    async def upsert_customer(self, user: User) -> dict[str, Any]:
        if not self.is_enabled():
            return {"success": False, "disabled": True}

        payload = {
            "external_customer_id": self.build_external_customer_id(user),
            "display_name": (user.name or user.username or user.email or "").strip() or f"PigTex {user.id}",
            "metadata": {
                "pigtex_user_id": str(user.id),
                "pigtex_plan": (user.plan or "").strip() or "free",
            },
            "scopes": ["models:read", "chat:write", "messages:write"],
            "model_allowlist": None,
        }
        return await self._control_plane_request("PUT", "/customers/upsert", json=payload)

    async def get_delegated_token(
        self,
        user: User,
        *,
        force_refresh: bool = False,
    ) -> TexApiDelegatedToken:
        if not self.is_enabled():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_api_key_unavailable",
                    "message": "TexAPI partner integration is not configured.",
                },
            )

        cache_key = str(user.id)
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._token_cache.get(cache_key)
            if not force_refresh and self._is_cached_token_usable(cached):
                return cached

            # Customer provisioning is best-effort during auth, but required before mint.
            await self.upsert_customer(user)
            payload = {
                "external_customer_id": self.build_external_customer_id(user),
                "token_type": "DELEGATED",
                "label": f"PigTex delegated token for {user.id}",
                "scopes": ["models:read", "chat:write", "messages:write"],
                "ttl_seconds": self.token_ttl_seconds,
            }
            body = await self._control_plane_request("POST", "/tokens/mint", json=payload)
            token = self._parse_token_payload(body)
            self._token_cache[cache_key] = token
            return token

    async def get_usage(
        self,
        user: User,
        *,
        limit: int = 50,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "texapi_partner_disabled",
                    "message": "TexAPI partner integration is disabled.",
                },
            )

        params: dict[str, Any] = {
            "external_customer_id": self.build_external_customer_id(user),
            "limit": max(1, min(int(limit or 50), 200)),
        }
        if from_iso:
            params["from"] = from_iso
        if to_iso:
            params["to"] = to_iso
        return await self._control_plane_request("GET", "/usage", params=params)

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        *,
        timestamp: str | None,
        signature: str | None,
    ) -> None:
        secret = (self.settings.texapi_partner_webhook_signing_secret or "").strip()
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "texapi_partner_webhook_unconfigured",
                    "message": "TexAPI partner webhook signing secret is missing.",
                },
            )

        if not timestamp or not signature:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_webhook_signature",
                    "message": "Missing webhook signature headers.",
                },
            )

        try:
            parsed_ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_webhook_signature",
                    "message": "Invalid webhook timestamp.",
                },
            ) from exc

        if abs((_utcnow() - parsed_ts).total_seconds()) > 300:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_webhook_signature",
                    "message": "Webhook timestamp is outside the allowed replay window.",
                },
            )

        expected = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature.strip(), f"sha256={expected}"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_webhook_signature",
                    "message": "Webhook signature verification failed.",
                },
            )

    def handle_webhook_event(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("type") or "").strip()
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}

        external_customer_id = str(data.get("external_customer_id") or "").strip()
        user_id = self._user_id_from_external_customer_id(external_customer_id)
        if user_id and event_type in {"partner.token.revoked", "partner.budget.exceeded"}:
            self._token_cache.pop(user_id, None)

        logger.info(
            "texapi_partner_webhook_received type=%s external_customer_id=%s",
            event_type,
            external_customer_id,
        )

    def _user_id_from_external_customer_id(self, external_customer_id: str) -> str | None:
        prefix = "pigtex:"
        if not external_customer_id.startswith(prefix):
            return None
        user_id = external_customer_id[len(prefix):].strip()
        return user_id or None

    def _is_cached_token_usable(self, token: TexApiDelegatedToken | None) -> bool:
        if token is None or not token.token:
            return False
        if token.expires_at is None:
            return True
        return (token.expires_at - _utcnow()).total_seconds() > self.refresh_buffer_seconds

    def _parse_token_payload(self, payload: dict[str, Any]) -> TexApiDelegatedToken:
        token_block = payload.get("token")
        if isinstance(token_block, dict):
            token_value = (
                token_block.get("value")
                or token_block.get("token")
                or token_block.get("secret")
                or token_block.get("partner_token")
            )
            token_id = token_block.get("id")
            expires_at = token_block.get("expires_at")
        else:
            token_value = (
                payload.get("partner_token")
                or payload.get("token")
                or payload.get("value")
                or payload.get("secret")
            )
            token_id = payload.get("token_id")
            expires_at = payload.get("expires_at")

        token = str(token_value or "").strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_api_key_unavailable",
                    "message": "TexAPI partner mint response did not include a delegated token.",
                },
            )

        parsed_expires_at: datetime | None = None
        if isinstance(expires_at, str) and expires_at.strip():
            try:
                parsed_expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                parsed_expires_at = None

        return TexApiDelegatedToken(
            token=token,
            expires_at=parsed_expires_at,
            token_id=str(token_id).strip() or None,
        )

    async def _control_plane_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.control_plane_base_url}{path}"
        auth = httpx.BasicAuth(
            username=(self.settings.texapi_partner_client_id or "").strip(),
            password=(self.settings.texapi_partner_client_secret or "").strip(),
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(
                    method,
                    url,
                    auth=auth,
                    json=json,
                    params=params,
                )
        except httpx.RequestError as exc:
            logger.warning("TexAPI control plane request failed method=%s path=%s error=%s", method, path, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_api_key_unavailable",
                    "message": "Cannot reach TexAPI partner control plane.",
                },
            ) from exc

        if response.status_code >= 400:
            detail = self._extract_error_detail(response)
            logger.warning(
                "TexAPI control plane error method=%s path=%s status=%s detail=%s",
                method,
                path,
                response.status_code,
                detail,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_api_key_unavailable",
                    "message": detail or "TexAPI partner control plane returned an error.",
                },
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_api_key_unavailable",
                    "message": "TexAPI partner control plane returned invalid JSON.",
                },
            ) from exc

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_api_key_unavailable",
                    "message": "TexAPI partner control plane returned an unsupported payload.",
                },
            )
        return payload

    def _extract_error_detail(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return (response.text or "").strip() or f"HTTP {response.status_code}"

        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return f"HTTP {response.status_code}"
