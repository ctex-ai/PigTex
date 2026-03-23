"""Competitive skill ingestion, normalization, and runtime selection for PigTex."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

from .packs import PromptPackStore

logger = logging.getLogger(__name__)

_VIETNAMESE_CHAR_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯ"
    r"áàảãạấầẩẫậắằẳẵặ"
    r"éèẻẽẹếềểễệ"
    r"íìỉĩị"
    r"óòỏõọốồổỗộớờởỡợ"
    r"úùủũụứừửữự"
    r"ýỳỷỹỵ]"
)
_SPLIT_HEADING_RE = re.compile(r"^#{2,4}\s+(?P<title>.+)$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_DANGEROUS_SKILL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?previous\s+(instructions|system|rules)", re.I),
    re.compile(r"(reveal|show|print).*(system\s+prompt|internal\s+instructions?)", re.I),
    re.compile(r"(override|replace)\s+(the\s+)?system", re.I),
)
_GENERIC_PATH_TOKENS = {
    "data",
    "incoming",
    "skills",
    "skill",
    "prompt",
    "prompts",
    "templates",
    "template",
    "rules",
    "docs",
    "doc",
    "playbooks",
    "playbook",
    "repo",
    "json",
    "md",
    "markdown",
}
_DOMAIN_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("marketing.ads.facebook", ("facebook ads", "meta ads", "facebook", "ad creative", "hook")),
    ("marketing.ads.tiktok", ("tiktok ads", "tiktok", "ugc ad", "short-form ad")),
    ("marketing.copy.hooks", ("hook", "headline", "angle", "cta", "offer")),
    ("marketing.video.production", ("video", "storyboard", "shot list", "cta ending")),
    ("marketing.voice.production", ("voiceover", "tts", "dubbing", "pronunciation", "narration")),
    ("design.image.qwen", ("qwen image", "poster", "banner", "thumbnail", "typography")),
    ("sales.outreach", ("cold email", "outreach", "follow-up", "lead", "prospect")),
    ("support.triage", ("support ticket", "triage", "customer complaint", "incident response")),
    ("education.teaching", ("explain", "teach", "lesson", "tutorial", "study")),
    ("coding.debug", ("debug", "exception", "stack trace", "root cause", "error")),
    ("coding.review", ("code review", "bug risk", "behavioral regression", "test gap")),
    ("coding.implementation", ("implement", "api", "function", "class", "refactor")),
    ("research.evidence", ("citation", "source", "evidence", "benchmark", "research")),
    ("operations.workflow", ("sop", "workflow", "playbook", "process", "checklist")),
]
_INTENT_HINTS: dict[str, tuple[str, ...]] = {
    "creative": ("marketing", "copy", "design", "video", "voice", "creative"),
    "planning": ("operations", "workflow", "planning"),
    "learning": ("education", "teaching", "research"),
    "advice": ("support", "sales", "operations"),
    "analysis": ("research", "review", "debug"),
    "research": ("research", "evidence", "benchmark"),
    "code_generation": ("coding", "implementation"),
    "code_review": ("coding", "review"),
    "debug": ("coding", "debug"),
}
_MONETIZATION_DOMAIN_PREFIXES: tuple[str, ...] = (
    "marketing.",
    "sales.",
    "operations.workflow",
    "design.image.",
)
_OUTPUT_CONTRACT_FALLBACKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "marketing.copy.hooks",
        (
            "Return exactly 3 hook options unless the user explicitly asks for a different count.",
            "Keep each hook concise and front-load the strongest angle.",
            "Use a different angle for each option.",
            "Do not add greetings, disclaimers, or outro filler.",
        ),
    ),
    (
        "marketing.ads.facebook",
        (
            "Return Facebook-ready copy with a strong first-line hook.",
            "Keep the opening concise enough to work above the fold.",
            "Use concrete pain, desire, or proof angles instead of generic reassurance.",
            "Do not add greetings, disclaimers, or outro filler.",
        ),
    ),
    (
        "marketing.ads.tiktok",
        (
            "Open with a short thumb-stop hook in the first line.",
            "Keep the structure punchy and suited to short-form ads.",
            "Use one clear angle per option instead of repeating the same idea.",
            "Do not add greetings, disclaimers, or outro filler.",
        ),
    ),
    (
        "marketing.video.production",
        (
            "Return a hook, a short beat-by-beat outline, and a CTA ending.",
            "Keep each beat scannable and production-ready.",
            "Avoid generic intros and narration padding.",
        ),
    ),
    (
        "marketing.voice.production",
        (
            "Return spoken-script wording that sounds natural when read aloud.",
            "Use short sentences and natural breathing cadence.",
            "Avoid meta commentary, labels, and generic intros.",
        ),
    ),
    (
        "sales.outreach",
        (
            "Return concise outreach copy with a clear CTA.",
            "Lead with a specific problem, trigger, or opportunity.",
            "Avoid generic greetings, filler compliments, and vague promises.",
        ),
    ),
    (
        "operations.workflow",
        (
            "Return an ordered checklist or process flow.",
            "Include owner or role labels when relevant.",
            "Keep the wording operational and specific.",
        ),
    ),
    (
        "design.image.qwen",
        (
            "Return a production-ready image prompt instead of an essay.",
            "Specify subject, style, composition, and any text layout requirements.",
            "Avoid vague adjectives without concrete visual direction.",
        ),
    ),
)


@dataclass
class SkillJudgeConfig:
    model: str
    api_key: str
    api_base_url: str
    temperature: float = 0.0
    timeout_seconds: float = 90.0


@dataclass
class SkillFoundryConfig:
    active_threshold: float = 62.0
    challenger_threshold: float = 56.0
    duplicate_similarity_threshold: float = 0.95
    functional_overlap_threshold: float = 0.87
    competition_margin: float = 6.0
    challenger_margin: float = 3.0
    max_examples: int = 2
    max_active_matches: int = 2
    max_active_skills_per_family: int = 4
    max_challenger_skills_per_family: int = 3
    max_root_domains_per_source: int = 3
    max_runtime_chars: int = 1500
    max_trigger_patterns: int = 10
    max_output_contract_items: int = 5
    max_safety_notes: int = 4
    max_files_per_run: int = 500
    auto_archive_artifacts: bool = True
    rejected_retention_days: int = 0


class LLMFoundryJudge:
    """Optional strong-model reviewer for skill competition."""

    def __init__(self, config: SkillJudgeConfig):
        self.config = config

    def review(
        self,
        candidate: dict[str, Any],
        incumbent: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(candidate, incumbent)
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are PigTex Skill Foundry judge. Score prompt skills strictly. "
                        "Return only valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        endpoint = self.config.api_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            return {}
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Skill Foundry judge returned non-JSON content")
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _build_prompt(candidate: dict[str, Any], incumbent: Optional[dict[str, Any]]) -> str:
        return json.dumps(
            {
                "task": "Evaluate whether the candidate skill should be promoted, kept as challenger, or rejected.",
                "rubric": {
                    "score_delta_range": [-10, 10],
                    "verdicts": ["promote", "challenger", "reject", "keep_incumbent"],
                    "strict_rules": [
                        "Prefer highly specific skills over broad generic prompts.",
                        "Reject duplicates, unsafe prompt patterns, and vague instructions.",
                        "Favor concise high-signal output contracts.",
                    ],
                },
                "candidate": candidate,
                "incumbent": incumbent,
                "required_output": {
                    "score_delta": 0,
                    "verdict": "challenger",
                    "domain_override": "",
                    "title_override": "",
                    "trigger_patterns": [],
                    "notes": [],
                },
            },
            ensure_ascii=False,
        )


class SkillFoundry:
    """Offline skill ingestion + online runtime selection."""

    REGISTRY_FILE = "runtime_registry.json"
    DRAFT_REGISTRY_FILE = "draft_registry.json"
    SKILL_STORE_FILE = "accepted_skill_store.json"
    CATALOG_FILE = "catalog.json"
    REPORTS_DIR = "reports"
    INCOMING_DIR = "incoming"
    RELEASES_DIR = "releases"
    PROCESSED_DIR = "processed"
    ACCEPTED_DIR = "accepted"
    REJECTED_DIR = "rejected"

    def __init__(
        self,
        *,
        data_root: Optional[Path] = None,
        config: Optional[SkillFoundryConfig] = None,
        judge: Optional[LLMFoundryJudge] = None,
    ):
        self.data_root = data_root or self._resolve_skill_root()
        self.config = config or SkillFoundryConfig()
        self.judge = judge
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / self.REPORTS_DIR).mkdir(parents=True, exist_ok=True)
        (self.data_root / self.INCOMING_DIR).mkdir(parents=True, exist_ok=True)
        (self.data_root / self.RELEASES_DIR).mkdir(parents=True, exist_ok=True)
        (self.data_root / self.PROCESSED_DIR / self.ACCEPTED_DIR).mkdir(parents=True, exist_ok=True)
        (self.data_root / self.PROCESSED_DIR / self.REJECTED_DIR).mkdir(parents=True, exist_ok=True)

    @classmethod
    def _resolve_skill_root(cls) -> Path:
        data_dir = PromptPackStore.resolve_data_dir()
        if not data_dir:
            raise RuntimeError("Could not resolve PigTex data directory")
        return data_dir / "skill_foundry"

    def registry_path(self) -> Path:
        return self.data_root / self.REGISTRY_FILE

    def draft_registry_path(self) -> Path:
        return self.data_root / self.DRAFT_REGISTRY_FILE

    def catalog_path(self) -> Path:
        return self.data_root / self.CATALOG_FILE

    def skill_store_path(self) -> Path:
        return self.data_root / self.SKILL_STORE_FILE

    def incoming_path(self) -> Path:
        return self.data_root / self.INCOMING_DIR

    def releases_path(self) -> Path:
        return self.data_root / self.RELEASES_DIR

    def processed_path(self) -> Path:
        return self.data_root / self.PROCESSED_DIR

    def accepted_artifacts_path(self) -> Path:
        return self.processed_path() / self.ACCEPTED_DIR

    def rejected_artifacts_path(self) -> Path:
        return self.processed_path() / self.REJECTED_DIR

    def _workspace_root(self) -> Path:
        resolved_data_root = self.data_root.resolve()
        if resolved_data_root.parent.name == "data":
            return resolved_data_root.parent.parent
        return resolved_data_root.parent

    def _display_path(self, target: Path | str) -> str:
        resolved_target = Path(target).resolve()
        workspace_root = self._workspace_root().resolve()
        try:
            return resolved_target.relative_to(workspace_root).as_posix()
        except ValueError:
            return resolved_target.as_posix()

    def load_registry(self) -> dict[str, Any]:
        path = self.registry_path()
        if not path.exists():
            return self._empty_registry()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skill registry load failed (%s): %s", path, exc)
            return self._empty_registry()
        return payload if isinstance(payload, dict) else self._empty_registry()

    def load_draft_registry(self) -> dict[str, Any]:
        path = self.draft_registry_path()
        if not path.exists():
            return self._empty_registry()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skill draft registry load failed (%s): %s", path, exc)
            return self._empty_registry()
        return payload if isinstance(payload, dict) else self._empty_registry()

    def load_catalog(self) -> dict[str, Any]:
        path = self.catalog_path()
        if not path.exists():
            return self._empty_catalog()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skill catalog load failed (%s): %s", path, exc)
            return self._empty_catalog()
        return payload if isinstance(payload, dict) else self._empty_catalog()

    def load_skill_store(self) -> dict[str, Any]:
        path = self.skill_store_path()
        if not path.exists():
            payload = self._bootstrap_skill_store()
            if payload.get("skills"):
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skill store load failed (%s): %s", path, exc)
            return self._empty_skill_store()
        return payload if isinstance(payload, dict) else self._empty_skill_store()

    def _bootstrap_skill_store(self) -> dict[str, Any]:
        generated_at = datetime.now().isoformat()
        seeded_skills: list[dict[str, Any]] = []
        source = "bootstrap:empty"

        accepted_root = self.accepted_artifacts_path()
        accepted_artifacts = self._scan_artifacts(accepted_root) if accepted_root.exists() else []
        if accepted_artifacts:
            for artifact_path in accepted_artifacts:
                seeded_skills.extend(self._normalize_artifact(artifact_path))
            source = "bootstrap:accepted_artifacts"
        else:
            runtime_registry = self.load_registry()
            draft_registry = self.load_draft_registry()
            catalog = self.load_catalog()
            for item in runtime_registry.get("active_skills", []):
                if isinstance(item, dict):
                    seeded_skills.append(deepcopy(item))
            for item in draft_registry.get("active_skills", []):
                if isinstance(item, dict):
                    seeded_skills.append(deepcopy(item))
            for item in catalog.get("challengers", []):
                if isinstance(item, dict):
                    seeded_skills.append(deepcopy(item))
            if seeded_skills:
                source = "bootstrap:runtime_and_catalog"

        merged = self._merge_corpus_candidates([], seeded_skills)
        store_items = [
            self._annotate_store_item(skill, state="active", seen_at=generated_at, origin=source)
            for skill in merged
        ]
        return {
            "schema_version": "1.0",
            "generated_at": generated_at,
            "skills": store_items,
            "summary": {
                "active_count": len(store_items),
                "challenger_count": 0,
                "retired_count": 0,
                "source": source,
            },
        }

    def _extract_store_corpus_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        corpus: list[dict[str, Any]] = []
        for item in payload.get("skills", []):
            if not isinstance(item, dict):
                continue
            state = str(item.get("warehouse_state") or "").strip().lower()
            if state not in {"active", "challenger", "accepted"}:
                continue
            candidate = deepcopy(item)
            candidate.pop("warehouse_state", None)
            candidate.pop("warehouse_origin", None)
            candidate.pop("warehouse_first_seen_at", None)
            candidate.pop("warehouse_last_seen_at", None)
            candidate.pop("warehouse_retired_at", None)
            candidate.pop("warehouse_last_report_id", None)
            corpus.append(candidate)
        return corpus

    def _merge_corpus_candidates(
        self,
        existing_candidates: list[dict[str, Any]],
        incoming_candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for candidate in [*existing_candidates, *incoming_candidates]:
            if not isinstance(candidate, dict):
                continue
            merged[self._candidate_store_key(candidate)] = deepcopy(candidate)
        return self._dedupe_candidates(list(merged.values()))

    def _candidate_store_key(self, candidate: dict[str, Any]) -> str:
        domain = str(candidate.get("domain") or "").strip().lower()
        title = str(candidate.get("title") or "").strip().lower()
        source = candidate.get("source", {}) if isinstance(candidate.get("source"), dict) else {}
        source_hash = str(source.get("source_hash") or "").strip().lower()
        if not source_hash:
            source_hash = hashlib.sha1(self._candidate_text(candidate).encode("utf-8")).hexdigest()
        return hashlib.sha1(f"{domain}::{title}::{source_hash}".encode("utf-8")).hexdigest()

    def _annotate_store_item(
        self,
        candidate: dict[str, Any],
        *,
        state: str,
        seen_at: str,
        origin: str,
        previous: Optional[dict[str, Any]] = None,
        report_id: Optional[str] = None,
    ) -> dict[str, Any]:
        item = deepcopy(candidate)
        item["warehouse_state"] = state
        item["warehouse_origin"] = origin
        item["warehouse_first_seen_at"] = (
            str(previous.get("warehouse_first_seen_at")).strip()
            if isinstance(previous, dict) and previous.get("warehouse_first_seen_at")
            else seen_at
        )
        item["warehouse_last_seen_at"] = seen_at
        item["warehouse_last_report_id"] = report_id or (previous.get("warehouse_last_report_id") if isinstance(previous, dict) else None)
        retired_at = previous.get("warehouse_retired_at") if isinstance(previous, dict) else None
        item["warehouse_retired_at"] = None if state in {"active", "challenger", "accepted"} else (retired_at or seen_at)
        return item

    def _sync_skill_store(
        self,
        *,
        scored_candidates: list[dict[str, Any]],
        competition: dict[str, Any],
        report: dict[str, Any],
    ) -> None:
        store_payload = self.load_skill_store()
        existing_items = store_payload.get("skills", []) if isinstance(store_payload.get("skills"), list) else []
        existing_by_key = {
            self._candidate_store_key(item): deepcopy(item)
            for item in existing_items
            if isinstance(item, dict)
        }
        active_keys = {
            self._candidate_store_key(item)
            for item in competition.get("active_skills", [])
            if isinstance(item, dict)
        }
        challenger_keys = {
            self._candidate_store_key(item)
            for item in competition.get("challengers", [])
            if isinstance(item, dict)
        }
        report_id = str(report.get("report_id") or "").strip() or None
        seen_at = str(report.get("generated_at") or datetime.now().isoformat())

        updated_items: dict[str, dict[str, Any]] = {}
        for candidate in scored_candidates:
            if not isinstance(candidate, dict):
                continue
            key = self._candidate_store_key(candidate)
            previous = existing_by_key.get(key)
            if key in active_keys:
                updated_items[key] = self._annotate_store_item(
                    candidate,
                    state="active",
                    seen_at=seen_at,
                    origin="compile",
                    previous=previous,
                    report_id=report_id,
                )
            elif key in challenger_keys:
                updated_items[key] = self._annotate_store_item(
                    candidate,
                    state="challenger",
                    seen_at=seen_at,
                    origin="compile",
                    previous=previous,
                    report_id=report_id,
                )
            elif previous:
                updated_items[key] = self._annotate_store_item(
                    candidate,
                    state="retired",
                    seen_at=seen_at,
                    origin="compile",
                    previous=previous,
                    report_id=report_id,
                )

        for key, existing in existing_by_key.items():
            if key not in updated_items:
                updated_items[key] = deepcopy(existing)

        skills = list(updated_items.values())
        skills.sort(key=lambda item: (str(item.get("warehouse_state") or ""), str(item.get("domain") or ""), str(item.get("title") or "")))
        summary = {
            "active_count": sum(1 for item in skills if str(item.get("warehouse_state")) == "active"),
            "challenger_count": sum(1 for item in skills if str(item.get("warehouse_state")) == "challenger"),
            "retired_count": sum(1 for item in skills if str(item.get("warehouse_state")) == "retired"),
            "source": "accepted_corpus",
        }
        self.skill_store_path().write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "generated_at": seen_at,
                    "skills": skills,
                    "summary": summary,
                    "report_id": report_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def compile_from_path(
        self,
        input_path: Path,
        *,
        dry_run: bool = False,
        max_files: Optional[int] = None,
    ) -> dict[str, Any]:
        source_path = input_path.resolve()
        artifacts = self._scan_artifacts(source_path, max_files=max_files)
        baseline = self._load_builtin_baseline_skills()
        incoming_candidates: list[dict[str, Any]] = []
        skill_store = self.load_skill_store()
        accepted_corpus = self._extract_store_corpus_candidates(skill_store)

        for artifact_path in artifacts:
            incoming_candidates.extend(self._normalize_artifact(artifact_path))

        corpus_candidates = self._merge_corpus_candidates(accepted_corpus, incoming_candidates)
        scored_candidates = [self._score_candidate(candidate, [], baseline) for candidate in corpus_candidates]
        competition = self._run_competition(scored_candidates, [], baseline)
        competition = self._apply_redundancy_guardrails(competition)
        artifact_retention = None
        retained_artifacts = list(artifacts)
        if not dry_run and self.config.auto_archive_artifacts:
            artifact_retention = self._apply_artifact_retention(
                artifacts=artifacts,
                scored_candidates=scored_candidates,
                competition=competition,
            )
            retained_artifacts = [
                Path(artifact_retention["artifact_destinations"].get(path.as_posix(), path.as_posix()))
                for path in artifacts
            ]
            if self.config.rejected_retention_days > 0:
                self.cleanup_rejected_artifacts(self.config.rejected_retention_days)
        report = self._build_compile_report(
            source_path,
            retained_artifacts,
            incoming_candidates,
            accepted_corpus,
            scored_candidates,
            competition,
            artifact_retention=artifact_retention,
        )

        if not dry_run:
            self._save_outputs(
                report=report,
                active_skills=competition["active_skills"],
                challengers=competition["challengers"],
                rejected=competition["rejected"],
                target="draft",
            )
            self._sync_skill_store(
                scored_candidates=scored_candidates,
                competition=competition,
                report=report,
            )
        return report

    def resolve_matches(
        self,
        *,
        user_message: str,
        detected_intent: Optional[str] = None,
        keywords: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        registry = self.load_registry()
        active_skills = registry.get("active_skills", [])
        if not isinstance(active_skills, list):
            return []

        text = (user_message or "").strip().lower()
        extracted_keywords = {str(item).strip().lower() for item in (keywords or []) if item}
        vn_language = bool(_VIETNAMESE_CHAR_RE.search(user_message or ""))
        matches: list[tuple[float, dict[str, Any]]] = []

        for raw_skill in active_skills:
            if not isinstance(raw_skill, dict):
                continue
            score = self._score_runtime_match(
                raw_skill,
                text=text,
                detected_intent=detected_intent,
                extracted_keywords=extracted_keywords,
                prefer_vietnamese=vn_language,
            )
            if score < 0.5:
                continue
            skill = deepcopy(raw_skill)
            skill["runtime_match_score"] = round(score, 3)
            matches.append((score, skill))

        matches.sort(key=lambda item: (item[0], float(item[1].get("score_total", 0.0))), reverse=True)
        return [skill for _, skill in matches[: self.config.max_active_matches]]

    @staticmethod
    def _normalize_domain(value: Any) -> str:
        return str(value or "").strip().lower()

    def _domain_output_contract_fallback(self, domain: str) -> list[str]:
        normalized_domain = self._normalize_domain(domain)
        if not normalized_domain:
            return []
        best_prefix = ""
        best_contract: tuple[str, ...] = ()
        for prefix, contract in _OUTPUT_CONTRACT_FALLBACKS:
            if normalized_domain == prefix or normalized_domain.startswith(f"{prefix}."):
                if len(prefix) > len(best_prefix):
                    best_prefix = prefix
                    best_contract = contract
        return list(best_contract)

    def is_monetization_domain(self, value: Any) -> bool:
        normalized_domain = self._normalize_domain(value)
        if not normalized_domain:
            return False
        return any(
            normalized_domain == prefix.rstrip(".")
            or normalized_domain.startswith(prefix)
            for prefix in _MONETIZATION_DOMAIN_PREFIXES
        )

    def get_effective_output_contract(self, skill: dict[str, Any]) -> list[str]:
        explicit = self._ensure_list(skill.get("output_contract"), fallback_as_single=True)
        if explicit:
            return explicit
        return self._domain_output_contract_fallback(str(skill.get("domain") or ""))

    def format_runtime_skills(self, skills: list[dict[str, Any]]) -> str:
        if not skills:
            return ""

        sections: list[str] = []
        current_chars = 0
        for index, skill in enumerate(skills):
            title = str(skill.get("title") or skill.get("domain") or "Skill").strip()
            domain = str(skill.get("domain") or "general").strip()
            role_label = "Champion skill" if index == 0 else "Support skill"
            lines = [f"### {title}", f"- Role: {role_label}", f"- Domain: {domain}"]
            for instruction in self._ensure_list(skill.get("instruction_core"), fallback_as_single=True)[:2]:
                lines.append(f"- Apply: {instruction}")
            for contract in self.get_effective_output_contract(skill)[:2]:
                lines.append(f"- Output contract: {contract}")
            formatted = "\n".join(lines)
            if current_chars + len(formatted) > self.config.max_runtime_chars and sections:
                break
            sections.append(formatted)
            current_chars += len(formatted)
        return "\n\n".join(sections)

    def format_runtime_output_contracts(self, skills: list[dict[str, Any]]) -> str:
        if not skills:
            return ""

        sections: list[str] = []
        current_chars = 0
        for index, skill in enumerate(skills):
            contracts = self.get_effective_output_contract(skill)
            if not contracts:
                continue
            title = str(skill.get("title") or skill.get("domain") or "Skill").strip()
            role_label = "Champion" if index == 0 else "Support"
            lines = [f"### {role_label} Contract: {title}"]
            lines.extend(f"- {contract}" for contract in contracts[:4])
            formatted = "\n".join(lines)
            if current_chars + len(formatted) > max(320, int(self.config.max_runtime_chars * 0.55)) and sections:
                break
            sections.append(formatted)
            current_chars += len(formatted)
        return "\n\n".join(sections)

    def registry_summary(self) -> dict[str, Any]:
        registry = self.load_registry()
        draft_registry = self.load_draft_registry()
        catalog = self.load_catalog()
        skill_store = self.load_skill_store()
        active = registry.get("active_skills", [])
        draft = draft_registry.get("active_skills", [])
        active_count = len(active) if isinstance(active, list) else 0
        draft_count = len(draft) if isinstance(draft, list) else 0
        challengers = catalog.get("challengers", [])
        rejected = catalog.get("rejected", [])
        return {
            "active_skill_count": active_count,
            "draft_skill_count": draft_count,
            "challenger_count": len(challengers) if isinstance(challengers, list) else 0,
            "rejected_count": len(rejected) if isinstance(rejected, list) else 0,
            "generated_at": registry.get("generated_at"),
            "draft_generated_at": draft_registry.get("generated_at"),
            "registry_path": str(self.registry_path()),
            "draft_registry_path": str(self.draft_registry_path()),
            "skill_store_path": str(self.skill_store_path()),
            "accepted_corpus_count": len(skill_store.get("skills", [])) if isinstance(skill_store.get("skills"), list) else 0,
            "incoming_path": str(self.incoming_path()),
            "release_count": len(self.list_releases()),
            "publish_gate": self.evaluate_publish_gate(draft_registry=draft_registry, catalog=catalog),
        }

    def evaluate_publish_gate(
        self,
        *,
        draft_registry: Optional[dict[str, Any]] = None,
        catalog: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        draft = deepcopy(draft_registry) if isinstance(draft_registry, dict) else self.load_draft_registry()
        catalog_payload = deepcopy(catalog) if isinstance(catalog, dict) else self.load_catalog()
        runtime_registry = self.load_registry()

        draft_skills = draft.get("active_skills", [])
        if not isinstance(draft_skills, list):
            draft_skills = []
        challenger_skills = catalog_payload.get("challengers", [])
        if not isinstance(challenger_skills, list):
            challenger_skills = []
        rejected_skills = catalog_payload.get("rejected", [])
        if not isinstance(rejected_skills, list):
            rejected_skills = []

        blockers: list[str] = []
        warnings: list[str] = []

        if not draft_skills:
            blockers.append("no_active_skills_in_draft")
        if not draft.get("report_id"):
            blockers.append("missing_report_id")
        if not draft.get("generated_at"):
            blockers.append("missing_generated_at")

        average_score = self._safe_float(draft.get("summary", {}).get("avg_score"))
        if average_score is None:
            blockers.append("missing_average_score")
        elif average_score < self.config.active_threshold:
            blockers.append("average_score_below_active_threshold")

        low_score_active = sum(
            1
            for skill in draft_skills
            if self._safe_float(skill.get("score_total")) is not None
            and float(skill.get("score_total")) < self.config.challenger_threshold
        )
        if low_score_active:
            blockers.append("draft_contains_low_score_active_skills")

        missing_output_contract = sum(
            1 for skill in draft_skills if not self._ensure_list(skill.get("output_contract"), fallback_as_single=True)
        )
        if draft_skills and missing_output_contract / max(1, len(draft_skills)) >= 0.45:
            warnings.append("many_active_skills_missing_output_contract")

        monetization_skills = [skill for skill in draft_skills if self.is_monetization_domain(skill.get("domain"))]
        missing_effective_monetization_contract = sum(
            1 for skill in monetization_skills if not self.get_effective_output_contract(skill)
        )
        if monetization_skills and missing_effective_monetization_contract:
            blockers.append("monetization_skills_missing_effective_output_contract")
        monetization_fallback_contracts = sum(
            1
            for skill in monetization_skills
            if not self._ensure_list(skill.get("output_contract"), fallback_as_single=True)
            and self.get_effective_output_contract(skill)
        )
        if monetization_skills and monetization_fallback_contracts:
            warnings.append("monetization_skills_using_fallback_contracts")

        if len(challenger_skills) > max(24, len(draft_skills) * 3):
            warnings.append("challenger_pool_is_large")
        if len(rejected_skills) > max(200, len(draft_skills) * 10):
            warnings.append("rejected_pool_is_large")
        if len(draft_skills) > 320:
            warnings.append("draft_registry_is_large")
        redundancy_pruned_count = int(draft.get("summary", {}).get("redundancy_pruned_count", 0) or 0)
        if redundancy_pruned_count:
            warnings.append("automatic_redundancy_pruning_applied")

        runtime_empty = not isinstance(runtime_registry.get("active_skills"), list) or not runtime_registry.get("active_skills")
        return {
            "ready": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "runtime_empty": runtime_empty,
            "draft_skill_count": len(draft_skills),
            "challenger_count": len(challenger_skills),
            "rejected_count": len(rejected_skills),
            "average_score": average_score,
            "active_threshold": self.config.active_threshold,
            "challenger_threshold": self.config.challenger_threshold,
            "redundancy_pruned_count": redundancy_pruned_count,
        }

    def _empty_registry(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "generated_at": None,
            "active_skills": [],
            "summary": {},
        }

    def _empty_catalog(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "generated_at": None,
            "challengers": [],
            "rejected": [],
            "reports": [],
        }

    def _empty_skill_store(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "generated_at": None,
            "skills": [],
            "summary": {
                "active_count": 0,
                "challenger_count": 0,
                "retired_count": 0,
                "source": "empty",
            },
        }

    def _save_outputs(
        self,
        *,
        report: dict[str, Any],
        active_skills: list[dict[str, Any]],
        challengers: list[dict[str, Any]],
        rejected: list[dict[str, Any]],
        target: str = "draft",
    ) -> None:
        registry_payload = {
            "schema_version": "1.0",
            "generated_at": report["generated_at"],
            "active_skills": active_skills,
            "summary": report["summary"],
            "report_id": report["report_id"],
            "state": target,
        }
        output_path = self.draft_registry_path() if target == "draft" else self.registry_path()
        output_path.write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        catalog = self.load_catalog()
        reports = catalog.get("reports", [])
        if not isinstance(reports, list):
            reports = []
        reports.append(
            {
                "generated_at": report["generated_at"],
                "summary": report["summary"],
                "source_path": report["source_path"],
                "report_path": self._display_path(self.data_root / self.REPORTS_DIR / f"compile-{report['report_id']}.json"),
                "report_id": report["report_id"],
                "artifact_retention": {
                    "enabled": bool(report.get("artifact_retention", {}).get("enabled")),
                    "moved_count": int(report.get("artifact_retention", {}).get("moved_count", 0) or 0),
                    "accepted_artifact_count": int(report.get("artifact_retention", {}).get("accepted_artifact_count", 0) or 0),
                    "rejected_artifact_count": int(report.get("artifact_retention", {}).get("rejected_artifact_count", 0) or 0),
                    "sample_moved_items": list(report.get("artifact_retention", {}).get("moved_items", []))[:6],
                },
                "redundancy_pruning": {
                    "enabled": bool(report.get("redundancy_pruning", {}).get("enabled")),
                    "pruned_count": int(report.get("redundancy_pruning", {}).get("pruned_count", 0) or 0),
                    "sample_actions": list(report.get("redundancy_pruning", {}).get("sample_actions", []))[:8],
                },
            }
        )
        catalog_payload = {
            "schema_version": "1.0",
            "generated_at": report["generated_at"],
            "challengers": challengers,
            "rejected": rejected,
            "draft_summary": report["summary"],
            "draft_report_id": report["report_id"],
            "reports": reports[-20:],
        }
        self.catalog_path().write_text(
            json.dumps(catalog_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        report_path = self.data_root / self.REPORTS_DIR / f"compile-{report['report_id']}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_admin_overview(self) -> dict[str, Any]:
        return {
            "summary": self.registry_summary(),
            "active_registry": self.load_registry(),
            "draft_registry": self.load_draft_registry(),
            "catalog": self.load_catalog(),
            "releases": self.list_releases(),
            "publish_gate": self.evaluate_publish_gate(),
        }

    def list_releases(self) -> list[dict[str, Any]]:
        releases: list[dict[str, Any]] = []
        for path in sorted(self.releases_path().glob("release-*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Skill release load failed (%s): %s", path, exc)
                continue
            if not isinstance(payload, dict):
                continue
            registry = payload.get("registry", {})
            active_skills = registry.get("active_skills", []) if isinstance(registry, dict) else []
            releases.append(
                {
                    "release_id": str(payload.get("release_id") or path.stem.replace("release-", "")),
                    "released_at": payload.get("released_at"),
                    "released_by": payload.get("released_by"),
                    "note": payload.get("note"),
                    "active_skill_count": len(active_skills) if isinstance(active_skills, list) else 0,
                    "path": self._display_path(path),
                }
            )
        return releases

    def cleanup_rejected_artifacts(self, retention_days: int) -> dict[str, Any]:
        safe_days = max(0, int(retention_days or 0))
        if safe_days <= 0:
            return {"deleted_count": 0, "retention_days": safe_days}

        cutoff = datetime.now().timestamp() - (safe_days * 86400)
        deleted_count = 0
        for path in sorted(self.rejected_artifacts_path().rglob("*"), reverse=True):
            if not path.exists():
                continue
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                deleted_count += 1
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        return {"deleted_count": deleted_count, "retention_days": safe_days}

    def publish_draft(self, *, released_by: str, note: str = "", force: bool = False) -> dict[str, Any]:
        draft_registry = self.load_draft_registry()
        active_skills = draft_registry.get("active_skills", [])
        if not isinstance(active_skills, list) or not active_skills:
            raise ValueError("No draft registry available to publish")
        publish_gate = self.evaluate_publish_gate(draft_registry=draft_registry)
        if not force and not publish_gate.get("ready"):
            blocker_text = ", ".join(publish_gate.get("blockers", [])) or "publish_gate_blocked"
            raise ValueError(f"Draft registry failed publish gate: {blocker_text}")

        released_at = datetime.now().isoformat()
        release_id = self._make_release_id(f"publish::{released_by}::{note}")
        active_payload = {
            "schema_version": "1.0",
            "generated_at": draft_registry.get("generated_at"),
            "active_skills": active_skills,
            "summary": draft_registry.get("summary", {}),
            "report_id": draft_registry.get("report_id"),
            "release_id": release_id,
            "released_at": released_at,
            "released_by": released_by,
            "note": note.strip() or None,
            "state": "active",
            "publish_gate": publish_gate,
        }
        self.registry_path().write_text(
            json.dumps(active_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        release_path = self.releases_path() / f"release-{release_id}.json"
        release_path.write_text(
            json.dumps(
                {
                    "release_id": release_id,
                    "released_at": released_at,
                    "released_by": released_by,
                    "note": active_payload["note"],
                    "registry": active_payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "release_id": release_id,
            "released_at": released_at,
            "released_by": released_by,
            "note": active_payload["note"],
            "active_skill_count": len(active_skills),
            "publish_gate": publish_gate,
        }

    def rollback_release(self, release_id: str, *, rolled_back_by: str, note: str = "") -> dict[str, Any]:
        normalized_release_id = (release_id or "").strip()
        if not normalized_release_id:
            raise ValueError("release_id is required")

        release_path = self.releases_path() / f"release-{normalized_release_id}.json"
        if not release_path.exists():
            raise ValueError("Release not found")

        payload = json.loads(release_path.read_text(encoding="utf-8"))
        registry = payload.get("registry", {}) if isinstance(payload, dict) else {}
        if not isinstance(registry, dict):
            raise ValueError("Release payload is invalid")

        restored_payload = deepcopy(registry)
        restored_payload["rolled_back_at"] = datetime.now().isoformat()
        restored_payload["rolled_back_by"] = rolled_back_by
        restored_payload["rollback_note"] = note.strip() or None
        self.registry_path().write_text(
            json.dumps(restored_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "release_id": normalized_release_id,
            "rolled_back_at": restored_payload["rolled_back_at"],
            "rolled_back_by": rolled_back_by,
            "active_skill_count": len(restored_payload.get("active_skills", [])),
        }

    @staticmethod
    def _make_release_id(seed: str) -> str:
        now = datetime.now().strftime("%Y%m%d%H%M%S")
        suffix = hashlib.sha1(f"{seed}::{datetime.now().isoformat()}".encode("utf-8")).hexdigest()[:8]
        return f"{now}-{suffix}"

    def _scan_artifacts(self, source_path: Path, *, max_files: Optional[int] = None) -> list[Path]:
        if source_path.is_file():
            return [source_path]

        allowed_suffixes = {".md", ".markdown", ".json"}
        discovered = [
            path
            for path in sorted(source_path.rglob("*"))
            if path.is_file() and path.suffix.lower() in allowed_suffixes
        ]
        return discovered[: max_files or self.config.max_files_per_run]

    def _apply_artifact_retention(
        self,
        *,
        artifacts: list[Path],
        scored_candidates: list[dict[str, Any]],
        competition: dict[str, Any],
    ) -> dict[str, Any]:
        artifact_status = self._classify_artifact_status(artifacts, scored_candidates)
        moved_items: list[dict[str, Any]] = []
        destinations: dict[str, str] = {}
        incoming_root = self.incoming_path().resolve()

        for artifact in artifacts:
            if not artifact.exists():
                continue
            try:
                relative_path = artifact.resolve().relative_to(incoming_root)
            except ValueError:
                continue

            display_source_path = self._display_path(artifact)
            status_name = artifact_status.get(display_source_path, "rejected")
            target_root = self.accepted_artifacts_path() if status_name == "accepted" else self.rejected_artifacts_path()
            destination = self._resolve_archive_destination(target_root, relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(artifact), str(destination))
            display_destination_path = self._display_path(destination)
            destinations[display_source_path] = display_destination_path
            moved_items.append(
                {
                    "source_path": display_source_path,
                    "destination_path": display_destination_path,
                    "status": status_name,
                }
            )
            self._cleanup_empty_incoming_dirs(artifact.parent, incoming_root)

        if destinations:
            self._rewrite_source_paths(scored_candidates, destinations)
            self._rewrite_source_paths(competition.get("active_skills", []), destinations)
            self._rewrite_source_paths(competition.get("challengers", []), destinations)
            self._rewrite_source_paths(competition.get("rejected", []), destinations)

        accepted_count = sum(1 for item in moved_items if item["status"] == "accepted")
        rejected_count = sum(1 for item in moved_items if item["status"] == "rejected")
        return {
            "enabled": True,
            "moved_count": len(moved_items),
            "accepted_artifact_count": accepted_count,
            "rejected_artifact_count": rejected_count,
            "artifact_destinations": destinations,
            "moved_items": moved_items,
        }

    def _classify_artifact_status(
        self,
        artifacts: list[Path],
        scored_candidates: list[dict[str, Any]],
    ) -> dict[str, str]:
        status_map = {self._display_path(artifact): "rejected" for artifact in artifacts}
        for candidate in scored_candidates:
            source = candidate.get("source", {}) if isinstance(candidate, dict) else {}
            source_path = str(source.get("file_path") or "").strip()
            if not source_path:
                continue
            verdict = str(candidate.get("competition", {}).get("verdict") or "").strip().lower()
            if verdict in {"promote", "challenger"}:
                status_map[source_path] = "accepted"
        return status_map

    def _resolve_archive_destination(self, target_root: Path, relative_path: Path) -> Path:
        destination = target_root / relative_path
        if not destination.exists():
            return destination

        suffix = datetime.now().strftime("%Y%m%d%H%M%S")
        stem = destination.stem
        ext = destination.suffix
        return destination.with_name(f"{stem}-{suffix}{ext}")

    def _rewrite_source_paths(self, items: Any, destinations: dict[str, str]) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            if not isinstance(source, dict):
                continue
            current = str(source.get("file_path") or "").strip()
            if current and current in destinations:
                source["file_path"] = destinations[current]

    @staticmethod
    def _cleanup_empty_incoming_dirs(path: Path, incoming_root: Path) -> None:
        current = path
        while current != incoming_root and incoming_root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _normalize_artifact(self, artifact_path: Path) -> list[dict[str, Any]]:
        suffix = artifact_path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            return self._normalize_markdown_artifact(artifact_path)
        if suffix == ".json":
            return self._normalize_json_artifact(artifact_path)
        return []

    def _normalize_markdown_artifact(self, artifact_path: Path) -> list[dict[str, Any]]:
        raw_text = artifact_path.read_text(encoding="utf-8", errors="ignore")
        normalized_text = self._normalize_text(raw_text)
        if not normalized_text:
            return []

        frontmatter, body = self._extract_frontmatter(normalized_text)
        title = (
            frontmatter.get("title")
            or frontmatter.get("name")
            or artifact_path.stem.replace("_", " ").replace("-", " ").title()
        )

        candidates = [self._build_skill_candidate(title, body, artifact_path, section_path=[])]
        headings = list(_SPLIT_HEADING_RE.finditer(body))
        for index, match in enumerate(headings):
            start = match.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            section_text = body[start:end].strip()
            if len(section_text) < 180:
                continue
            section_title = match.group("title").strip()
            candidates.append(
                self._build_skill_candidate(
                    f"{title} :: {section_title}",
                    section_text,
                    artifact_path,
                    section_path=[section_title],
                )
            )
        return [candidate for candidate in candidates if candidate]

    def _normalize_json_artifact(self, artifact_path: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skill JSON parse failed (%s): %s", artifact_path, exc)
            return []
        return self._extract_json_skill_candidates(payload, artifact_path, node_path=[])

    def _extract_json_skill_candidates(
        self,
        node: Any,
        artifact_path: Path,
        *,
        node_path: list[str],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        if isinstance(node, dict):
            skill_text = self._extract_json_instruction_text(node)
            if skill_text:
                title = str(
                    node.get("title")
                    or node.get("name")
                    or node.get("id")
                    or " ".join(node_path)
                    or artifact_path.stem
                ).strip()
                candidates.append(
                    self._build_skill_candidate(
                        title,
                        skill_text,
                        artifact_path,
                        section_path=node_path,
                        metadata=node,
                    )
                )
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    candidates.extend(
                        self._extract_json_skill_candidates(
                            value,
                            artifact_path,
                            node_path=[*node_path, str(key)],
                        )
                    )
        elif isinstance(node, list):
            for index, item in enumerate(node):
                candidates.extend(
                    self._extract_json_skill_candidates(
                        item,
                        artifact_path,
                        node_path=[*node_path, str(index)],
                    )
                )
        return [candidate for candidate in candidates if candidate]

    @staticmethod
    def _extract_json_instruction_text(node: dict[str, Any]) -> str:
        fields = (
            "instruction",
            "instructions",
            "prompt",
            "system_instruction",
            "inject",
            "master_prompt_template",
            "template",
            "description",
            "output_contract",
        )
        lines: list[str] = []
        for field_name in fields:
            value = node.get(field_name)
            if isinstance(value, str) and value.strip():
                lines.append(value.strip())
            elif isinstance(value, list):
                parts = [str(item).strip() for item in value if str(item).strip()]
                if parts:
                    lines.append("\n".join(parts))
        return "\n\n".join(lines).strip()

    def _build_skill_candidate(
        self,
        title: str,
        body: str,
        artifact_path: Path,
        *,
        section_path: list[str],
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        normalized_body = self._normalize_text(body)
        if len(normalized_body) < 90:
            return None

        domain = self._derive_domain(artifact_path, title, normalized_body, section_path)
        output_contract = self._extract_output_contract(normalized_body, metadata)
        triggers = self._derive_trigger_patterns(artifact_path, title, normalized_body, section_path)
        safety_notes = self._extract_safety_notes(normalized_body, metadata)
        anti_triggers = self._extract_anti_triggers(normalized_body)
        examples = self._extract_examples(normalized_body)
        source_hash = hashlib.sha1(normalized_body.encode("utf-8")).hexdigest()
        display_artifact_path = self._display_path(artifact_path)
        skill_id = hashlib.sha1(
            f"{display_artifact_path}::{title}::{source_hash}".encode("utf-8")
        ).hexdigest()
        language = "vi" if _VIETNAMESE_CHAR_RE.search(normalized_body) else "en"

        return {
            "skill_id": skill_id,
            "title": title.strip(),
            "domain": domain,
            "language": language,
            "instruction_core": self._extract_instruction_core(normalized_body),
            "output_contract": output_contract,
            "trigger_patterns": triggers[: self.config.max_trigger_patterns],
            "anti_triggers": anti_triggers,
            "examples": examples[: self.config.max_examples],
            "safety_notes": safety_notes[: self.config.max_safety_notes],
            "token_budget": self._estimate_tokens(normalized_body),
            "source": {
                "file_path": display_artifact_path,
                "section_path": section_path,
                "source_hash": source_hash,
            },
            "selection_hints": self._build_selection_hints(domain, triggers, language),
            "metadata": {
                "section_depth": len(section_path),
                "path_tokens": self._derive_path_tokens(artifact_path),
            },
        }

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        normalized = re.sub(r"[ \t]+\n", "\n", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    @staticmethod
    def _extract_frontmatter(text: str) -> tuple[dict[str, str], str]:
        match = _FRONTMATTER_RE.match(text)
        if not match:
            return {}, text
        payload: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            if key and value:
                payload[key] = value
        return payload, text[match.end():].lstrip()

    def _derive_domain(
        self,
        artifact_path: Path,
        title: str,
        body: str,
        section_path: list[str],
    ) -> str:
        text = f"{title}\n{body}".lower()
        best_domain = "general"
        best_score = 0
        for domain, keywords in _DOMAIN_KEYWORDS:
            score = sum(1 for keyword in keywords if keyword in text)
            if score > best_score:
                best_domain = domain
                best_score = score

        path_tokens = self._derive_path_tokens(artifact_path)
        specific_tokens = []
        for token in [*path_tokens, *self._tokenize(title), *(self._tokenize(" ".join(section_path)))]:
            if token in _GENERIC_PATH_TOKENS or token in {"vi", "en"}:
                continue
            if len(token) <= 2 or token in specific_tokens:
                continue
            specific_tokens.append(token)
            if len(specific_tokens) >= 2:
                break

        if best_domain == "general":
            if specific_tokens:
                return ".".join(["general", *specific_tokens[:2]])
            return "general.default"

        domain_parts = best_domain.split(".")
        for token in specific_tokens:
            if token not in domain_parts:
                domain_parts.append(token)
            if len(domain_parts) >= 5:
                break
        return ".".join(domain_parts)

    def _derive_trigger_patterns(
        self,
        artifact_path: Path,
        title: str,
        body: str,
        section_path: list[str],
    ) -> list[str]:
        quoted_terms = re.findall(r'"([^"\n]{3,80})"', body)
        candidates = [
            *self._derive_path_tokens(artifact_path),
            *self._tokenize(title),
            *self._tokenize(" ".join(section_path)),
        ]
        for phrase in quoted_terms[:6]:
            normalized = " ".join(phrase.split()).strip().lower()
            if normalized:
                candidates.append(normalized)

        hit_phrases: list[str] = []
        lowered_body = body.lower()
        for domain, keywords in _DOMAIN_KEYWORDS:
            if domain.split(".")[0] in lowered_body:
                hit_phrases.extend(keywords[:3])

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in [*candidates, *hit_phrases]:
            normalized = " ".join(str(item).strip().lower().split())
            if (
                not normalized
                or len(normalized) <= 2
                or normalized in seen
                or normalized in _GENERIC_PATH_TOKENS
                or normalized in {"pro", "default"}
            ):
                continue
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned

    def _extract_output_contract(
        self,
        body: str,
        metadata: Optional[dict[str, Any]],
    ) -> list[str]:
        contracts: list[str] = []
        lowered = body.lower()
        markers = (
            "output contract",
            "output:",
            "return ",
            "must include",
            "format:",
            "result:",
            "deliverable",
        )
        for paragraph in self._split_paragraphs(body):
            paragraph_lower = paragraph.lower()
            if any(marker in paragraph_lower for marker in markers):
                lines = [line.strip("-* ").strip() for line in paragraph.splitlines() if line.strip()]
                marker_lines = [
                    line for line in lines
                    if not any(marker in line.lower() for marker in ("output contract", "output:", "format:", "result:"))
                ]
                if marker_lines:
                    contracts.extend(marker_lines)
                else:
                    contracts.append(paragraph.strip())
        if metadata and isinstance(metadata.get("output_contract"), list):
            for item in metadata["output_contract"]:
                value = str(item).strip()
                if value:
                    contracts.append(value)
        if not contracts and "must " in lowered:
            contracts.extend(
                sentence.strip()
                for sentence in re.split(r"(?<=[.!?])\s+", body)
                if "must " in sentence.lower()
            )
        return self._normalize_items(contracts, self.config.max_output_contract_items)

    def _extract_safety_notes(
        self,
        body: str,
        metadata: Optional[dict[str, Any]],
    ) -> list[str]:
        notes: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", body):
            lowered = sentence.lower()
            if any(token in lowered for token in ("do not", "never", "avoid", "unsafe", "risk", "safety")):
                notes.append(sentence.strip())
        if metadata and isinstance(metadata.get("safety_notes"), list):
            for item in metadata["safety_notes"]:
                value = str(item).strip()
                if value:
                    notes.append(value)
        return self._normalize_items(notes, self.config.max_safety_notes)

    @staticmethod
    def _extract_anti_triggers(body: str) -> list[str]:
        anti_markers = {
            "not for medical advice": "medical",
            "not for legal advice": "legal",
            "avoid coding": "coding",
            "avoid generic answers": "generic",
        }
        lowered = body.lower()
        hits = [label for marker, label in anti_markers.items() if marker in lowered]
        return hits[:4]

    @staticmethod
    def _extract_examples(body: str) -> list[str]:
        examples: list[str] = []
        for line in body.splitlines():
            normalized = line.strip()
            if normalized.startswith(("Example:", "Examples:", "- Example", "* Example")):
                examples.append(normalized)
        return examples[:2]

    @staticmethod
    def _extract_instruction_core(body: str) -> str:
        paragraphs = [paragraph for paragraph in re.split(r"\n{2,}", body) if paragraph.strip()]
        if not paragraphs:
            return body[:600].strip()
        selected = paragraphs[:3]
        return "\n\n".join(selected).strip()[:1400].rstrip()

    def _build_selection_hints(self, domain: str, triggers: list[str], language: str) -> dict[str, Any]:
        intent_allowlist = [
            intent
            for intent, fragments in _INTENT_HINTS.items()
            if any(fragment in domain for fragment in fragments)
        ]
        return {
            "intent_allowlist": intent_allowlist[:3],
            "keyword_allowlist": triggers[:6],
            "language": language,
        }

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9_]{3,}", (text or "").lower())

    def _derive_path_tokens(self, artifact_path: Path) -> list[str]:
        tokens: list[str] = []
        lowered_parts = [part.lower() for part in artifact_path.parts]
        tail_parts = artifact_path.parts[-3:]
        if "incoming" in lowered_parts:
            incoming_index = max(index for index, part in enumerate(lowered_parts) if part == "incoming")
            tail_parts = artifact_path.parts[incoming_index + 1:]
        for part in tail_parts:
            for token in self._tokenize(part.replace("-", "_")):
                if token not in tokens:
                    tokens.append(token)
        return tokens

    @staticmethod
    def _split_paragraphs(body: str) -> list[str]:
        return [paragraph.strip() for paragraph in re.split(r"\n{2,}", body) if paragraph.strip()]

    @staticmethod
    def _normalize_items(items: Iterable[str], limit: int) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            value = " ".join(str(item).split()).strip()
            key = value.lower()
            if not value or key in seen:
                continue
            seen.add(key)
            normalized.append(value)
            if len(normalized) >= limit:
                break
        return normalized

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, int(len((text or "").split()) * 1.3))

    def _dedupe_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        for candidate in candidates:
            text = self._candidate_text(candidate)
            duplicate_hit = False
            for existing in deduped:
                if candidate.get("domain") != existing.get("domain"):
                    continue
                similarity = self._text_similarity(text, self._candidate_text(existing))
                if similarity >= self.config.duplicate_similarity_threshold:
                    duplicate_hit = True
                    break
            if not duplicate_hit:
                deduped.append(candidate)
        return deduped

    def _score_candidate(
        self,
        candidate: dict[str, Any],
        incumbents: list[dict[str, Any]],
        baseline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        item = deepcopy(candidate)
        instruction_text = self._candidate_text(item)
        hard_rejects: list[str] = []

        for pattern in _DANGEROUS_SKILL_PATTERNS:
            if pattern.search(instruction_text):
                hard_rejects.append("unsafe_override_pattern")
                break
        if str(item.get("domain", "")).startswith("general.") or len(item.get("domain", "").split(".")) < 2:
            hard_rejects.append("domain_too_generic")
        if not item.get("trigger_patterns"):
            hard_rejects.append("missing_triggers")
        if self._estimate_tokens(instruction_text) > 650:
            hard_rejects.append("token_budget_too_large")

        domain_depth = min(10.0, len(item.get("domain", "").split(".")) * 2.0)
        specificity_score = min(15.0, domain_depth + min(5, len(item.get("trigger_patterns", []))))
        trigger_score = min(15.0, len(item.get("trigger_patterns", [])) * 1.8)
        instruction_score = min(15.0, 6.0 + (len(item.get("instruction_core", "")) / 120.0))
        output_contract_score = min(10.0, len(item.get("output_contract", [])) * 2.5)
        safety_score = min(10.0, 4.0 + (len(item.get("safety_notes", [])) * 1.5))
        token_budget = int(item.get("token_budget") or 0)
        if token_budget <= 240:
            token_efficiency_score = 10.0
        elif token_budget <= 360:
            token_efficiency_score = 8.0
        elif token_budget <= 500:
            token_efficiency_score = 5.0
        else:
            token_efficiency_score = 2.0
        examples_score = 10.0 if item.get("examples") else 4.0
        provenance_score = 15.0 if item.get("source", {}).get("file_path") else 6.0

        incumbent, incumbent_similarity = self._find_best_incumbent(item, [*incumbents, *baseline])
        competition_penalty = 0.0
        if incumbent_similarity >= 0.88:
            competition_penalty = 10.0
        elif incumbent_similarity >= 0.75:
            competition_penalty = 4.0

        score_total = round(
            specificity_score
            + trigger_score
            + instruction_score
            + output_contract_score
            + safety_score
            + token_efficiency_score
            + examples_score
            + provenance_score
            - competition_penalty,
            1,
        )

        item["score_breakdown"] = {
            "specificity": round(specificity_score, 2),
            "trigger_clarity": round(trigger_score, 2),
            "instruction_quality": round(instruction_score, 2),
            "output_contract": round(output_contract_score, 2),
            "safety": round(safety_score, 2),
            "token_efficiency": round(token_efficiency_score, 2),
            "examples": round(examples_score, 2),
            "provenance": round(provenance_score, 2),
            "competition_penalty": round(competition_penalty, 2),
        }
        item["score_total"] = score_total
        item["hard_rejects"] = hard_rejects
        item["nearest_incumbent"] = incumbent
        item["nearest_incumbent_similarity"] = round(incumbent_similarity, 3)

        if self.judge and not hard_rejects:
            item = self._apply_judge_review(item)
        return item

    def _apply_judge_review(self, item: dict[str, Any]) -> dict[str, Any]:
        incumbent = item.get("nearest_incumbent")
        try:
            review = self.judge.review(item, incumbent if isinstance(incumbent, dict) else None)
        except Exception as exc:
            logger.warning("Skill judge failed for %s: %s", item.get("title"), exc)
            return item

        if not review:
            return item

        delta = review.get("score_delta", 0)
        try:
            delta_value = max(-10.0, min(10.0, float(delta)))
        except (TypeError, ValueError):
            delta_value = 0.0
        item["score_total"] = round(float(item.get("score_total", 0.0)) + delta_value, 1)

        domain_override = str(review.get("domain_override", "")).strip().lower()
        if domain_override:
            item["domain"] = domain_override
        title_override = str(review.get("title_override", "")).strip()
        if title_override:
            item["title"] = title_override
        trigger_patterns = review.get("trigger_patterns")
        if isinstance(trigger_patterns, list):
            item["trigger_patterns"] = self._normalize_items(
                [str(entry) for entry in trigger_patterns],
                self.config.max_trigger_patterns,
            )
        item["judge_review"] = {
            "verdict": str(review.get("verdict", "")).strip().lower(),
            "score_delta": delta_value,
            "notes": self._normalize_items(review.get("notes", []) if isinstance(review.get("notes"), list) else [], 4),
        }
        return item

    def _find_best_incumbent(
        self,
        candidate: dict[str, Any],
        incumbents: list[dict[str, Any]],
    ) -> tuple[Optional[dict[str, Any]], float]:
        best_skill: Optional[dict[str, Any]] = None
        best_similarity = 0.0
        candidate_text = self._candidate_text(candidate)
        candidate_domain = str(candidate.get("domain") or "")
        for incumbent in incumbents:
            if not isinstance(incumbent, dict):
                continue
            incumbent_domain = str(incumbent.get("domain") or "")
            if not incumbent_domain:
                continue
            if not (
                candidate_domain == incumbent_domain
                or candidate_domain.startswith(f"{incumbent_domain}.")
                or incumbent_domain.startswith(f"{candidate_domain}.")
            ):
                continue
            similarity = self._text_similarity(candidate_text, self._candidate_text(incumbent))
            if similarity > best_similarity:
                best_similarity = similarity
                best_skill = incumbent
        return deepcopy(best_skill) if best_skill else None, best_similarity

    @staticmethod
    def _candidate_text(candidate: dict[str, Any]) -> str:
        sections = [
            str(candidate.get("title") or "").strip(),
            str(candidate.get("instruction_core") or "").strip(),
            "\n".join(candidate.get("output_contract", []) if isinstance(candidate.get("output_contract"), list) else []),
        ]
        return "\n".join(section for section in sections if section)

    @staticmethod
    def _text_similarity(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        left_normalized = " ".join(left.lower().split())
        right_normalized = " ".join(right.lower().split())
        return SequenceMatcher(None, left_normalized, right_normalized).ratio()

    def _run_competition(
        self,
        candidates: list[dict[str, Any]],
        incumbents: list[dict[str, Any]],
        baseline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        active_skills = [deepcopy(item) for item in incumbents if isinstance(item, dict)]
        challengers: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        sorted_candidates = sorted(candidates, key=lambda item: float(item.get("score_total", 0.0)), reverse=True)

        for candidate in sorted_candidates:
            action = self._decide_competition_action(candidate, active_skills, baseline)
            candidate["competition"] = action
            verdict = action.get("verdict")

            if verdict == "promote":
                replaced_skill_id = action.get("replaced_skill_id")
                if replaced_skill_id:
                    active_skills = [
                        item for item in active_skills
                        if str(item.get("skill_id")) != str(replaced_skill_id)
                    ]
                champion = deepcopy(candidate)
                champion["competition_status"] = "champion"
                active_skills.append(champion)
            elif verdict == "challenger":
                challenger = deepcopy(candidate)
                challenger["competition_status"] = "challenger"
                challengers.append(challenger)
            else:
                rejected_item = deepcopy(candidate)
                rejected_item["competition_status"] = "rejected"
                rejected.append(rejected_item)

        active_skills.sort(
            key=lambda item: (str(item.get("domain") or ""), float(item.get("score_total", 0.0))),
            reverse=True,
        )
        return {
            "active_skills": active_skills,
            "challengers": challengers,
            "rejected": rejected,
        }

    @staticmethod
    def _skill_family(domain: Any) -> str:
        segments = [segment for segment in str(domain or "").split(".") if segment]
        if not segments:
            return "unknown"
        return ".".join(segments[:3])

    @staticmethod
    def _domain_root(domain: Any) -> str:
        segments = [segment for segment in str(domain or "").split(".") if segment]
        return segments[0] if segments else "unknown"

    @staticmethod
    def _source_file_path(skill: dict[str, Any]) -> str:
        source = skill.get("source", {})
        if not isinstance(source, dict):
            return ""
        return str(source.get("file_path") or "").strip()

    def _is_reference_fragment(self, skill: dict[str, Any]) -> bool:
        domain = self._normalize_domain(skill.get("domain"))
        if not domain:
            return False
        return ".references." in domain or domain.endswith(".references")

    def _has_explicit_output_contract(self, skill: dict[str, Any]) -> bool:
        return bool(self._ensure_list(skill.get("output_contract"), fallback_as_single=True))

    def _redundancy_rank(self, skill: dict[str, Any]) -> tuple[Any, ...]:
        domain = str(skill.get("domain") or "")
        metadata = skill.get("metadata", {}) if isinstance(skill.get("metadata"), dict) else {}
        section_depth = int(metadata.get("section_depth") or 0)
        return (
            0 if self._is_reference_fragment(skill) else 1,
            1 if self._has_explicit_output_contract(skill) else 0,
            float(skill.get("score_total", 0.0)),
            len([segment for segment in domain.split(".") if segment]),
            max(0, 3 - section_depth),
        )

    def _find_family_overlap(
        self,
        skill: dict[str, Any],
        others: list[dict[str, Any]],
    ) -> tuple[float, Optional[dict[str, Any]]]:
        family = self._skill_family(skill.get("domain"))
        candidate_text = self._candidate_text(skill)
        best_similarity = 0.0
        best_skill: Optional[dict[str, Any]] = None
        for other in others:
            if not isinstance(other, dict):
                continue
            if self._skill_family(other.get("domain")) != family:
                continue
            similarity = self._text_similarity(candidate_text, self._candidate_text(other))
            if similarity > best_similarity:
                best_similarity = similarity
                best_skill = other
        return best_similarity, deepcopy(best_skill) if best_skill else None

    def _mark_auto_pruned(self, skill: dict[str, Any], reason: str, *, target: str) -> dict[str, Any]:
        item = deepcopy(skill)
        item["auto_pruned_reason"] = reason
        item["competition"] = {
            **(item.get("competition", {}) if isinstance(item.get("competition"), dict) else {}),
            "auto_pruned_reason": reason,
            "auto_pruned_target": target,
        }
        item["competition_status"] = "challenger" if target == "challenger" else "rejected"
        return item

    def _apply_redundancy_guardrails(
        self,
        competition: dict[str, Any],
    ) -> dict[str, Any]:
        active_skills = [
            deepcopy(item)
            for item in competition.get("active_skills", [])
            if isinstance(item, dict)
        ]
        challengers = [
            deepcopy(item)
            for item in competition.get("challengers", [])
            if isinstance(item, dict)
        ]
        rejected = [
            deepcopy(item)
            for item in competition.get("rejected", [])
            if isinstance(item, dict)
        ]

        kept_active: list[dict[str, Any]] = []
        active_family_counts: Counter[str] = Counter()
        source_root_sets: defaultdict[str, set[str]] = defaultdict(set)
        pruning_actions: list[dict[str, Any]] = []

        for skill in sorted(active_skills, key=self._redundancy_rank, reverse=True):
            family = self._skill_family(skill.get("domain"))
            reason: Optional[str] = None

            if self._is_reference_fragment(skill):
                has_non_reference_in_family = any(
                    self._skill_family(existing.get("domain")) == family
                    and not self._is_reference_fragment(existing)
                    for existing in kept_active
                )
                if has_non_reference_in_family:
                    reason = "reference_fragment_shadowed"

            overlap_similarity, overlap_skill = self._find_family_overlap(skill, kept_active)
            if reason is None and overlap_skill and overlap_similarity >= self.config.functional_overlap_threshold:
                if self._is_reference_fragment(skill) and not self._is_reference_fragment(overlap_skill):
                    reason = "reference_fragment_shadowed"
                else:
                    reason = "functional_overlap_with_active"

            if reason is None and active_family_counts[family] >= self.config.max_active_skills_per_family:
                reason = "family_cap_exceeded"

            source_path = self._source_file_path(skill)
            root = self._domain_root(skill.get("domain"))
            if (
                reason is None
                and source_path
                and root not in source_root_sets[source_path]
                and len(source_root_sets[source_path]) >= self.config.max_root_domains_per_source
            ):
                reason = "source_cross_domain_sprawl"

            if reason:
                target = "challenger" if float(skill.get("score_total", 0.0)) >= self.config.challenger_threshold else "rejected"
                pruned = self._mark_auto_pruned(skill, reason, target=target)
                if target == "challenger":
                    challengers.append(pruned)
                else:
                    rejected.append(pruned)
                pruning_actions.append(
                    {
                        "reason": reason,
                        "target": target,
                        "title": str(skill.get("title") or skill.get("domain") or "Skill"),
                        "domain": str(skill.get("domain") or ""),
                    }
                )
                continue

            kept_active.append(skill)
            active_family_counts[family] += 1
            if source_path:
                source_root_sets[source_path].add(root)

        kept_challengers: list[dict[str, Any]] = []
        challenger_family_counts: Counter[str] = Counter()
        reference_shadow_cache: defaultdict[str, bool] = defaultdict(bool)
        for active_skill in kept_active:
            if not self._is_reference_fragment(active_skill):
                reference_shadow_cache[self._skill_family(active_skill.get("domain"))] = True

        for skill in sorted(challengers, key=self._redundancy_rank, reverse=True):
            family = self._skill_family(skill.get("domain"))
            reason: Optional[str] = None
            if self._is_reference_fragment(skill) and reference_shadow_cache[family]:
                reason = "reference_fragment_shadowed"
            overlap_similarity, overlap_skill = self._find_family_overlap(skill, [*kept_active, *kept_challengers])
            if reason is None and overlap_skill and overlap_similarity >= self.config.functional_overlap_threshold:
                if self._is_reference_fragment(skill) and reference_shadow_cache[family]:
                    reason = "reference_fragment_shadowed"
                else:
                    reason = "functional_overlap_with_catalog"

            if reason is None and challenger_family_counts[family] >= self.config.max_challenger_skills_per_family:
                reason = "challenger_family_cap_exceeded"

            if reason:
                rejected.append(self._mark_auto_pruned(skill, reason, target="rejected"))
                pruning_actions.append(
                    {
                        "reason": reason,
                        "target": "rejected",
                        "title": str(skill.get("title") or skill.get("domain") or "Skill"),
                        "domain": str(skill.get("domain") or ""),
                    }
                )
                continue

            kept_challengers.append(skill)
            challenger_family_counts[family] += 1

        return {
            "active_skills": kept_active,
            "challengers": kept_challengers,
            "rejected": rejected,
            "redundancy_pruning": {
                "enabled": True,
                "pruned_count": len(pruning_actions),
                "active_pruned_count": sum(1 for item in pruning_actions if item["target"] in {"challenger", "rejected"}),
                "sample_actions": pruning_actions[:20],
            },
        }

    def _decide_competition_action(
        self,
        candidate: dict[str, Any],
        active_skills: list[dict[str, Any]],
        baseline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if candidate.get("hard_rejects"):
            return {
                "verdict": "reject",
                "reason": ",".join(candidate["hard_rejects"]),
            }

        score_total = float(candidate.get("score_total", 0.0))
        incumbent, similarity = self._find_best_incumbent(candidate, [*active_skills, *baseline])
        candidate_domain = str(candidate.get("domain") or "")

        if similarity >= self.config.duplicate_similarity_threshold:
            return {"verdict": "reject", "reason": "duplicate_or_near_duplicate"}

        if incumbent:
            incumbent_score = float(incumbent.get("score_total", 78.0))
            incumbent_domain = str(incumbent.get("domain") or "")
            incumbent_is_baseline = bool(incumbent.get("protected_builtin"))
            candidate_depth = len(candidate_domain.split("."))
            incumbent_depth = len(incumbent_domain.split("."))

            if score_total >= incumbent_score + self.config.competition_margin:
                if incumbent_is_baseline:
                    return {"verdict": "promote", "reason": "beats_builtin_champion"}
                return {
                    "verdict": "promote",
                    "reason": "beats_incumbent",
                    "replaced_skill_id": incumbent.get("skill_id"),
                }

            if candidate_depth > incumbent_depth and score_total >= incumbent_score - 1.0 and similarity < 0.87:
                return {"verdict": "promote", "reason": "more_specific_than_incumbent"}

            if score_total >= max(self.config.challenger_threshold, incumbent_score - self.config.challenger_margin):
                return {"verdict": "challenger", "reason": "close_but_not_champion"}

            return {"verdict": "reject", "reason": "weaker_than_incumbent"}

        if score_total >= self.config.active_threshold:
            return {"verdict": "promote", "reason": "new_domain_champion"}
        if score_total >= self.config.challenger_threshold:
            return {"verdict": "challenger", "reason": "promising_but_below_active_threshold"}
        return {"verdict": "reject", "reason": "below_threshold"}

    def _load_builtin_baseline_skills(self) -> list[dict[str, Any]]:
        baseline: list[dict[str, Any]] = []
        expert_pack = PromptPackStore.load_json_file("enhancement_rules", "expert_prompt_intelligence.json")
        curriculum_pack = PromptPackStore.load_json_file("enhancement_rules", "skill_curriculum.json")
        topic_pack = PromptPackStore.load_json_file("context_injection", "topic_handlers.json")

        intent_modules = expert_pack.get("intent_modules", {}) if isinstance(expert_pack, dict) else {}
        if isinstance(intent_modules, dict):
            for key, value in intent_modules.items():
                if not isinstance(value, dict):
                    continue
                baseline.append(
                    self._build_builtin_skill(
                        title=value.get("name") or key.replace("_", " ").title(),
                        domain=f"builtin.expert.{key}",
                        instructions=value.get("instructions", []),
                        triggers=[key],
                    )
                )

        intent_curriculum = curriculum_pack.get("intent_curriculum", {}) if isinstance(curriculum_pack, dict) else {}
        if isinstance(intent_curriculum, dict):
            for key, value in intent_curriculum.items():
                if not isinstance(value, dict):
                    continue
                baseline.append(
                    self._build_builtin_skill(
                        title=value.get("name") or key.replace("_", " ").title(),
                        domain=f"builtin.curriculum.{key}",
                        instructions=value.get("instructions", []),
                        triggers=[key, *(value.get("trigger_keywords", []) if isinstance(value.get("trigger_keywords"), list) else [])],
                    )
                )

        handlers = topic_pack.get("handlers", {}) if isinstance(topic_pack, dict) else {}
        if isinstance(handlers, dict):
            for key, value in handlers.items():
                if not isinstance(value, dict):
                    continue
                baseline.append(
                    self._build_builtin_skill(
                        title=key.replace("_", " ").title(),
                        domain=f"builtin.topic.{key}",
                        instructions=[value.get("inject", "")],
                        triggers=value.get("keywords", []),
                    )
                )
        return baseline

    def _build_builtin_skill(
        self,
        *,
        title: str,
        domain: str,
        instructions: Any,
        triggers: Any,
    ) -> dict[str, Any]:
        instruction_list = self._normalize_items(
            [str(item) for item in instructions] if isinstance(instructions, list) else [str(instructions)],
            4,
        )
        trigger_list = self._normalize_items(
            [str(item) for item in triggers] if isinstance(triggers, list) else [str(triggers)],
            6,
        )
        return {
            "skill_id": f"builtin::{domain}",
            "title": title,
            "domain": domain,
            "instruction_core": "\n".join(instruction_list),
            "output_contract": [],
            "trigger_patterns": trigger_list,
            "score_total": 78.0,
            "protected_builtin": True,
        }

    def _build_compile_report(
        self,
        source_path: Path,
        artifacts: list[Path],
        incoming_candidates: list[dict[str, Any]],
        accepted_corpus: list[dict[str, Any]],
        scored_candidates: list[dict[str, Any]],
        competition: dict[str, Any],
        *,
        artifact_retention: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        generated_at = datetime.now().isoformat()
        display_source_path = self._display_path(source_path)
        report_id = hashlib.sha1(f"{display_source_path}::{generated_at}".encode("utf-8")).hexdigest()[:12]
        summary = {
            "artifact_count": len(artifacts),
            "incoming_candidate_count": len(incoming_candidates),
            "accepted_corpus_count": len(accepted_corpus),
            "candidate_count": len(scored_candidates),
            "active_skill_count": len(competition["active_skills"]),
            "challenger_count": len(competition["challengers"]),
            "rejected_count": len(competition["rejected"]),
            "redundancy_pruned_count": int(competition.get("redundancy_pruning", {}).get("pruned_count", 0) or 0),
            "avg_score": round(
                sum(float(item.get("score_total", 0.0)) for item in scored_candidates) / max(1, len(scored_candidates)),
                2,
            ),
        }
        return {
            "report_id": report_id,
            "generated_at": generated_at,
            "source_path": display_source_path,
            "summary": summary,
            "artifacts": [self._display_path(path) for path in artifacts],
            "incoming_candidates": incoming_candidates,
            "accepted_corpus": accepted_corpus,
            "candidates": scored_candidates,
            "active_skills": competition["active_skills"],
            "challengers": competition["challengers"],
            "rejected": competition["rejected"],
            "publish_gate": self.evaluate_publish_gate(
                draft_registry={
                    "generated_at": generated_at,
                    "active_skills": competition["active_skills"],
                    "summary": summary,
                    "report_id": report_id,
                    "state": "draft",
                },
                catalog={
                    "challengers": competition["challengers"],
                    "rejected": competition["rejected"],
                },
            ),
            "redundancy_pruning": competition.get("redundancy_pruning", {
                "enabled": False,
                "pruned_count": 0,
                "active_pruned_count": 0,
                "sample_actions": [],
            }),
            "artifact_retention": artifact_retention or {
                "enabled": False,
                "moved_count": 0,
                "accepted_artifact_count": 0,
                "rejected_artifact_count": 0,
                "artifact_destinations": {},
                "moved_items": [],
            },
        }

    def _score_runtime_match(
        self,
        skill: dict[str, Any],
        *,
        text: str,
        detected_intent: Optional[str],
        extracted_keywords: set[str],
        prefer_vietnamese: bool,
    ) -> float:
        score = 0.35 * min(1.0, float(skill.get("score_total", 0.0)) / 100.0)
        hints = skill.get("selection_hints", {})
        intent_allowlist = hints.get("intent_allowlist", []) if isinstance(hints, dict) else []

        language = str(hints.get("language") or skill.get("language") or "").strip().lower()
        if prefer_vietnamese and language in {"vi", "multilingual"}:
            score += 0.05
        if not prefer_vietnamese and language in {"en", "multilingual"}:
            score += 0.05

        trigger_patterns = skill.get("trigger_patterns", [])
        hits = 0
        if isinstance(trigger_patterns, list):
            for pattern in trigger_patterns:
                normalized = str(pattern).strip().lower()
                if not normalized:
                    continue
                if normalized in text or normalized in extracted_keywords:
                    hits += 1
            if hits:
                score += min(0.35, hits * 0.12)

        if isinstance(intent_allowlist, list) and intent_allowlist:
            if detected_intent in intent_allowlist:
                score += 0.28
            elif hits <= 0:
                score -= 0.05

        domain_tokens = {segment for segment in str(skill.get("domain") or "").split(".") if segment}
        domain_hits = sum(1 for token in domain_tokens if token in text or token in extracted_keywords)
        if domain_hits:
            score += min(0.15, domain_hits * 0.05)

        anti_triggers = skill.get("anti_triggers", [])
        if isinstance(anti_triggers, list) and anti_triggers:
            for anti_trigger in anti_triggers:
                normalized = str(anti_trigger).strip().lower()
                if normalized and (normalized in text or normalized in extracted_keywords):
                    score -= 0.25
        return score

    @staticmethod
    def _ensure_list(value: Any, *, fallback_as_single: bool = False) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if fallback_as_single and isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None


def build_foundry_from_env(*, data_root: Optional[Path] = None) -> SkillFoundry:
    model = os.getenv("PIGTEX_SKILL_FOUNDRY_JUDGE_MODEL", "").strip()
    api_key = os.getenv("PIGTEX_SKILL_FOUNDRY_API_KEY", "").strip()
    api_base_url = os.getenv("PIGTEX_SKILL_FOUNDRY_API_BASE_URL", "").strip()
    auto_archive_raw = os.getenv("PIGTEX_SKILL_FOUNDRY_AUTO_ARCHIVE_ARTIFACTS", "1").strip().lower()
    retention_raw = os.getenv("PIGTEX_SKILL_FOUNDRY_REJECTED_RETENTION_DAYS", "0").strip()
    try:
        rejected_retention_days = max(0, int(retention_raw or 0))
    except ValueError:
        rejected_retention_days = 0
    config = SkillFoundryConfig(
        auto_archive_artifacts=auto_archive_raw in {"1", "true", "yes", "on"},
        rejected_retention_days=rejected_retention_days,
    )
    judge = None
    if model and api_key and api_base_url:
        judge = LLMFoundryJudge(
            SkillJudgeConfig(
                model=model,
                api_key=api_key,
                api_base_url=api_base_url,
            )
        )
    return SkillFoundry(data_root=data_root, judge=judge, config=config)
