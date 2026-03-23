#!/usr/bin/env python3
"""
Production-grade full-system benchmark for PigTex.

Coverage:
1. Availability and health
2. Auth bootstrap and access control
3. BYOK model discovery
4. Non-stream chat contract and usage metadata
5. Streaming contract and latency
6. Memory continuity and temporary containment
7. Workspace and user isolation
8. Error semantics and fail-fast resilience
9. Concurrent stability under load

Competitive production gate:
- score_percent >= 90
- domain_pass_rate_percent >= 85
- critical_failures <= 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="backslashreplace")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(part: float, whole: float) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100.0, 2)


def percentile(values: List[float], q: float) -> float:
    if not values:
        return -1.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(ordered[lo])
    ratio = pos - lo
    return float(ordered[lo] * (1.0 - ratio) + ordered[hi] * ratio)


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def extract_content(payload: Any) -> str:
    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else None
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
                    if isinstance(content, list):
                        parts: List[str] = []
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                parts.append(str(part.get("text") or ""))
                        if parts:
                            return "".join(parts)
                delta = first.get("delta")
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str):
                        return content
        direct = payload.get("content")
        if isinstance(direct, str):
            return direct
    return ""


def mask_secret(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) <= 8:
        return "***"
    return f"{raw[:6]}...{raw[-4:]}"


@dataclass
class CheckResult:
    id: str
    name: str
    passed: bool
    weight: float
    critical: bool = False
    detail: str = ""
    expected: Optional[str] = None
    actual: Optional[str] = None
    skipped: bool = False


@dataclass
class DomainResult:
    id: str
    title: str
    critical: bool
    min_pass_ratio: float
    checks: List[CheckResult] = field(default_factory=list)
    score: float = 0.0
    max_score: float = 0.0
    score_percent: float = 0.0
    passed: bool = False
    critical_failed_checks: List[str] = field(default_factory=list)
    elapsed_ms: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UserSession:
    label: str
    email: str
    username: str
    password: str
    token: str = ""
    user_id: str = ""
    register_status: int = 0
    login_status: int = 0
    me_status: int = 0


class PigTexProductionBenchmark:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base_url = args.base_url.rstrip("/")
        self.root_url = f"{self.base_url}/"
        self.health_url = f"{self.base_url}/api/health"
        self.register_url = f"{self.base_url}/api/auth/register"
        self.login_url = f"{self.base_url}/api/auth/login"
        self.me_url = f"{self.base_url}/api/auth/me"
        self.models_url = f"{self.base_url}/api/v1/models"
        self.chat_url = f"{self.base_url}/api/v1/chat/completions"
        self.memory_stats_url = f"{self.base_url}/api/v1/memory/stats"
        self.memories_url = f"{self.base_url}/api/v1/memory/memories"
        self.memory_remember_url = f"{self.base_url}/api/v1/memory/remember"
        self.usage_url = f"{self.base_url}/api/user/usage"

        repo_root = Path(__file__).resolve().parents[3]
        default_output = repo_root / "ops" / "observability" / "reports" / "pigtex-production-full-latest.json"
        output_value = (args.output_json or "").strip()
        self.output_json = Path(output_value).expanduser().resolve() if output_value else default_output

        self.random = random.Random(args.random_seed)
        self.primary: Optional[UserSession] = None
        self.secondary: Optional[UserSession] = None
        self.models_payload: Dict[str, Any] = {}
        self.resolved_model: str = (args.model or "").strip() or "gpt-4o"
        self.primary_name_token: str = ""
        self.primary_name_conversation_id: str = ""
        self.notes: List[str] = []

    def _tag(self, prefix: str) -> str:
        return f"{prefix}-{self.random.randint(1000, 9999)}-{uuid.uuid4().hex[:6].upper()}"

    def _headers(
        self,
        *,
        token: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base_url: Optional[str] = None,
        api_provider: Optional[str] = None,
    ) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resolved_key = self.args.api_key if api_key is None else api_key
        resolved_base = self.args.api_base_url if api_base_url is None else api_base_url
        resolved_provider = self.args.api_provider if api_provider is None else api_provider

        if resolved_key:
            headers["X-API-Key"] = resolved_key
        if resolved_base:
            headers["X-API-Base-URL"] = resolved_base
        if resolved_provider:
            headers["X-API-Provider"] = resolved_provider
        return headers

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> tuple[Optional[httpx.Response], int, Optional[str]]:
        started = time.perf_counter()
        try:
            response = await client.request(method, url, **kwargs)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return response, elapsed_ms, None
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return None, elapsed_ms, str(exc)

    @staticmethod
    def _safe_json(response: Optional[httpx.Response]) -> Any:
        if response is None:
            return None
        try:
            return response.json()
        except Exception:
            return None

    def _payload_contains_token(self, payload: Any, token: str) -> bool:
        if not token:
            return False
        try:
            haystack = json.dumps(payload, ensure_ascii=False)
        except Exception:
            haystack = str(payload)
        return token.lower() in haystack.lower()

    @staticmethod
    def _latest_value_for_key(payload: Any, key: str) -> str:
        if not isinstance(payload, dict):
            return ""
        memories = payload.get("memories")
        if not isinstance(memories, list):
            return ""
        for item in memories:
            if not isinstance(item, dict):
                continue
            if str(item.get("key") or "").strip().lower() == key.strip().lower():
                return str(item.get("value") or item.get("object") or "").strip()
        return ""

    def _add_check(
        self,
        checks: List[CheckResult],
        *,
        check_id: str,
        name: str,
        passed: bool,
        weight: float,
        critical: bool = False,
        detail: str = "",
        expected: Optional[str] = None,
        actual: Optional[str] = None,
        skipped: bool = False,
    ) -> None:
        checks.append(
            CheckResult(
                id=check_id,
                name=name,
                passed=bool(passed),
                weight=0.0 if skipped else float(weight),
                critical=bool(critical),
                detail=detail,
                expected=expected,
                actual=actual,
                skipped=bool(skipped),
            )
        )

    def _finalize_domain(
        self,
        *,
        domain_id: str,
        title: str,
        critical: bool,
        min_pass_ratio: float,
        checks: List[CheckResult],
        elapsed_ms: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> DomainResult:
        max_score = sum(item.weight for item in checks if not item.skipped)
        score = sum(item.weight for item in checks if item.passed and not item.skipped)
        score_percent = pct(score, max_score)
        critical_failed_checks = [
            item.id for item in checks if item.critical and (not item.passed) and (not item.skipped)
        ]
        passed = (len(critical_failed_checks) == 0) and (
            (score / max_score) >= min_pass_ratio if max_score > 0 else False
        )
        return DomainResult(
            id=domain_id,
            title=title,
            critical=critical,
            min_pass_ratio=min_pass_ratio,
            checks=checks,
            score=round(score, 2),
            max_score=round(max_score, 2),
            score_percent=score_percent,
            passed=passed,
            critical_failed_checks=critical_failed_checks,
            elapsed_ms=elapsed_ms,
            details=details or {},
        )

    def _blocked_domain(self, domain_id: str, title: str, reason: str, weight: float) -> DomainResult:
        checks: List[CheckResult] = []
        self._add_check(
            checks,
            check_id=f"{domain_id}_BLOCKED",
            name="Prerequisites available",
            passed=False,
            weight=weight,
            critical=True,
            detail=reason,
            expected="required auth session and model are available",
            actual=reason,
        )
        return self._finalize_domain(
            domain_id=domain_id,
            title=title,
            critical=True,
            min_pass_ratio=1.0,
            checks=checks,
            elapsed_ms=0,
            details={"blocked_reason": reason},
        )

    def _pick_model(self, payload: Any) -> str:
        requested = (self.args.model or "").strip()
        candidates: List[Dict[str, Any]] = []
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            candidates = [item for item in payload.get("data") or [] if isinstance(item, dict)]

        candidate_ids = [str(item.get("id") or "").strip() for item in candidates if str(item.get("id") or "").strip()]
        if requested and requested.lower() != "auto":
            for candidate in candidate_ids:
                if candidate == requested:
                    return requested
            for candidate in candidate_ids:
                if candidate.lower() == requested.lower():
                    self.notes.append(f"Requested model '{requested}' resolved to '{candidate}' via case-insensitive match.")
                    return candidate

        for item in candidates:
            model_id = str(item.get("id") or "").strip()
            model_type = str(item.get("type") or "").strip().lower()
            if not model_id:
                continue
            if model_type in {"", "chat"} or bool(item.get("supports_streaming")):
                if requested and requested.lower() != "auto" and model_id != requested:
                    self.notes.append(f"Requested model '{requested}' not listed by upstream. Falling back to '{model_id}'.")
                return model_id

        if candidate_ids:
            model_id = candidate_ids[0]
            if requested and requested.lower() != "auto" and model_id != requested:
                self.notes.append(f"Requested model '{requested}' not listed by upstream. Falling back to '{model_id}'.")
            return model_id
        return requested or "gpt-4o"

    async def _bootstrap_user(self, client: httpx.AsyncClient, label: str) -> UserSession:
        user = UserSession(
            label=label,
            email=f"{label}.{uuid.uuid4().hex[:10]}@pigtex-bench.example.com",
            username=f"{label}_{uuid.uuid4().hex[:10]}",
            password=self.args.bench_password,
        )

        register_payload = {
            "email": user.email,
            "username": user.username,
            "password": user.password,
        }
        register_response, _, _ = await self._request(
            client,
            "POST",
            self.register_url,
            headers=self._headers(),
            json=register_payload,
            timeout=self.args.chat_timeout_seconds,
        )
        user.register_status = register_response.status_code if register_response is not None else 0

        login_response, _, _ = await self._request(
            client,
            "POST",
            self.login_url,
            headers=self._headers(),
            json={"email": user.email, "password": user.password},
            timeout=self.args.chat_timeout_seconds,
        )
        user.login_status = login_response.status_code if login_response is not None else 0
        login_payload = self._safe_json(login_response)
        if isinstance(login_payload, dict):
            user.token = str(login_payload.get("access_token") or "")

        if user.token:
            me_response, _, _ = await self._request(
                client,
                "GET",
                self.me_url,
                headers=self._headers(token=user.token),
                timeout=self.args.chat_timeout_seconds,
            )
            user.me_status = me_response.status_code if me_response is not None else 0
            me_payload = self._safe_json(me_response)
            if isinstance(me_payload, dict):
                user.user_id = str(me_payload.get("id") or "")

        return user

    async def _get_me(self, client: httpx.AsyncClient, token: str) -> Dict[str, Any]:
        response, elapsed_ms, error = await self._request(
            client,
            "GET",
            self.me_url,
            headers=self._headers(token=token),
            timeout=self.args.chat_timeout_seconds,
        )
        payload = self._safe_json(response)
        return {
            "status_code": response.status_code if response is not None else 0,
            "elapsed_ms": elapsed_ms,
            "error": error or "",
            "payload": payload if isinstance(payload, dict) else {},
        }

    async def _list_models(self, client: httpx.AsyncClient, token: str) -> Dict[str, Any]:
        response, elapsed_ms, error = await self._request(
            client,
            "GET",
            self.models_url,
            headers=self._headers(token=token),
            timeout=self.args.chat_timeout_seconds,
        )
        payload = self._safe_json(response)
        return {
            "status_code": response.status_code if response is not None else 0,
            "elapsed_ms": elapsed_ms,
            "error": error or "",
            "payload": payload if isinstance(payload, dict) else {},
            "body_preview": (response.text[:500] if response is not None else ""),
        }

    async def _get_usage(self, client: httpx.AsyncClient, token: str) -> Dict[str, Any]:
        response, elapsed_ms, error = await self._request(
            client,
            "GET",
            self.usage_url,
            headers=self._headers(token=token),
            timeout=self.args.chat_timeout_seconds,
        )
        payload = self._safe_json(response)
        return {
            "status_code": response.status_code if response is not None else 0,
            "elapsed_ms": elapsed_ms,
            "error": error or "",
            "payload": payload if isinstance(payload, dict) else {},
        }

    async def _memory_stats(self, client: httpx.AsyncClient, token: str) -> Dict[str, Any]:
        response, elapsed_ms, error = await self._request(
            client,
            "GET",
            self.memory_stats_url,
            headers=self._headers(token=token),
            timeout=self.args.chat_timeout_seconds,
        )
        payload = self._safe_json(response)
        return {
            "status_code": response.status_code if response is not None else 0,
            "elapsed_ms": elapsed_ms,
            "error": error or "",
            "payload": payload if isinstance(payload, dict) else {},
        }

    async def _list_memories(
        self,
        client: httpx.AsyncClient,
        *,
        token: str,
        scope: str = "all",
        workspace_id: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"scope": scope, "limit": limit}
        if workspace_id:
            params["workspace_id"] = workspace_id
        response, elapsed_ms, error = await self._request(
            client,
            "GET",
            self.memories_url,
            headers=self._headers(token=token),
            params=params,
            timeout=self.args.chat_timeout_seconds,
        )
        payload = self._safe_json(response)
        return {
            "status_code": response.status_code if response is not None else 0,
            "elapsed_ms": elapsed_ms,
            "error": error or "",
            "payload": payload if isinstance(payload, dict) else {},
            "body_preview": (response.text[:500] if response is not None else ""),
        }

    async def _remember(
        self,
        client: httpx.AsyncClient,
        *,
        token: str,
        content: str,
        predicate: str,
        category: str = "explicit_memory",
        subject: str = "User",
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "content": content,
            "predicate": predicate,
            "category": category,
            "subject": subject,
        }
        if workspace_id:
            params["workspace_id"] = workspace_id
        if conversation_id:
            params["conversation_id"] = conversation_id
        response, elapsed_ms, error = await self._request(
            client,
            "POST",
            self.memory_remember_url,
            headers=self._headers(token=token),
            params=params,
            timeout=self.args.chat_timeout_seconds,
        )
        payload = self._safe_json(response)
        return {
            "status_code": response.status_code if response is not None else 0,
            "elapsed_ms": elapsed_ms,
            "error": error or "",
            "payload": payload if isinstance(payload, dict) else {},
            "body_preview": (response.text[:500] if response is not None else ""),
        }

    async def _chat_nonstream(
        self,
        client: httpx.AsyncClient,
        *,
        token: str,
        message: str,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        use_memory: bool = True,
        use_history: bool = True,
        use_knowledge: bool = False,
        use_facts: bool = True,
        temperature: float = 0.0,
        max_tokens: int = 96,
        runtime_instruction: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base_url: Optional[str] = None,
        api_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.resolved_model,
            "messages": [{"role": "user", "content": message}],
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "use_memory": bool(use_memory),
            "use_history": bool(use_history),
            "use_knowledge": bool(use_knowledge),
            "use_facts": bool(use_facts),
            "use_web_search": False,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if workspace_id:
            payload["workspace_id"] = workspace_id
        if runtime_instruction:
            payload["runtime_instruction"] = runtime_instruction

        response, elapsed_ms, error = await self._request(
            client,
            "POST",
            self.chat_url,
            headers=self._headers(
                token=token,
                api_key=api_key,
                api_base_url=api_base_url,
                api_provider=api_provider,
            ),
            json=payload,
            timeout=self.args.chat_timeout_seconds,
        )
        parsed = self._safe_json(response)
        result: Dict[str, Any] = {
            "status_code": response.status_code if response is not None else 0,
            "elapsed_ms": elapsed_ms,
            "error": error or "",
            "payload": parsed if isinstance(parsed, dict) else {},
            "body_preview": (response.text[:500] if response is not None else ""),
        }
        if isinstance(parsed, dict):
            result["content"] = extract_content(parsed)
            result["request_id"] = str(parsed.get("request_id") or response.headers.get("X-Request-ID", "") if response else "")
            result["conversation_id"] = str(parsed.get("conversation_id") or response.headers.get("X-Conversation-ID", "") if response else "")
            result["usage"] = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
            result["memory"] = parsed.get("memory") if isinstance(parsed.get("memory"), dict) else {}
        else:
            result["content"] = ""
            result["request_id"] = response.headers.get("X-Request-ID", "") if response else ""
            result["conversation_id"] = response.headers.get("X-Conversation-ID", "") if response else ""
            result["usage"] = {}
            result["memory"] = {}
        return result

    async def _chat_stream(
        self,
        client: httpx.AsyncClient,
        *,
        token: str,
        message: str,
        workspace_id: Optional[str] = None,
        use_memory: bool = True,
        use_history: bool = True,
        use_knowledge: bool = False,
        use_facts: bool = True,
        temperature: float = 0.0,
        max_tokens: int = 128,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.resolved_model,
            "messages": [{"role": "user", "content": message}],
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "use_memory": bool(use_memory),
            "use_history": bool(use_history),
            "use_knowledge": bool(use_knowledge),
            "use_facts": bool(use_facts),
            "use_web_search": False,
        }
        if workspace_id:
            payload["workspace_id"] = workspace_id

        started = time.perf_counter()
        request_id = ""
        conversation_id = ""
        body_preview = ""
        content_parts: List[str] = []
        content_timestamps: List[float] = []
        first_content_at: Optional[float] = None
        memory_event_seen = False
        usage_event_seen = False
        conversation_event_seen = False
        last_error = ""
        status_code = 0

        timeout = httpx.Timeout(
            connect=8.0,
            read=self.args.stream_timeout_seconds,
            write=30.0,
            pool=8.0,
        )

        try:
            async with client.stream(
                "POST",
                self.chat_url,
                headers=self._headers(token=token),
                json=payload,
                timeout=timeout,
            ) as response:
                status_code = response.status_code
                request_id = response.headers.get("X-Request-ID", "")
                conversation_id = response.headers.get("X-Conversation-ID", "")

                if response.status_code != 200:
                    body_preview = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    return {
                        "status_code": status_code,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "ttft_ms": -1.0,
                        "request_id": request_id,
                        "conversation_id": conversation_id,
                        "conversation_event_seen": conversation_event_seen,
                        "memory_event_seen": memory_event_seen,
                        "usage_event_seen": usage_event_seen,
                        "chunk_count": 0,
                        "content": "",
                        "body_preview": body_preview,
                        "error": f"http_{status_code}",
                    }

                async for raw_line in response.aiter_lines():
                    if raw_line is None:
                        continue
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue

                    if data.startswith("{"):
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(obj, dict):
                            if "error" in obj:
                                last_error = str(obj.get("error"))
                                continue
                            if isinstance(obj.get("memory"), dict):
                                memory_event_seen = True
                            if isinstance(obj.get("usage"), dict):
                                usage_event_seen = True
                            event_conversation_id = str(obj.get("conversation_id") or "").strip()
                            if event_conversation_id:
                                conversation_event_seen = True
                                conversation_id = conversation_id or event_conversation_id
                            chunk = extract_content(obj)
                        else:
                            chunk = ""
                    else:
                        chunk = data

                    if not chunk:
                        continue
                    now = time.perf_counter()
                    if first_content_at is None:
                        first_content_at = now
                    content_timestamps.append(now)
                    content_parts.append(chunk)
        except Exception as exc:
            last_error = str(exc)

        ended = time.perf_counter()
        ttft_ms = ((first_content_at - started) * 1000.0) if first_content_at else -1.0
        return {
            "status_code": status_code,
            "elapsed_ms": int((ended - started) * 1000),
            "ttft_ms": round(ttft_ms, 2) if ttft_ms >= 0 else -1.0,
            "request_id": request_id,
            "conversation_id": conversation_id,
            "conversation_event_seen": conversation_event_seen,
            "memory_event_seen": memory_event_seen,
            "usage_event_seen": usage_event_seen,
            "chunk_count": len(content_timestamps),
            "content": "".join(content_parts),
            "body_preview": body_preview,
            "error": last_error,
        }

    async def _ensure_primary_name_seed(self, client: httpx.AsyncClient) -> tuple[str, str]:
        if self.primary_name_token and self.primary_name_conversation_id:
            return self.primary_name_token, self.primary_name_conversation_id
        name_token = self._tag("NAME")
        seeded = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message=f"My legal name is {name_token}. Please remember it for future conversations.",
            use_memory=True,
            use_history=True,
            max_tokens=64,
        )
        if seeded.get("status_code") == 200:
            self.primary_name_token = name_token
            self.primary_name_conversation_id = str(seeded.get("conversation_id") or "")
        return name_token, str(seeded.get("conversation_id") or "")

    async def _domain_d01_availability_auth(self, client: httpx.AsyncClient) -> DomainResult:
        started = time.perf_counter()
        checks: List[CheckResult] = []

        root_response, _, root_error = await self._request(client, "GET", self.root_url, timeout=10.0)
        root_payload = self._safe_json(root_response)
        self._add_check(
            checks,
            check_id="D01_C1",
            name="Root endpoint is reachable and reports running status",
            passed=bool(
                root_response is not None
                and root_response.status_code == 200
                and isinstance(root_payload, dict)
                and str(root_payload.get("service") or "").strip() == "PigTex Backend"
                and str(root_payload.get("status") or "").strip().lower() == "running"
            ),
            weight=3,
            critical=True,
            expected="HTTP 200 with service=PigTex Backend and status=running",
            actual=(json.dumps(root_payload, ensure_ascii=False)[:220] if isinstance(root_payload, dict) else root_error or ""),
        )

        health_response, _, health_error = await self._request(client, "GET", self.health_url, timeout=10.0)
        health_payload = self._safe_json(health_response)
        self._add_check(
            checks,
            check_id="D01_C2",
            name="Health endpoint reports healthy database",
            passed=bool(
                health_response is not None
                and health_response.status_code == 200
                and isinstance(health_payload, dict)
                and str(health_payload.get("status") or "").strip().lower() == "ok"
                and str(health_payload.get("db") or "").strip().lower() == "ok"
            ),
            weight=3,
            critical=True,
            expected="HTTP 200 with status=ok and db=ok",
            actual=(json.dumps(health_payload, ensure_ascii=False)[:220] if isinstance(health_payload, dict) else health_error or ""),
        )

        unauth_response, _, unauth_error = await self._request(client, "GET", self.me_url, timeout=10.0)
        self._add_check(
            checks,
            check_id="D01_C3",
            name="Protected auth endpoint rejects unauthenticated access",
            passed=bool(unauth_response is not None and unauth_response.status_code in {401, 403}),
            weight=2,
            expected="HTTP 401 or 403",
            actual=str(unauth_response.status_code if unauth_response is not None else unauth_error),
        )

        self.primary = await self._bootstrap_user(client, "primary")
        primary_me = await self._get_me(client, self.primary.token) if self.primary.token else {"status_code": 0, "payload": {}}
        self.secondary = await self._bootstrap_user(client, "secondary")

        self._add_check(
            checks,
            check_id="D01_C4",
            name="Primary benchmark user can register",
            passed=(self.primary.register_status == 201),
            weight=2,
            expected="register returns 201",
            actual=str(self.primary.register_status),
        )
        self._add_check(
            checks,
            check_id="D01_C5",
            name="Primary benchmark user can login and receive a bearer token",
            passed=bool(self.primary.login_status == 200 and self.primary.token),
            weight=2,
            critical=True,
            expected="login returns 200 with access_token",
            actual=str(self.primary.login_status),
        )
        me_payload = primary_me.get("payload") if isinstance(primary_me.get("payload"), dict) else {}
        self._add_check(
            checks,
            check_id="D01_C6",
            name="Primary user profile endpoint returns the expected identity",
            passed=bool(
                primary_me.get("status_code") == 200
                and str(me_payload.get("email") or "") == self.primary.email
                and str(me_payload.get("username") or "") == self.primary.username
                and bool(me_payload.get("is_active")) is True
            ),
            weight=1,
            expected=f"email={self.primary.email}, username={self.primary.username}",
            actual=json.dumps(me_payload, ensure_ascii=False)[:220],
        )
        self._add_check(
            checks,
            check_id="D01_C7",
            name="Secondary benchmark user can register and login for isolation tests",
            passed=bool(
                self.secondary.register_status == 201
                and self.secondary.login_status == 200
                and self.secondary.token
            ),
            weight=2,
            critical=True,
            expected="secondary user ready with token",
            actual=f"register={self.secondary.register_status}, login={self.secondary.login_status}",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "primary": {
                "email": self.primary.email,
                "username": self.primary.username,
                "user_id": self.primary.user_id,
            },
            "secondary": {
                "email": self.secondary.email,
                "username": self.secondary.username,
                "user_id": self.secondary.user_id,
            },
        }
        return self._finalize_domain(
            domain_id="D01",
            title="Availability and Auth",
            critical=True,
            min_pass_ratio=0.8,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _domain_d02_byok_core_chat(self, client: httpx.AsyncClient) -> DomainResult:
        if not self.primary or not self.primary.token:
            return self._blocked_domain("D02", "BYOK and Core Chat Contract", "primary auth session unavailable", 15)

        started = time.perf_counter()
        checks: List[CheckResult] = []

        models = await self._list_models(client, self.primary.token)
        self.models_payload = models.get("payload") if isinstance(models.get("payload"), dict) else {}
        self.resolved_model = self._pick_model(self.models_payload)
        model_items = self.models_payload.get("data") if isinstance(self.models_payload.get("data"), list) else []

        self._add_check(
            checks,
            check_id="D02_C1",
            name="BYOK models endpoint responds successfully",
            passed=(models.get("status_code") == 200),
            weight=2,
            critical=True,
            expected="HTTP 200 from /api/v1/models",
            actual=str(models.get("status_code") or models.get("error") or ""),
        )
        self._add_check(
            checks,
            check_id="D02_C2",
            name="Upstream model catalog is non-empty",
            passed=bool(isinstance(model_items, list) and len(model_items) > 0),
            weight=3,
            critical=True,
            expected="at least one model entry",
            actual=f"models={len(model_items) if isinstance(model_items, list) else 0}",
        )

        nonstream = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message="In one short sentence, explain why production health checks matter. Include the word heartbeat.",
            use_memory=True,
            use_history=False,
            use_knowledge=False,
            use_facts=True,
            max_tokens=80,
            temperature=0.0,
        )
        usage_summary = await self._get_usage(client, self.primary.token)

        self._add_check(
            checks,
            check_id="D02_C3",
            name="Non-stream chat request returns HTTP 200",
            passed=(nonstream.get("status_code") == 200),
            weight=2,
            critical=True,
            expected="HTTP 200",
            actual=str(nonstream.get("status_code") or nonstream.get("error") or ""),
        )
        self._add_check(
            checks,
            check_id="D02_C4",
            name="Non-stream chat response contains assistant text",
            passed=bool(str(nonstream.get("content") or "").strip()),
            weight=3,
            critical=True,
            expected="non-empty assistant content",
            actual=str(nonstream.get("content") or "")[:220],
        )

        usage_payload = nonstream.get("usage") if isinstance(nonstream.get("usage"), dict) else {}
        memory_payload = nonstream.get("memory") if isinstance(nonstream.get("memory"), dict) else {}
        metadata_ok = bool(
            str(nonstream.get("request_id") or "").strip()
            and str(nonstream.get("conversation_id") or "").strip()
            and isinstance(usage_payload, dict)
            and int(usage_payload.get("total_tokens") or 0) > 0
            and isinstance(memory_payload, dict)
        )
        self._add_check(
            checks,
            check_id="D02_C5",
            name="Non-stream contract exposes request, conversation, usage, and memory metadata",
            passed=metadata_ok,
            weight=2,
            critical=True,
            expected="request_id + conversation_id + usage.total_tokens>0 + memory object",
            actual=json.dumps(
                {
                    "request_id": nonstream.get("request_id"),
                    "conversation_id": nonstream.get("conversation_id"),
                    "usage": usage_payload,
                    "memory": memory_payload,
                },
                ensure_ascii=False,
            )[:260],
        )

        usage_today = {}
        usage_payload_full = usage_summary.get("payload") if isinstance(usage_summary.get("payload"), dict) else {}
        if isinstance(usage_payload_full.get("today"), dict):
            usage_today = usage_payload_full.get("today") or {}
        self._add_check(
            checks,
            check_id="D02_C6",
            name="Usage tracking endpoint reflects at least one request",
            passed=bool(usage_summary.get("status_code") == 200 and int(usage_today.get("total_requests") or 0) >= 1),
            weight=1,
            expected="today.total_requests >= 1",
            actual=json.dumps(usage_today, ensure_ascii=False)[:180],
        )
        self._add_check(
            checks,
            check_id="D02_C7",
            name="Non-stream latency stays within the production SLO",
            passed=bool(int(nonstream.get("elapsed_ms") or 0) <= self.args.nonstream_slo_ms),
            weight=2,
            expected=f"<= {self.args.nonstream_slo_ms} ms",
            actual=f"{nonstream.get('elapsed_ms')} ms",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "requested_model": self.args.model,
            "resolved_model": self.resolved_model,
            "models_count": len(model_items) if isinstance(model_items, list) else 0,
            "nonstream_preview": str(nonstream.get("content") or "")[:280],
            "usage_today": usage_today,
        }
        return self._finalize_domain(
            domain_id="D02",
            title="BYOK and Core Chat Contract",
            critical=True,
            min_pass_ratio=0.8,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _domain_d03_streaming(self, client: httpx.AsyncClient) -> DomainResult:
        if not self.primary or not self.primary.token:
            return self._blocked_domain("D03", "Streaming Contract and Latency", "primary auth session unavailable", 15)

        started = time.perf_counter()
        checks: List[CheckResult] = []

        streamed = await self._chat_stream(
            client,
            token=self.primary.token,
            message="Answer with two short bullet points about production monitoring.",
            use_memory=True,
            use_history=False,
            use_knowledge=False,
            use_facts=True,
            max_tokens=120,
            temperature=0.0,
        )

        self._add_check(
            checks,
            check_id="D03_C1",
            name="Streaming endpoint returns HTTP 200",
            passed=(streamed.get("status_code") == 200),
            weight=2,
            critical=True,
            expected="HTTP 200",
            actual=str(streamed.get("status_code") or streamed.get("error") or ""),
        )
        self._add_check(
            checks,
            check_id="D03_C2",
            name="Streaming response emits assistant content",
            passed=bool(str(streamed.get("content") or "").strip()),
            weight=4,
            critical=True,
            expected="non-empty streamed content",
            actual=str(streamed.get("content") or "")[:220],
        )
        self._add_check(
            checks,
            check_id="D03_C3",
            name="Streaming metadata exposes a conversation identifier",
            passed=bool(str(streamed.get("conversation_id") or "").strip()),
            weight=2,
            critical=True,
            expected="conversation_id in header or stream metadata",
            actual=str(streamed.get("conversation_id") or ""),
        )
        self._add_check(
            checks,
            check_id="D03_C4",
            name="Streaming emits memory metadata event",
            passed=bool(streamed.get("memory_event_seen")),
            weight=2,
            expected="memory event seen before or during stream",
            actual=str(streamed.get("memory_event_seen")),
        )
        self._add_check(
            checks,
            check_id="D03_C5",
            name="Streaming emits usage metadata event",
            passed=bool(streamed.get("usage_event_seen")),
            weight=2,
            expected="usage event emitted at end of stream",
            actual=str(streamed.get("usage_event_seen")),
        )
        self._add_check(
            checks,
            check_id="D03_C6",
            name="Streaming time-to-first-token stays within the TTFT SLO",
            passed=bool(
                float(streamed.get("ttft_ms") or -1.0) >= 0
                and float(streamed.get("ttft_ms") or -1.0) <= float(self.args.stream_ttft_slo_ms)
            ),
            weight=1,
            expected=f"<= {self.args.stream_ttft_slo_ms} ms",
            actual=f"{streamed.get('ttft_ms')} ms",
        )
        self._add_check(
            checks,
            check_id="D03_C7",
            name="Streaming total latency stays within the end-to-end SLO",
            passed=bool(int(streamed.get("elapsed_ms") or 0) <= self.args.stream_total_slo_ms),
            weight=1,
            expected=f"<= {self.args.stream_total_slo_ms} ms",
            actual=f"{streamed.get('elapsed_ms')} ms",
        )
        self._add_check(
            checks,
            check_id="D03_C8",
            name="Streaming emits more than one content chunk",
            passed=bool(int(streamed.get("chunk_count") or 0) >= 2),
            weight=1,
            expected="chunk_count >= 2",
            actual=str(streamed.get("chunk_count")),
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "ttft_ms": streamed.get("ttft_ms"),
            "elapsed_ms": streamed.get("elapsed_ms"),
            "chunk_count": streamed.get("chunk_count"),
            "content_preview": str(streamed.get("content") or "")[:280],
        }
        return self._finalize_domain(
            domain_id="D03",
            title="Streaming Contract and Latency",
            critical=True,
            min_pass_ratio=0.8,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _domain_d04_memory(self, client: httpx.AsyncClient) -> DomainResult:
        if not self.primary or not self.primary.token:
            return self._blocked_domain("D04", "Memory Continuity and Containment", "primary auth session unavailable", 15)

        started = time.perf_counter()
        checks: List[CheckResult] = []

        name_token = self._tag("NAME")
        seed = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message=f"My legal name is {name_token}. Please remember it for future conversations.",
            use_memory=True,
            use_history=True,
            use_knowledge=False,
            use_facts=True,
            max_tokens=56,
            temperature=0.0,
        )
        conversation_id = str(seed.get("conversation_id") or "")
        if seed.get("status_code") == 200:
            self.primary_name_token = name_token
            self.primary_name_conversation_id = conversation_id

        same_conv = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message="What is my exact name from saved memory? Name only.",
            conversation_id=conversation_id or None,
            use_memory=True,
            use_history=True,
            use_knowledge=False,
            use_facts=True,
            max_tokens=32,
            temperature=0.0,
        )
        cross_conv = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message="What is my exact name from saved memory? Name only.",
            use_memory=True,
            use_history=False,
            use_knowledge=False,
            use_facts=True,
            max_tokens=32,
            temperature=0.0,
        )
        system_memories = await self._list_memories(
            client,
            token=self.primary.token,
            scope="system",
            limit=200,
        )
        stats = await self._memory_stats(client, self.primary.token)

        temp_nickname = f"TEMP-{self._tag('TMP')}"
        temp_seed = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message=f"For this week only, call me {temp_nickname}. This is temporary and conversation-scoped.",
            use_memory=True,
            use_history=True,
            use_knowledge=False,
            use_facts=True,
            max_tokens=56,
            temperature=0.0,
        )
        temp_conversation_id = str(temp_seed.get("conversation_id") or "")
        temp_same = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message="In this conversation, what temporary nickname should you use for me?",
            conversation_id=temp_conversation_id or None,
            use_memory=True,
            use_history=True,
            use_knowledge=False,
            use_facts=True,
            max_tokens=32,
            temperature=0.0,
        )
        temp_new = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message="In a separate fresh conversation, what temporary nickname should you use for me? If none, reply NONE.",
            use_memory=True,
            use_history=False,
            use_knowledge=False,
            use_facts=True,
            max_tokens=40,
            temperature=0.0,
            runtime_instruction="If there is no temporary nickname available in this conversation, reply NONE.",
        )

        self._add_check(
            checks,
            check_id="D04_C1",
            name="Identity memory seed request succeeds",
            passed=(seed.get("status_code") == 200 and bool(conversation_id)),
            weight=2,
            critical=True,
            expected="HTTP 200 with conversation_id",
            actual=f"status={seed.get('status_code')}, conversation_id={conversation_id}",
        )
        self._add_check(
            checks,
            check_id="D04_C2",
            name="Exact identity recall works inside the original conversation",
            passed=(name_token.lower() in str(same_conv.get("content") or "").lower()),
            weight=4,
            critical=True,
            expected=name_token,
            actual=str(same_conv.get("content") or "")[:180],
        )
        self._add_check(
            checks,
            check_id="D04_C3",
            name="Exact identity recall works from a fresh conversation",
            passed=(name_token.lower() in str(cross_conv.get("content") or "").lower()),
            weight=4,
            critical=True,
            expected=name_token,
            actual=str(cross_conv.get("content") or "")[:180],
        )
        self._add_check(
            checks,
            check_id="D04_C4",
            name="Canonical system memory contains the persisted identity token",
            passed=bool(
                system_memories.get("status_code") == 200
                and self._payload_contains_token(system_memories.get("payload"), name_token)
            ),
            weight=3,
            critical=True,
            expected=f"token present in /v1/memory/memories scope=system: {name_token}",
            actual=json.dumps(system_memories.get("payload") or {}, ensure_ascii=False)[:240],
        )
        self._add_check(
            checks,
            check_id="D04_C5",
            name="Temporary nickname is available only within the same conversation",
            passed=(temp_nickname.lower() in str(temp_same.get("content") or "").lower()),
            weight=1,
            expected=temp_nickname,
            actual=str(temp_same.get("content") or "")[:160],
        )
        self._add_check(
            checks,
            check_id="D04_C6",
            name="Temporary nickname does not leak into a fresh conversation",
            passed=(temp_nickname.lower() not in str(temp_new.get("content") or "").lower()),
            weight=1,
            critical=True,
            expected=f"not contain {temp_nickname}",
            actual=str(temp_new.get("content") or "")[:160],
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "identity_token": name_token,
            "identity_same_conv": str(same_conv.get("content") or "")[:220],
            "identity_cross_conv": str(cross_conv.get("content") or "")[:220],
            "temporary_token": temp_nickname,
            "temporary_same_conv": str(temp_same.get("content") or "")[:220],
            "temporary_new_conv": str(temp_new.get("content") or "")[:220],
            "memory_stats": stats.get("payload") if isinstance(stats.get("payload"), dict) else {},
        }
        return self._finalize_domain(
            domain_id="D04",
            title="Memory Continuity and Containment",
            critical=True,
            min_pass_ratio=0.8,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _domain_d05_isolation(self, client: httpx.AsyncClient) -> DomainResult:
        if not self.primary or not self.primary.token or not self.secondary or not self.secondary.token:
            return self._blocked_domain("D05", "Isolation and Access Control", "primary or secondary auth session unavailable", 15)

        started = time.perf_counter()
        checks: List[CheckResult] = []

        if not self.primary_name_token:
            await self._ensure_primary_name_seed(client)

        workspace_a = f"ws-a-{uuid.uuid4().hex[:8]}"
        workspace_b = f"ws-b-{uuid.uuid4().hex[:8]}"
        token_a = self._tag("WSA")
        token_b = self._tag("WSB")

        remember_a = await self._remember(
            client,
            token=self.primary.token,
            content=token_a,
            predicate="naming_convention",
            workspace_id=workspace_a,
        )
        remember_b = await self._remember(
            client,
            token=self.primary.token,
            content=token_b,
            predicate="naming_convention",
            workspace_id=workspace_b,
        )
        memories_a = await self._list_memories(
            client,
            token=self.primary.token,
            scope="workspace",
            workspace_id=workspace_a,
            limit=200,
        )
        memories_b = await self._list_memories(
            client,
            token=self.primary.token,
            scope="workspace",
            workspace_id=workspace_b,
            limit=200,
        )
        value_a = self._latest_value_for_key(memories_a.get("payload"), "naming_convention")
        value_b = self._latest_value_for_key(memories_b.get("payload"), "naming_convention")

        probe_a = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message="What naming convention should we use in this workspace? Reply with the exact token only.",
            workspace_id=workspace_a,
            use_memory=True,
            use_history=False,
            use_knowledge=False,
            use_facts=True,
            max_tokens=24,
            temperature=0.0,
        )
        probe_b = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message="What naming convention should we use in this workspace? Reply with the exact token only.",
            workspace_id=workspace_b,
            use_memory=True,
            use_history=False,
            use_knowledge=False,
            use_facts=True,
            max_tokens=24,
            temperature=0.0,
        )
        secondary_probe = await self._chat_nonstream(
            client,
            token=self.secondary.token,
            message="What is my exact name from saved memory? Name only.",
            use_memory=True,
            use_history=False,
            use_knowledge=False,
            use_facts=True,
            max_tokens=24,
            temperature=0.0,
        )
        secondary_memories = await self._list_memories(
            client,
            token=self.secondary.token,
            scope="all",
            limit=200,
        )

        self._add_check(
            checks,
            check_id="D05_C1",
            name="Workspace memories persist as separate canonical values",
            passed=bool(
                remember_a.get("status_code") == 200
                and remember_b.get("status_code") == 200
                and normalize_text(value_a) == normalize_text(token_a)
                and normalize_text(value_b) == normalize_text(token_b)
            ),
            weight=3,
            critical=True,
            expected=f"{workspace_a}={token_a}, {workspace_b}={token_b}",
            actual=f"{workspace_a}={value_a} | {workspace_b}={value_b}",
        )
        reply_a = str(probe_a.get("content") or "")
        self._add_check(
            checks,
            check_id="D05_C2",
            name="Workspace A retrieval returns only the Workspace A token",
            passed=(token_a.lower() in reply_a.lower() and token_b.lower() not in reply_a.lower()),
            weight=3,
            critical=True,
            expected=f"contains {token_a}, not {token_b}",
            actual=reply_a[:180],
        )
        reply_b = str(probe_b.get("content") or "")
        self._add_check(
            checks,
            check_id="D05_C3",
            name="Workspace B retrieval returns only the Workspace B token",
            passed=(token_b.lower() in reply_b.lower() and token_a.lower() not in reply_b.lower()),
            weight=3,
            critical=True,
            expected=f"contains {token_b}, not {token_a}",
            actual=reply_b[:180],
        )
        secondary_reply = str(secondary_probe.get("content") or "")
        self._add_check(
            checks,
            check_id="D05_C4",
            name="A different user cannot retrieve the primary user's identity memory through chat",
            passed=bool(
                self.primary_name_token
                and self.primary_name_token.lower() not in secondary_reply.lower()
                and token_a.lower() not in secondary_reply.lower()
                and token_b.lower() not in secondary_reply.lower()
            ),
            weight=3,
            critical=True,
            expected=f"response does not contain {self.primary_name_token}, {token_a}, or {token_b}",
            actual=secondary_reply[:180],
        )
        leaked_to_secondary = any(
            self._payload_contains_token(secondary_memories.get("payload"), token)
            for token in [self.primary_name_token, token_a, token_b]
            if token
        )
        self._add_check(
            checks,
            check_id="D05_C5",
            name="A different user cannot see the primary user's memory entries via the memory API",
            passed=bool(secondary_memories.get("status_code") == 200 and not leaked_to_secondary),
            weight=3,
            critical=True,
            expected="secondary memory payload excludes primary-user tokens",
            actual=json.dumps(secondary_memories.get("payload") or {}, ensure_ascii=False)[:240],
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "workspace_a": workspace_a,
            "workspace_b": workspace_b,
            "token_a": token_a,
            "token_b": token_b,
            "reply_a": reply_a[:220],
            "reply_b": reply_b[:220],
            "secondary_reply": secondary_reply[:220],
        }
        return self._finalize_domain(
            domain_id="D05",
            title="Isolation and Access Control",
            critical=True,
            min_pass_ratio=0.8,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _domain_d06_resilience(self, client: httpx.AsyncClient) -> DomainResult:
        if not self.primary or not self.primary.token:
            return self._blocked_domain("D06", "Resilience and Error Semantics", "primary auth session unavailable", 10)

        started = time.perf_counter()
        checks: List[CheckResult] = []

        malformed_response, malformed_elapsed_ms, malformed_error = await self._request(
            client,
            "POST",
            self.chat_url,
            headers=self._headers(token=self.primary.token),
            json={"messages": "oops"},
            timeout=15.0,
        )
        malformed_payload = self._safe_json(malformed_response)

        invalid_scope_response, _, invalid_scope_error = await self._request(
            client,
            "GET",
            self.memories_url,
            headers=self._headers(token=self.primary.token),
            params={"scope": "bogus", "limit": 10},
            timeout=15.0,
        )
        invalid_scope_payload = self._safe_json(invalid_scope_response)

        bad_upstream = await self._chat_nonstream(
            client,
            token=self.primary.token,
            message="Respond with one short sentence about graceful degradation.",
            use_memory=False,
            use_history=False,
            use_knowledge=False,
            use_facts=False,
            max_tokens=40,
            temperature=0.0,
            api_key=self.args.api_key,
            api_base_url="http://127.0.0.1:1",
            api_provider="openai",
        )

        self._add_check(
            checks,
            check_id="D06_C1",
            name="Malformed chat payload is rejected with a validation error",
            passed=bool(malformed_response is not None and malformed_response.status_code == 422),
            weight=4,
            critical=True,
            expected="HTTP 422",
            actual=str(malformed_response.status_code if malformed_response is not None else malformed_error),
        )
        self._add_check(
            checks,
            check_id="D06_C2",
            name="Memory API rejects invalid scope values",
            passed=bool(invalid_scope_response is not None and invalid_scope_response.status_code == 400),
            weight=2,
            expected="HTTP 400",
            actual=str(invalid_scope_response.status_code if invalid_scope_response is not None else invalid_scope_error),
        )
        self._add_check(
            checks,
            check_id="D06_C3",
            name="Unreachable upstream returns a server-side error instead of hanging",
            passed=bool(int(bad_upstream.get("status_code") or 0) in {500, 502, 503, 504}),
            weight=2,
            critical=True,
            expected="HTTP 5xx for unreachable upstream",
            actual=f"status={bad_upstream.get('status_code')}, body={bad_upstream.get('body_preview') or bad_upstream.get('error') or ''}",
        )
        self._add_check(
            checks,
            check_id="D06_C4",
            name="Unreachable upstream fails fast within the production timeout budget",
            passed=bool(int(bad_upstream.get("elapsed_ms") or 0) <= self.args.fail_fast_slo_ms),
            weight=2,
            critical=True,
            expected=f"<= {self.args.fail_fast_slo_ms} ms",
            actual=f"{bad_upstream.get('elapsed_ms')} ms",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "malformed_elapsed_ms": malformed_elapsed_ms,
            "malformed_payload": malformed_payload if isinstance(malformed_payload, dict) else {},
            "invalid_scope_payload": invalid_scope_payload if isinstance(invalid_scope_payload, dict) else {},
            "bad_upstream": {
                "status_code": bad_upstream.get("status_code"),
                "elapsed_ms": bad_upstream.get("elapsed_ms"),
                "error": bad_upstream.get("error"),
                "body_preview": str(bad_upstream.get("body_preview") or "")[:240],
            },
        }
        return self._finalize_domain(
            domain_id="D06",
            title="Resilience and Error Semantics",
            critical=True,
            min_pass_ratio=0.8,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _domain_d07_concurrency(self, client: httpx.AsyncClient) -> DomainResult:
        if not self.primary or not self.primary.token:
            return self._blocked_domain("D07", "Concurrency and Stability", "primary auth session unavailable", 15)

        started = time.perf_counter()
        checks: List[CheckResult] = []

        batch_tag = self._tag("CONC")
        tasks = [
            self._chat_nonstream(
                client,
                token=self.primary.token,
                message=f"Return one short sentence about reliable systems and include the token {batch_tag}-{index:02d}.",
                use_memory=True,
                use_history=False,
                use_knowledge=False,
                use_facts=True,
                max_tokens=48,
                temperature=0.0,
            )
            for index in range(1, self.args.concurrent_requests + 1)
        ]
        results = await asyncio.gather(*tasks)

        total = len(results)
        successful = [item for item in results if int(item.get("status_code") or 0) == 200 and str(item.get("content") or "").strip()]
        empty = [item for item in results if int(item.get("status_code") or 0) == 200 and not str(item.get("content") or "").strip()]
        failures = [item for item in results if int(item.get("status_code") or 0) == 0 or int(item.get("status_code") or 0) >= 500]
        latency_values = [float(item.get("elapsed_ms") or 0) for item in successful]
        success_rate = (len(successful) / total) if total else 0.0
        empty_rate = (len(empty) / total) if total else 0.0
        p95_total_ms = percentile(latency_values, 0.95)

        self._add_check(
            checks,
            check_id="D07_C1",
            name="Concurrent success rate meets the production reliability target",
            passed=bool(success_rate >= self.args.concurrent_success_rate),
            weight=6,
            critical=True,
            expected=f">= {self.args.concurrent_success_rate:.2f}",
            actual=f"{success_rate:.2f}",
        )
        self._add_check(
            checks,
            check_id="D07_C2",
            name="Concurrent requests do not produce empty assistant responses",
            passed=bool(empty_rate == 0.0),
            weight=4,
            critical=True,
            expected="0.00",
            actual=f"{empty_rate:.2f}",
        )
        self._add_check(
            checks,
            check_id="D07_C3",
            name="Concurrent p95 latency stays within the production SLO",
            passed=bool(p95_total_ms >= 0 and p95_total_ms <= self.args.concurrent_p95_slo_ms),
            weight=3,
            expected=f"<= {self.args.concurrent_p95_slo_ms} ms",
            actual=f"{p95_total_ms:.2f} ms",
        )
        self._add_check(
            checks,
            check_id="D07_C4",
            name="Concurrent run completes without server-side 5xx or transport failures",
            passed=bool(len(failures) == 0),
            weight=2,
            critical=True,
            expected="0 failures",
            actual=f"{len(failures)} failures",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "concurrent_requests": self.args.concurrent_requests,
            "success_rate": round(success_rate, 4),
            "empty_rate": round(empty_rate, 4),
            "p95_total_ms": round(p95_total_ms, 2) if p95_total_ms >= 0 else -1.0,
            "failure_examples": [
                {
                    "status_code": item.get("status_code"),
                    "elapsed_ms": item.get("elapsed_ms"),
                    "error": item.get("error"),
                    "body_preview": str(item.get("body_preview") or "")[:140],
                }
                for item in failures[:5]
            ],
        }
        return self._finalize_domain(
            domain_id="D07",
            title="Concurrency and Stability",
            critical=True,
            min_pass_ratio=0.8,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def run(self) -> Dict[str, Any]:
        limits = httpx.Limits(
            max_keepalive_connections=max(20, self.args.concurrent_requests * 2),
            max_connections=max(50, self.args.concurrent_requests * 4),
        )
        timeout = httpx.Timeout(
            connect=8.0,
            read=self.args.chat_timeout_seconds,
            write=30.0,
            pool=8.0,
        )

        started = time.perf_counter()
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, limits=limits) as client:
            domains = [
                await self._domain_d01_availability_auth(client),
                await self._domain_d02_byok_core_chat(client),
                await self._domain_d03_streaming(client),
                await self._domain_d04_memory(client),
                await self._domain_d05_isolation(client),
                await self._domain_d06_resilience(client),
                await self._domain_d07_concurrency(client),
            ]

        total_elapsed_ms = int((time.perf_counter() - started) * 1000)
        score = round(sum(item.score for item in domains), 2)
        max_score = round(sum(item.max_score for item in domains), 2)
        score_percent = pct(score, max_score)
        passed_domains = [item for item in domains if item.passed]
        domain_pass_rate_percent = pct(len(passed_domains), len(domains))

        critical_failures: List[str] = []
        for domain in domains:
            for check_id in domain.critical_failed_checks:
                critical_failures.append(f"{domain.id}:{check_id}")

        production_ready = bool(
            score_percent >= 90.0
            and domain_pass_rate_percent >= 85.0
            and len(critical_failures) == 0
        )

        report = {
            "benchmark": "pigtex-production-full-system",
            "generated_at": utc_now_iso(),
            "base_url": self.base_url,
            "api_provider": self.args.api_provider,
            "api_base_url": self.args.api_base_url,
            "api_key_preview": mask_secret(self.args.api_key),
            "model_requested": self.args.model,
            "model_resolved": self.resolved_model,
            "thresholds": {
                "production_ready_score_percent": 90.0,
                "production_ready_domain_pass_rate_percent": 85.0,
                "nonstream_slo_ms": self.args.nonstream_slo_ms,
                "stream_ttft_slo_ms": self.args.stream_ttft_slo_ms,
                "stream_total_slo_ms": self.args.stream_total_slo_ms,
                "fail_fast_slo_ms": self.args.fail_fast_slo_ms,
                "concurrent_requests": self.args.concurrent_requests,
                "concurrent_success_rate": self.args.concurrent_success_rate,
                "concurrent_p95_slo_ms": self.args.concurrent_p95_slo_ms,
            },
            "users": {
                "primary": {
                    "email": self.primary.email if self.primary else "",
                    "username": self.primary.username if self.primary else "",
                    "user_id": self.primary.user_id if self.primary else "",
                },
                "secondary": {
                    "email": self.secondary.email if self.secondary else "",
                    "username": self.secondary.username if self.secondary else "",
                    "user_id": self.secondary.user_id if self.secondary else "",
                },
            },
            "score": score,
            "max_score": max_score,
            "score_percent": score_percent,
            "domain_pass_rate_percent": domain_pass_rate_percent,
            "passed_domains": len(passed_domains),
            "total_domains": len(domains),
            "critical_failures": critical_failures,
            "production_ready": production_ready,
            "notes": self.notes,
            "domains": [asdict(item) for item in domains],
            "elapsed_ms": total_elapsed_ms,
        }

        self.output_json.parent.mkdir(parents=True, exist_ok=True)
        self.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Production-grade full-system benchmark for PigTex.")
    parser.add_argument("--base-url", default=os.getenv("PIGTEX_BASE_URL", "http://127.0.0.1:3001"))
    parser.add_argument("--api-key", default=os.getenv("PIGTEX_API_KEY", ""))
    parser.add_argument("--api-base-url", default=os.getenv("PIGTEX_API_BASE_URL", ""))
    parser.add_argument("--api-provider", default=os.getenv("PIGTEX_API_PROVIDER", "custom"))
    parser.add_argument("--model", default=os.getenv("PIGTEX_MODEL", "gpt-4o"))
    parser.add_argument("--bench-password", default=os.getenv("PIGTEX_BENCH_PASSWORD", "PigTex#ProdBench2026"))
    parser.add_argument("--output-json", default="")
    parser.add_argument("--nonstream-slo-ms", type=int, default=15000)
    parser.add_argument("--stream-ttft-slo-ms", type=int, default=5000)
    parser.add_argument("--stream-total-slo-ms", type=int, default=25000)
    parser.add_argument("--fail-fast-slo-ms", type=int, default=12000)
    parser.add_argument("--concurrent-requests", type=int, default=8)
    parser.add_argument("--concurrent-success-rate", type=float, default=0.95)
    parser.add_argument("--concurrent-p95-slo-ms", type=int, default=20000)
    parser.add_argument("--chat-timeout-seconds", type=float, default=45.0)
    parser.add_argument("--stream-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--random-seed", type=int, default=42)
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()
    benchmark = PigTexProductionBenchmark(args)
    report = await benchmark.run()

    print(
        json.dumps(
            {
                "score_percent": report.get("score_percent"),
                "domain_pass_rate_percent": report.get("domain_pass_rate_percent"),
                "production_ready": report.get("production_ready"),
                "critical_failures": report.get("critical_failures"),
                "model_resolved": report.get("model_resolved"),
                "report_path": str(benchmark.output_json),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.get("production_ready") else 1


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
