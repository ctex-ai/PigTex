#!/usr/bin/env python3
"""
Hard production benchmark for PigTex memory + self-learning behavior.

This script stress-tests:
1) Long-horizon memory recall under distraction
2) Conflict guard for single-value identity memory
3) Explicit memory updates ("from now on")
4) Workspace memory isolation
5) Temporary memory containment
6) Noise immunity (transient queries should not pollute memory)
7) Adaptive response style learning
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
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


EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(part: float, whole: float) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100.0, 2)


def extract_content(payload: Dict[str, Any]) -> str:
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


def parse_iso(ts: Optional[str]) -> float:
    if not ts:
        return 0.0
    raw = str(ts).strip()
    if not raw:
        return 0.0
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return 0.0


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


@dataclass
class CheckResult:
    id: str
    name: str
    passed: bool
    weight: float
    critical: bool = False
    skipped: bool = False
    detail: str = ""
    expected: Optional[str] = None
    actual: Optional[str] = None


@dataclass
class ScenarioResult:
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


class HardMemorySelfLearningBenchmark:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base_url = args.base_url.rstrip("/")
        self.chat_url = f"{self.base_url}/api/v1/chat/completions"
        self.memories_url = f"{self.base_url}/api/v1/memory/memories"
        self.memory_stats_url = f"{self.base_url}/api/v1/memory/stats"
        self.memory_remember_url = f"{self.base_url}/api/v1/memory/remember"
        self.user_profile_url = f"{self.base_url}/api/user/profile"
        self.random = random.Random(args.random_seed)
        self._cached_user_id: Optional[str] = None
        self._cached_local_db_path: Optional[Path] = None

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.args.auth_token}",
            "Content-Type": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
        }
        if self.args.api_key:
            headers["X-API-Key"] = self.args.api_key
        if self.args.api_base_url:
            headers["X-API-Base-URL"] = self.args.api_base_url
        if self.args.api_provider:
            headers["X-API-Provider"] = self.args.api_provider
        return headers

    def _tag(self, prefix: str) -> str:
        return f"{prefix}-{self.random.randint(1000, 9999)}-{uuid.uuid4().hex[:4].upper()}"

    def _missing_tokens(self, text: str, tokens: List[str]) -> List[str]:
        lowered = normalize_text(text)
        missing: List[str] = []
        for token in tokens:
            tok = normalize_text(token)
            if tok and tok not in lowered:
                missing.append(token)
        return missing

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
                weight=(0.0 if skipped else float(weight)),
                critical=bool(critical),
                skipped=bool(skipped),
                detail=detail,
                expected=expected,
                actual=actual,
            )
        )

    def _finalize_scenario(
        self,
        *,
        scenario_id: str,
        title: str,
        critical: bool,
        min_pass_ratio: float,
        checks: List[CheckResult],
        elapsed_ms: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> ScenarioResult:
        max_score = sum(c.weight for c in checks if not c.skipped)
        score = sum(c.weight for c in checks if c.passed and not c.skipped)
        score_percent = pct(score, max_score)
        critical_failed_checks = [
            c.id
            for c in checks
            if c.critical and (not c.passed) and (not c.skipped)
        ]
        passed = (len(critical_failed_checks) == 0) and (
            (score / max_score) >= min_pass_ratio if max_score > 0 else False
        )
        return ScenarioResult(
            id=scenario_id,
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

    async def _chat(
        self,
        client: httpx.AsyncClient,
        *,
        message: str,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        use_memory: bool = True,
        use_history: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 120,
        runtime_instruction: Optional[str] = None,
        require_text: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.args.model,
            "messages": [{"role": "user", "content": message}],
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "use_memory": bool(use_memory),
            "use_knowledge": (not bool(self.args.disable_knowledge)),
            "use_web_search": False,
            "use_facts": True,
            "use_history": bool(use_history),
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if workspace_id:
            payload["workspace_id"] = workspace_id
        if runtime_instruction:
            payload["runtime_instruction"] = runtime_instruction

        last_error: Optional[Exception] = None
        attempts = max(0, int(self.args.chat_retries)) + 1
        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(self.chat_url, headers=self._headers(), json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                await asyncio.sleep(float(self.args.retry_backoff_seconds))
                continue

            if response.status_code != 200:
                retryable = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                if retryable and attempt < attempts:
                    await asyncio.sleep(float(self.args.retry_backoff_seconds))
                    continue
                raise RuntimeError(
                    f"chat_failed status={response.status_code} body={(response.text or '')[:400]}"
                )

            body = response.json()
            text = extract_content(body)
            conv_id = (
                body.get("conversation_id")
                or response.headers.get("X-Conversation-ID")
                or conversation_id
            )
            if require_text and not str(text or "").strip():
                if attempt < attempts:
                    await asyncio.sleep(float(self.args.retry_backoff_seconds))
                    continue
            memory_meta = body.get("memory")
            return {
                "conversation_id": conv_id,
                "text": text,
                "raw": body,
                "memory": memory_meta if isinstance(memory_meta, dict) else {},
                "empty_text": bool(require_text and not str(text or "").strip()),
            }

        if last_error is not None:
            raise last_error
        raise RuntimeError("chat_failed_unknown")

    async def _list_memories(
        self,
        client: httpx.AsyncClient,
        *,
        scope: str = "all",
        workspace_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"scope": scope, "limit": limit}
        if workspace_id:
            params["workspace_id"] = workspace_id
        response = await client.get(self.memories_url, headers=self._headers(), params=params)
        if response.status_code != 200:
            raise RuntimeError(
                f"list_memories_failed status={response.status_code} body={(response.text or '')[:400]}"
            )
        body = response.json()
        memories = body.get("memories")
        if not isinstance(memories, list):
            return []
        return [item for item in memories if isinstance(item, dict)]

    async def _remember(
        self,
        client: httpx.AsyncClient,
        *,
        content: str,
        predicate: str,
        category: str = "explicit_memory",
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "content": content,
            "predicate": predicate,
            "category": category,
            "subject": "User",
        }
        if workspace_id:
            params["workspace_id"] = workspace_id
        if conversation_id:
            params["conversation_id"] = conversation_id
        response = await client.post(
            self.memory_remember_url,
            headers=self._headers(),
            params=params,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"remember_failed status={response.status_code} body={(response.text or '')[:400]}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("remember_failed invalid_response")
        return body

    async def _memory_stats(self, client: httpx.AsyncClient) -> Dict[str, int]:
        response = await client.get(self.memory_stats_url, headers=self._headers())
        if response.status_code != 200:
            raise RuntimeError(
                f"memory_stats_failed status={response.status_code} body={(response.text or '')[:400]}"
            )
        body = response.json()
        storage = body.get("storage") if isinstance(body, dict) else {}
        if not isinstance(storage, dict):
            storage = {}
        return {
            "fact_count": int(storage.get("fact_count", 0) or 0),
            "assertion_count": int(storage.get("memory_assertion_count", 0) or 0),
        }

    async def _get_user_id(self, client: httpx.AsyncClient) -> Optional[str]:
        if self._cached_user_id:
            return self._cached_user_id
        response = await client.get(self.user_profile_url, headers=self._headers())
        if response.status_code != 200:
            return None
        body = response.json()
        if not isinstance(body, dict):
            return None
        user_id = str(body.get("id") or "").strip()
        if not user_id:
            return None
        self._cached_user_id = user_id
        return user_id

    async def _pending_change_count(self, client: httpx.AsyncClient) -> Optional[int]:
        if self.args.skip_sqlite_checks:
            return None

        user_id = await self._get_user_id(client)
        if not user_id:
            return None

        if self._cached_local_db_path is None:
            if os.name == "nt":
                base = Path(os.environ.get("APPDATA", str(Path.home())))
            else:
                base = Path.home()
            self._cached_local_db_path = base / ".pigtex" / user_id / "local.db"

        db_path = self._cached_local_db_path
        if not db_path.exists():
            return None

        try:
            conn = sqlite3.connect(str(db_path))
            try:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM memory_pending_changes WHERE user_id = ? AND status = 'pending'",
                    (user_id,),
                )
                row = cursor.fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception:
            return None

    def _latest_value_for_key(
        self,
        memories: List[Dict[str, Any]],
        key: str,
    ) -> Optional[str]:
        candidates = []
        normalized_key = (key or "").strip().lower()
        for item in memories:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("key") or item.get("predicate") or "").strip().lower()
            if item_key != normalized_key:
                continue
            ts = parse_iso(str(item.get("updated_at") or item.get("created_at") or ""))
            candidates.append((ts, item))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]
        value = str(best.get("value") or best.get("object") or "").strip()
        return value or None

    async def _run_noise_turns(
        self,
        client: httpx.AsyncClient,
        *,
        conversation_id: str,
        turns: int,
        workspace_id: Optional[str] = None,
    ) -> str:
        conv_id = conversation_id
        for i in range(max(0, turns)):
            checksum = f"ACK-{i + 1:02d}"
            prompt = (
                f"Noise turn {i + 1}: return exactly {checksum}. "
                "No explanation."
            )
            result = await self._chat(
                client,
                message=prompt,
                conversation_id=conv_id,
                workspace_id=workspace_id,
                use_memory=True,
                use_history=True,
                temperature=0.0,
                max_tokens=32,
                runtime_instruction="Reply with exactly one short token.",
                require_text=True,
            )
            conv_id = str(result.get("conversation_id") or conv_id)
        return conv_id

    def _count_bullets(self, text: str) -> int:
        count = 0
        for line in (text or "").splitlines():
            if BULLET_RE.match(line):
                count += 1
        return count

    def _has_emoji(self, text: str) -> bool:
        return bool(EMOJI_RE.search(text or ""))

    async def _scenario_m01_long_horizon(self, client: httpx.AsyncClient) -> ScenarioResult:
        sid = "M01"
        title = "Long-Horizon Recall Under Distraction"
        checks: List[CheckResult] = []
        started = time.perf_counter()

        tag = self._tag("M01")
        nickname = f"NICK-{tag}-R7"
        editor = f"EDITOR-{tag}-NVIM"

        seed_message = (
            f"Please remember permanently: my nickname is {nickname} "
            f"and my daily editor is {editor}."
        )
        t1 = await self._chat(client, message=seed_message, max_tokens=64, temperature=0.1)
        conversation_id = str(t1.get("conversation_id") or "")

        if conversation_id:
            conversation_id = await self._run_noise_turns(
                client,
                conversation_id=conversation_id,
                turns=self.args.noise_turns + 4,
            )

        probe = await self._chat(
            client,
            message=(
                "Use saved memory only. Reply in one line with this exact format: "
                "nickname=<value>; editor=<value>. "
                "What are my nickname and daily editor?"
            ),
            conversation_id=conversation_id or None,
            use_memory=True,
            use_history=True,
            temperature=0.0,
            max_tokens=72,
            runtime_instruction="Do not guess. Use current memory context only.",
            require_text=True,
        )
        reply_text = str(probe.get("text") or "")

        missing_reply = self._missing_tokens(reply_text, [nickname, editor])
        self._add_check(
            checks,
            check_id="M01_C1",
            name="Recall contains both unique tokens",
            passed=(len(missing_reply) == 0),
            weight=10,
            critical=True,
            detail=("missing=" + ", ".join(missing_reply)) if missing_reply else "ok",
            expected=f"{nickname}, {editor}",
            actual=reply_text[:180],
        )

        memory_meta = probe.get("memory") if isinstance(probe.get("memory"), dict) else {}
        facts_used = int(memory_meta.get("facts_used", 0) or 0)
        self._add_check(
            checks,
            check_id="M01_C2",
            name="Memory metadata indicates fact injection",
            passed=(facts_used >= 1),
            weight=5,
            detail=f"facts_used={facts_used}",
            expected="facts_used >= 1",
            actual=str(facts_used),
        )

        system_memories = await self._list_memories(client, scope="system", limit=800)
        memory_values_blob = " ".join(
            str(item.get("value") or item.get("object") or "")
            for item in system_memories
        )
        missing_stored = self._missing_tokens(memory_values_blob, [nickname, editor])
        self._add_check(
            checks,
            check_id="M01_C3",
            name="Tokens persisted in canonical memory store",
            passed=(len(missing_stored) == 0),
            weight=3,
            detail=("missing=" + ", ".join(missing_stored)) if missing_stored else "ok",
        )

        self._add_check(
            checks,
            check_id="M01_C4",
            name="Conversation continuity available",
            passed=bool(conversation_id),
            weight=2,
            detail=f"conversation_id={'present' if conversation_id else 'missing'}",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "conversation_id": conversation_id,
            "nickname_token": nickname,
            "editor_token": editor,
            "probe_reply_preview": reply_text[:300],
            "facts_used": facts_used,
        }
        return self._finalize_scenario(
            scenario_id=sid,
            title=title,
            critical=True,
            min_pass_ratio=0.75,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _scenario_m02_conflict_guard(self, client: httpx.AsyncClient) -> ScenarioResult:
        sid = "M02"
        title = "Conflict Guard For Single-Value Identity Memory"
        checks: List[CheckResult] = []
        started = time.perf_counter()

        tag = self._tag("M02")
        old_name = f"NAME-{tag}-ALPHA"
        new_name = f"NAME-{tag}-BETA"

        seed = await self._chat(
            client,
            message=f"Please remember: my legal name is {old_name}.",
            max_tokens=48,
            temperature=0.0,
        )
        conversation_id = str(seed.get("conversation_id") or "")
        pending_before = await self._pending_change_count(client)

        await self._chat(
            client,
            message=f"My name is {new_name}.",
            conversation_id=conversation_id or None,
            max_tokens=48,
            temperature=0.0,
        )
        pending_after = await self._pending_change_count(client)

        memories = await self._list_memories(client, scope="system", limit=500)
        current_name = self._latest_value_for_key(memories, "user_name")

        self._add_check(
            checks,
            check_id="M02_C1",
            name="Conflicting implicit update does not overwrite current user_name",
            passed=bool(current_name and normalize_text(current_name) == normalize_text(old_name)),
            weight=7,
            critical=True,
            expected=old_name,
            actual=current_name or "",
        )
        self._add_check(
            checks,
            check_id="M02_C2",
            name="New conflicting value not promoted to active memory",
            passed=bool(not current_name or normalize_text(current_name) != normalize_text(new_name)),
            weight=4,
            expected=f"!= {new_name}",
            actual=current_name or "",
        )

        if pending_before is None or pending_after is None:
            self._add_check(
                checks,
                check_id="M02_C3",
                name="Pending change evidence created (local SQLite check)",
                passed=True,
                skipped=True,
                weight=0,
                detail="skipped (local SQLite not accessible or disabled)",
            )
        else:
            self._add_check(
                checks,
                check_id="M02_C3",
                name="Pending change evidence created (local SQLite check)",
                passed=(pending_after > pending_before),
                weight=2,
                detail=f"pending_before={pending_before}, pending_after={pending_after}",
                expected="pending_after > pending_before",
                actual=f"{pending_after} <= {pending_before}" if pending_after <= pending_before else "ok",
            )

        self._add_check(
            checks,
            check_id="M02_C4",
            name="Scenario conversation established",
            passed=bool(conversation_id),
            weight=2,
            detail=f"conversation_id={'present' if conversation_id else 'missing'}",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "conversation_id": conversation_id,
            "expected_name": old_name,
            "conflicting_name": new_name,
            "resolved_name": current_name,
            "pending_before": pending_before,
            "pending_after": pending_after,
        }
        return self._finalize_scenario(
            scenario_id=sid,
            title=title,
            critical=True,
            min_pass_ratio=0.75,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _scenario_m03_explicit_update(self, client: httpx.AsyncClient) -> ScenarioResult:
        sid = "M03"
        title = "Explicit Update Convergence"
        checks: List[CheckResult] = []
        started = time.perf_counter()

        tag = self._tag("M03")
        old_name = f"NAME-{tag}-OLD"
        new_name = f"NAME-{tag}-NEW"

        seeded = await self._chat(
            client,
            message=f"Please remember that my name is {old_name}.",
            max_tokens=50,
            temperature=0.0,
        )
        conversation_id = str(seeded.get("conversation_id") or "")

        await self._chat(
            client,
            message=(
                f"From now on, please change my name to {new_name}. "
                "Remember this change."
            ),
            conversation_id=conversation_id or None,
            max_tokens=56,
            temperature=0.0,
        )

        memories = await self._list_memories(client, scope="system", limit=500)
        current_name = self._latest_value_for_key(memories, "user_name")
        self._add_check(
            checks,
            check_id="M03_C1",
            name="Explicit update changes active user_name",
            passed=bool(current_name and normalize_text(current_name) == normalize_text(new_name)),
            weight=8,
            critical=True,
            expected=new_name,
            actual=current_name or "",
        )

        probe = await self._chat(
            client,
            message="What exact name should you use for me from saved memory? Reply with the name only.",
            conversation_id=conversation_id or None,
            max_tokens=24,
            temperature=0.0,
            runtime_instruction="Return only the current memory value.",
            require_text=True,
        )
        reply_text = str(probe.get("text") or "")
        missing = self._missing_tokens(reply_text, [new_name])
        self._add_check(
            checks,
            check_id="M03_C2",
            name="Assistant recalls updated name after explicit change",
            passed=(len(missing) == 0),
            weight=5,
            expected=new_name,
            actual=reply_text[:160],
        )

        self._add_check(
            checks,
            check_id="M03_C3",
            name="Conversation continuity maintained",
            passed=bool(conversation_id),
            weight=2,
            detail=f"conversation_id={'present' if conversation_id else 'missing'}",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "conversation_id": conversation_id,
            "old_name": old_name,
            "new_name": new_name,
            "resolved_name": current_name,
            "probe_reply_preview": reply_text[:240],
        }
        return self._finalize_scenario(
            scenario_id=sid,
            title=title,
            critical=True,
            min_pass_ratio=0.75,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _scenario_m04_workspace_isolation(self, client: httpx.AsyncClient) -> ScenarioResult:
        sid = "M04"
        title = "Workspace Isolation For Competing Facts"
        checks: List[CheckResult] = []
        started = time.perf_counter()

        tag = self._tag("M04")
        ws_a = f"mem-hard-ws-a-{tag.lower()}"
        ws_b = f"mem-hard-ws-b-{tag.lower()}"
        token_a = f"SNAKE-{tag}-A"
        token_b = f"CAMEL-{tag}-B"

        await self._remember(
            client,
            content=token_a,
            predicate="naming_convention",
            workspace_id=ws_a,
        )
        await self._remember(
            client,
            content=token_b,
            predicate="naming_convention",
            workspace_id=ws_b,
        )

        memories_a = await self._list_memories(client, scope="workspace", workspace_id=ws_a, limit=200)
        memories_b = await self._list_memories(client, scope="workspace", workspace_id=ws_b, limit=200)
        value_a = self._latest_value_for_key(memories_a, "naming_convention")
        value_b = self._latest_value_for_key(memories_b, "naming_convention")

        self._add_check(
            checks,
            check_id="M04_C1",
            name="Canonical workspace memories stored with separate values",
            passed=bool(
                value_a
                and value_b
                and normalize_text(value_a) == normalize_text(token_a)
                and normalize_text(value_b) == normalize_text(token_b)
            ),
            weight=5,
            critical=True,
            expected=f"{token_a} (ws_a) and {token_b} (ws_b)",
            actual=f"{value_a} | {value_b}",
        )

        probe_a = await self._chat(
            client,
            message=(
                "What naming convention should we use in this workspace? "
                "Reply with the exact token only."
            ),
            workspace_id=ws_a,
            use_history=False,
            max_tokens=24,
            temperature=0.0,
            require_text=True,
        )
        probe_b = await self._chat(
            client,
            message=(
                "What naming convention should we use in this workspace? "
                "Reply with the exact token only."
            ),
            workspace_id=ws_b,
            use_history=False,
            max_tokens=24,
            temperature=0.0,
            require_text=True,
        )
        reply_a = str(probe_a.get("text") or "")
        reply_b = str(probe_b.get("text") or "")

        self._add_check(
            checks,
            check_id="M04_C2",
            name="Workspace A retrieval returns only workspace A token",
            passed=(token_a.lower() in reply_a.lower() and token_b.lower() not in reply_a.lower()),
            weight=5,
            critical=True,
            expected=f"contains {token_a}, not {token_b}",
            actual=reply_a[:140],
        )
        self._add_check(
            checks,
            check_id="M04_C3",
            name="Workspace B retrieval returns only workspace B token",
            passed=(token_b.lower() in reply_b.lower() and token_a.lower() not in reply_b.lower()),
            weight=5,
            critical=True,
            expected=f"contains {token_b}, not {token_a}",
            actual=reply_b[:140],
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "workspace_a": ws_a,
            "workspace_b": ws_b,
            "token_a": token_a,
            "token_b": token_b,
            "reply_a": reply_a[:300],
            "reply_b": reply_b[:300],
        }
        return self._finalize_scenario(
            scenario_id=sid,
            title=title,
            critical=True,
            min_pass_ratio=0.8,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _scenario_m05_temporary_containment(self, client: httpx.AsyncClient) -> ScenarioResult:
        sid = "M05"
        title = "Temporary Memory Containment"
        checks: List[CheckResult] = []
        started = time.perf_counter()

        tag = self._tag("M05")
        temp_nickname = f"TEMP-{tag}-WEEK"

        seed = await self._chat(
            client,
            message=(
                f"For this week only, call me {temp_nickname}. "
                "This is temporary and conversation-scoped."
            ),
            max_tokens=56,
            temperature=0.0,
        )
        conversation_id = str(seed.get("conversation_id") or "")

        same_conv = await self._chat(
            client,
            message="In this conversation, what temporary nickname should you use for me?",
            conversation_id=conversation_id or None,
            max_tokens=32,
            temperature=0.0,
            require_text=True,
        )
        same_reply = str(same_conv.get("text") or "")

        new_conv = await self._chat(
            client,
            message=(
                "In a separate fresh conversation, what temporary nickname should you use for me? "
                "If none, reply NONE."
            ),
            use_history=False,
            max_tokens=40,
            temperature=0.0,
            runtime_instruction="If there is no temporary nickname available in this conversation, reply NONE.",
            require_text=True,
        )
        new_reply = str(new_conv.get("text") or "")

        self._add_check(
            checks,
            check_id="M05_C1",
            name="Temporary token is available in same conversation",
            passed=(temp_nickname.lower() in same_reply.lower()),
            weight=6,
            expected=temp_nickname,
            actual=same_reply[:150],
        )
        self._add_check(
            checks,
            check_id="M05_C2",
            name="Temporary token does not leak to new conversation",
            passed=(temp_nickname.lower() not in new_reply.lower()),
            weight=4,
            expected=f"not contain {temp_nickname}",
            actual=new_reply[:150],
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "conversation_id": conversation_id,
            "temporary_token": temp_nickname,
            "same_conversation_reply": same_reply[:300],
            "new_conversation_reply": new_reply[:300],
        }
        return self._finalize_scenario(
            scenario_id=sid,
            title=title,
            critical=False,
            min_pass_ratio=0.7,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _scenario_m06_noise_immunity(self, client: httpx.AsyncClient) -> ScenarioResult:
        sid = "M06"
        title = "Transient Noise Immunity"
        checks: List[CheckResult] = []
        started = time.perf_counter()

        baseline = await self._memory_stats(client)
        seed = await self._chat(
            client,
            message="Start transient noise session. Reply OK.",
            max_tokens=10,
            temperature=0.0,
        )
        conversation_id = str(seed.get("conversation_id") or "")

        transient_prompts = [
            "What is the weather in Ho Chi Minh City today?",
            "Gia vang hom nay bao nhieu?",
            "Latest bitcoin price right now?",
            "Any breaking tech news today?",
            "What time is it in Tokyo now?",
            "Ty gia USD/VND hom nay?",
            "Do we have rain forecast this weekend in Hanoi?",
            "Current ETH price and 24h change?",
        ]

        turns = self.args.noise_turns + 10
        conv_id = conversation_id
        for i in range(max(0, turns)):
            prompt = transient_prompts[i % len(transient_prompts)]
            response = await self._chat(
                client,
                message=prompt,
                conversation_id=conv_id or None,
                max_tokens=60,
                temperature=0.2,
                runtime_instruction="Reply briefly. No memory actions.",
            )
            conv_id = str(response.get("conversation_id") or conv_id)

        after = await self._memory_stats(client)
        delta_facts = int(after["fact_count"] - baseline["fact_count"])
        delta_assertions = int(after["assertion_count"] - baseline["assertion_count"])

        self._add_check(
            checks,
            check_id="M06_C1",
            name="Fact count growth remains bounded during transient-only traffic",
            passed=(delta_facts <= 2),
            weight=5,
            expected="delta_facts <= 2",
            actual=str(delta_facts),
        )
        self._add_check(
            checks,
            check_id="M06_C2",
            name="Assertion count growth remains bounded during transient-only traffic",
            passed=(delta_assertions <= 2),
            weight=5,
            expected="delta_assertions <= 2",
            actual=str(delta_assertions),
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "conversation_id": conversation_id,
            "turns": turns,
            "fact_count_before": baseline["fact_count"],
            "fact_count_after": after["fact_count"],
            "assertion_count_before": baseline["assertion_count"],
            "assertion_count_after": after["assertion_count"],
            "delta_facts": delta_facts,
            "delta_assertions": delta_assertions,
        }
        return self._finalize_scenario(
            scenario_id=sid,
            title=title,
            critical=False,
            min_pass_ratio=0.7,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def _scenario_m07_adaptive_style(self, client: httpx.AsyncClient) -> ScenarioResult:
        sid = "M07"
        title = "Self-Learning Style Adaptation"
        checks: List[CheckResult] = []
        started = time.perf_counter()

        style_instruction = (
            "From now on: answer using exactly 3 concise bullet points, "
            "avoid emoji, and end with a line that starts with 'Action:'."
        )
        seed = await self._chat(
            client,
            message=style_instruction,
            max_tokens=80,
            temperature=0.0,
        )
        conversation_id = str(seed.get("conversation_id") or "")

        q1 = await self._chat(
            client,
            message="How can we reduce API latency in Python services?",
            conversation_id=conversation_id or None,
            max_tokens=220,
            temperature=0.2,
            require_text=True,
        )
        q2 = await self._chat(
            client,
            message="How can we detect memory leaks in Node.js workers?",
            conversation_id=conversation_id or None,
            max_tokens=220,
            temperature=0.2,
            require_text=True,
        )
        answer1 = str(q1.get("text") or "")
        answer2 = str(q2.get("text") or "")

        bullets_1 = self._count_bullets(answer1)
        bullets_2 = self._count_bullets(answer2)
        has_emoji_1 = self._has_emoji(answer1)
        has_emoji_2 = self._has_emoji(answer2)
        has_action_2 = bool(re.search(r"(?im)^\s*action\s*:", answer2))

        self._add_check(
            checks,
            check_id="M07_C1",
            name="Follow-up #1 uses bullet format (>=3 bullets)",
            passed=(bullets_1 >= 3),
            weight=3,
            expected=">= 3 bullets",
            actual=str(bullets_1),
        )
        self._add_check(
            checks,
            check_id="M07_C2",
            name="Follow-up #2 keeps bullet format (>=3 bullets)",
            passed=(bullets_2 >= 3),
            weight=3,
            expected=">= 3 bullets",
            actual=str(bullets_2),
        )
        self._add_check(
            checks,
            check_id="M07_C3",
            name="Follow-up #1 contains no emoji",
            passed=(not has_emoji_1),
            weight=3,
            expected="no emoji",
            actual="emoji_found" if has_emoji_1 else "ok",
        )
        self._add_check(
            checks,
            check_id="M07_C4",
            name="Follow-up #2 contains no emoji",
            passed=(not has_emoji_2),
            weight=3,
            expected="no emoji",
            actual="emoji_found" if has_emoji_2 else "ok",
        )
        self._add_check(
            checks,
            check_id="M07_C5",
            name="Follow-up #2 includes Action line",
            passed=has_action_2,
            weight=2,
            expected="line starts with Action:",
            actual=answer2[:200],
        )

        memories = await self._list_memories(client, scope="system", limit=500)
        known_style_keys = {"response_style", "tone_preference", "emoji_usage"}
        seen_style_keys = {
            str(item.get("key") or item.get("predicate") or "").strip().lower()
            for item in memories
            if isinstance(item, dict)
        }
        has_style_key = any(key in seen_style_keys for key in known_style_keys)
        self._add_check(
            checks,
            check_id="M07_C6",
            name="Style preference persisted to canonical memory",
            passed=has_style_key,
            weight=1,
            expected="at least one of response_style/tone_preference/emoji_usage",
            actual=", ".join(sorted(k for k in seen_style_keys if k in known_style_keys)) or "none",
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        details = {
            "conversation_id": conversation_id,
            "bullets_followup_1": bullets_1,
            "bullets_followup_2": bullets_2,
            "emoji_followup_1": has_emoji_1,
            "emoji_followup_2": has_emoji_2,
            "has_action_followup_2": has_action_2,
            "answer1_preview": answer1[:320],
            "answer2_preview": answer2[:320],
        }
        return self._finalize_scenario(
            scenario_id=sid,
            title=title,
            critical=False,
            min_pass_ratio=0.7,
            checks=checks,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    async def run(self) -> Dict[str, Any]:
        timeout = httpx.Timeout(self.args.timeout_seconds)
        scenario_specs = [
            ("M01", "Long-Horizon Recall Under Distraction", True, 20, self._scenario_m01_long_horizon),
            ("M02", "Conflict Guard For Single-Value Identity Memory", True, 15, self._scenario_m02_conflict_guard),
            ("M03", "Explicit Update Convergence", True, 15, self._scenario_m03_explicit_update),
            ("M04", "Workspace Isolation For Competing Facts", True, 15, self._scenario_m04_workspace_isolation),
            ("M05", "Temporary Memory Containment", False, 10, self._scenario_m05_temporary_containment),
            ("M06", "Transient Noise Immunity", False, 10, self._scenario_m06_noise_immunity),
            ("M07", "Self-Learning Style Adaptation", False, 15, self._scenario_m07_adaptive_style),
        ]

        results: List[ScenarioResult] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            for sid, title, is_critical, fallback_weight, runner in scenario_specs:
                started = time.perf_counter()
                try:
                    result = await runner(client)
                except Exception as exc:
                    err_text = str(exc).strip() or exc.__class__.__name__
                    checks: List[CheckResult] = []
                    self._add_check(
                        checks,
                        check_id=f"{sid}_FATAL",
                        name="Scenario execution did not complete",
                        passed=False,
                        weight=fallback_weight,
                        critical=True,
                        detail=err_text,
                    )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    result = self._finalize_scenario(
                        scenario_id=sid,
                        title=title,
                        critical=is_critical,
                        min_pass_ratio=1.0,
                        checks=checks,
                        elapsed_ms=elapsed_ms,
                        details={"error": err_text},
                    )
                results.append(result)
                print(
                    f"[{result.id}] {'PASS' if result.passed else 'FAIL'} "
                    f"score={result.score:.1f}/{result.max_score:.1f} ({result.score_percent:.1f}%) "
                    f"elapsed={result.elapsed_ms}ms"
                )

        total_score = round(sum(r.score for r in results), 2)
        max_score = round(sum(r.max_score for r in results), 2)
        score_percent = pct(total_score, max_score)
        passed_count = sum(1 for r in results if r.passed)
        scenario_pass_rate = pct(passed_count, len(results))
        critical_failures = [r.id for r in results if r.critical and not r.passed]

        if score_percent >= 90 and not critical_failures:
            grade = "S"
        elif score_percent >= 85 and len(critical_failures) <= 1:
            grade = "A"
        elif score_percent >= 75:
            grade = "B"
        else:
            grade = "C"

        targets = {
            "score_percent_min": float(self.args.min_competitive_score),
            "scenario_pass_rate_percent_min": float(self.args.min_competitive_pass_rate),
            "max_critical_failures": int(self.args.max_critical_failures),
        }
        competition_ready = bool(
            score_percent >= targets["score_percent_min"]
            and scenario_pass_rate >= targets["scenario_pass_rate_percent_min"]
            and len(critical_failures) <= targets["max_critical_failures"]
        )

        report = {
            "generated_at": utc_now_iso(),
            "evaluator": "benchmark_memory_self_learning_hard.py",
            "description": (
                "Hard benchmark for memory + self-learning behavior in production-style conditions. "
                "Focuses on recall, conflict safety, explicit updates, scope isolation, "
                "temporary containment, noise immunity, and adaptive style."
            ),
            "model": self.args.model,
            "base_url": self.base_url,
            "api_provider": self.args.api_provider or "",
            "noise_turns": int(self.args.noise_turns),
            "chat_retries": int(self.args.chat_retries),
            "retry_backoff_seconds": float(self.args.retry_backoff_seconds),
            "disable_knowledge": bool(self.args.disable_knowledge),
            "force_disable_web_search": True,
            "targets": targets,
            "summary": {
                "score": total_score,
                "max_score": max_score,
                "score_percent": score_percent,
                "scenario_pass_rate_percent": scenario_pass_rate,
                "scenarios_total": len(results),
                "scenarios_passed": passed_count,
                "critical_failures": critical_failures,
                "grade": grade,
                "competition_ready": competition_ready,
            },
            "scenarios": [asdict(result) for result in results],
        }
        return report


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "App_desktop" / "backend").exists() and (parent / "ops" / "observability").exists():
            return parent
    return here.parents[2]


def _resolve_output_path(path_value: str) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return (_find_repo_root() / candidate).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run hard production benchmark for PigTex memory + self-learning."
    )
    parser.add_argument("--base-url", default=os.getenv("PIGTEX_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--auth-token", default=os.getenv("PIGTEX_AUTH_TOKEN", ""))
    parser.add_argument(
        "--api-key",
        "--texapi-key",
        dest="api_key",
        default=os.getenv("PIGTEX_API_KEY") or os.getenv("PIGTEX_TEXAPI_KEY", ""),
        help="Provider API key (optional if server has fallback upstream config)",
    )
    parser.add_argument(
        "--api-base-url",
        "--texapi-base-url",
        dest="api_base_url",
        default=os.getenv("PIGTEX_API_BASE_URL") or os.getenv("PIGTEX_TEXAPI_BASE_URL", ""),
        help="Provider base URL (optional, legacy alias: --texapi-base-url)",
    )
    parser.add_argument(
        "--api-provider",
        default=os.getenv("PIGTEX_API_PROVIDER", ""),
        help="Provider name: openai | anthropic | gemini | custom",
    )
    parser.add_argument("--model", default=os.getenv("PIGTEX_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--noise-turns", type=int, default=10, help="Base distraction/noise turns")
    parser.add_argument("--random-seed", type=int, default=20260308)
    parser.add_argument(
        "--chat-retries",
        type=int,
        default=1,
        help="Retry count for transient chat failures or empty assistant replies",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=1.0,
        help="Delay between chat retries",
    )
    parser.add_argument("--disable-knowledge", action="store_true", help="Disable knowledge retrieval in chat requests")
    parser.add_argument("--skip-sqlite-checks", action="store_true", help="Skip local SQLite pending-change checks")
    parser.add_argument("--min-competitive-score", type=float, default=85.0)
    parser.add_argument("--min-competitive-pass-rate", type=float, default=80.0)
    parser.add_argument("--max-critical-failures", type=int, default=0)
    parser.add_argument(
        "--output-json",
        default="ops/observability/reports/memory-self-learning-hard-latest.json",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    if not args.auth_token:
        raise SystemExit("--auth-token is required (or set PIGTEX_AUTH_TOKEN)")

    benchmark = HardMemorySelfLearningBenchmark(args)
    report = await benchmark.run()
    summary = report.get("summary", {})

    score = float(summary.get("score", 0.0) or 0.0)
    max_score = float(summary.get("max_score", 0.0) or 0.0)
    score_percent = float(summary.get("score_percent", 0.0) or 0.0)
    pass_rate = float(summary.get("scenario_pass_rate_percent", 0.0) or 0.0)
    grade = str(summary.get("grade", "C"))
    critical_failures = summary.get("critical_failures", [])
    ready = bool(summary.get("competition_ready"))

    print("\n=== Hard Memory + Self-Learning Benchmark ===")
    print(f"model={args.model}")
    print(f"score={score:.1f}/{max_score:.1f} ({score_percent:.1f}%) grade={grade}")
    print(f"scenario_pass_rate={pass_rate:.1f}%")
    print(f"critical_failures={critical_failures if critical_failures else 'none'}")
    print(f"competition_ready={ready}")

    output_path = _resolve_output_path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={output_path}")

    return 0 if ready else 1


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
