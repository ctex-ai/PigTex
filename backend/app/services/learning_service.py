from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ..models import (
    KnowledgeItem,
    LearningAssessmentAttempt,
    LearningProgram,
    LearningProgramNode,
    LearningSession,
    User,
    Workspace,
)

_PIGTEX_METADATA_COMMENT_RE = re.compile(r"<!--\s*PIGTEX_[A-Z_]+\s+[\s\S]*?-->", re.IGNORECASE)
_PIGTEX_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_load_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _json_load_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strip_learning_transport_artifacts(value: str) -> str:
    cleaned = _PIGTEX_METADATA_COMMENT_RE.sub(" ", value or "")
    cleaned = _PIGTEX_MARKDOWN_IMAGE_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _fold_to_ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _normalize_text(value: str, *, ascii_fold: bool = False) -> str:
    lowered = _strip_learning_transport_artifacts(value).strip().lower()
    if ascii_fold:
        lowered = _fold_to_ascii(lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def _keyword_hits(answer: str, keywords: list[str]) -> tuple[list[str], list[str]]:
    normalized_answer = _normalize_text(answer, ascii_fold=True)
    matched: list[str] = []
    missing: list[str] = []
    for keyword in keywords:
        normalized_keyword = _normalize_text(keyword, ascii_fold=True)
        if not normalized_keyword:
            continue
        if normalized_keyword in normalized_answer:
            matched.append(keyword)
        else:
            missing.append(keyword)
    return matched, missing


def _extract_terms(value: Any, *, limit: int = 24) -> list[str]:
    stopwords = {
        "about", "after", "before", "between", "because", "their", "there", "these", "those",
        "where", "which", "while", "would", "should", "could", "through", "using", "into",
        "from", "with", "have", "that", "this", "your", "when", "what", "will", "them",
        "they", "then", "than", "only", "just", "very", "more", "much", "many", "need",
        "does", "dont", "cant", "wont", "under", "over", "learn", "learning", "study",
        "topic", "goal", "node", "turn", "mode", "real", "task", "next", "step", "core",
        "muc", "tieu", "nguoi", "hoc", "chu", "de", "bai", "ban", "mot", "nhung", "trong",
        "theo", "cach", "lam", "nay", "kia", "roi", "hay", "can", "them", "cua", "cho",
        "voi", "sau", "truoc", "khi", "neu", "vi", "sao", "giai", "thich", "duoc", "dung",
        "y", "nay", "minh", "toi", "em",
    }
    queue: list[Any] = [value]
    terms: list[str] = []
    seen: set[str] = set()

    while queue and len(terms) < limit:
        current = queue.pop(0)
        if current is None:
            continue
        if isinstance(current, (list, tuple, set)):
            queue.extend(list(current))
            continue
        if isinstance(current, dict):
            queue.extend(list(current.values()))
            continue
        text = _normalize_text(str(current), ascii_fold=True)
        for token in re.findall(r"[a-z0-9_]{3,}", text):
            if token in stopwords or token.isdigit() or token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= limit:
                break
    return terms


def _text_similarity(left: str, right: str) -> float:
    normalized_left = _normalize_text(left, ascii_fold=True)
    normalized_right = _normalize_text(right, ascii_fold=True)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _chunk_text_for_learning(value: str, *, max_chars: int = 320) -> list[str]:
    text = _strip_learning_transport_artifacts(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    chunks: list[str] = []
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", value or "") if item.strip()]
    if not paragraphs:
        paragraphs = [text]

    for paragraph in paragraphs:
        clean = re.sub(r"\s+", " ", paragraph).strip()
        if not clean:
            continue
        if len(clean) <= max_chars:
            chunks.append(clean)
            continue

        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", clean) if item.strip()]
        if not sentences:
            sentences = [clean]

        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = sentence
        if current:
            chunks.append(current)

    return chunks[:12]


@dataclass(slots=True)
class NodeBlueprint:
    node_key: str
    stage: str
    title: str
    summary: str
    explanation: str
    worked_example: str
    practice_task: str
    reflection_prompt: str
    estimated_minutes: int
    difficulty: int
    common_pitfalls: list[str]
    expected_keywords: list[str]
    success_criteria: list[str]
    resources: list[str]


class LearningService:
    REVIEW_DAY_STEPS = (1, 3, 7, 14, 30)

    def __init__(self, db: Session, current_user: User):
        self.db = db
        self.current_user = current_user

    def _locale_for_language(self, language: str) -> str:
        return "vi-VN" if language == "vi" else "en-US"

    def _pace_for_minutes(self, weekly_minutes: int) -> str:
        if weekly_minutes >= 420:
            return "intensive"
        if weekly_minutes <= 90:
            return "light"
        return "normal"

    def _preferred_explanation_style(self, learning_style: str) -> str:
        mapping = {
            "guided": "step_by_step",
            "practice_first": "practice_first",
            "socratic": "question_led",
            "project_first": "project_anchored",
        }
        return mapping.get(learning_style, "step_by_step")

    def _source_registry_warnings(self, language: str) -> list[str]:
        return [
            (
                "Nguon hoc tap duoc uu tien tu workspace; do tin cay giam neu excerpt khong duoc tim thay."
                if language == "vi"
                else "Workspace learning resources are preferred; confidence drops when no relevant excerpt is found."
            )
        ]

    def _collect_focus_terms(
        self,
        *,
        program: LearningProgram,
        focus_node: LearningProgramNode | None,
        learning_state: dict[str, Any] | None = None,
        query_text: str | None = None,
    ) -> list[str]:
        buckets: list[Any] = [program.topic, program.goal, query_text or ""]
        if focus_node is not None:
            buckets.extend([
                focus_node.title,
                focus_node.summary,
                focus_node.practice_task,
                _json_load_list(focus_node.expected_keywords_json),
                _json_load_list(focus_node.success_criteria_json),
            ])

        if isinstance(learning_state, dict):
            current_goal = learning_state.get("current_goal")
            if isinstance(current_goal, dict):
                buckets.extend([
                    current_goal.get("raw_goal"),
                    current_goal.get("operational_goal"),
                    current_goal.get("success_criteria"),
                ])

        return _extract_terms(buckets, limit=28)

    def _source_chunk_score(self, text: str, focus_terms: list[str]) -> tuple[float, list[str]]:
        normalized = _normalize_text(text, ascii_fold=True)
        if not normalized:
            return 0.0, []

        matched = [term for term in focus_terms if term in normalized]
        unique_matched = list(dict.fromkeys(matched))
        if not unique_matched:
            base_score = min(0.32, max(0.08, len(normalized) / 1800))
            return round(base_score, 2), []

        relevance = min(0.98, 0.18 + len(unique_matched) * 0.12 + min(0.24, len(normalized) / 1800))
        return round(relevance, 2), unique_matched[:6]

    def _knowledge_item_metadata(self, item: KnowledgeItem) -> dict[str, Any]:
        return _json_load_dict(item.metadata_json)

    def _derive_source_map_sections(self, text: str) -> list[dict[str, Any]]:
        raw = text or ""
        if not raw.strip():
            return []

        lines = raw.replace("\r\n", "\n").split("\n")
        sections: list[dict[str, Any]] = []
        current_title = "Overview"
        current_lines: list[str] = []
        section_index = 1

        def flush_section() -> None:
            nonlocal section_index, current_lines, current_title
            body = "\n".join(current_lines).strip()
            if not body:
                current_lines = []
                return
            chunks = _chunk_text_for_learning(body, max_chars=360)
            for chunk_index, chunk in enumerate(chunks, start=1):
                label = current_title if len(chunks) == 1 else f"{current_title} ({chunk_index})"
                sections.append({
                    "anchor_id": f"sec_{section_index}_{chunk_index}",
                    "label": label,
                    "anchor_ref": f"§{section_index}" + (f".{chunk_index}" if len(chunks) > 1 else ""),
                    "text": chunk,
                })
            section_index += 1
            current_lines = []

        for line in lines:
            stripped = line.strip()
            is_heading = bool(re.match(r"^(#{1,6}\s+|(?:section|chapter|part)\s+\d+|(?:\d+\.)+\s+|\d+\.\s+)", stripped, re.IGNORECASE))
            if not is_heading and stripped and len(stripped) <= 72 and stripped.upper() == stripped and len(stripped.split()) <= 8:
                is_heading = True

            if is_heading:
                flush_section()
                current_title = re.sub(r"^(#{1,6}\s+)", "", stripped).strip(" -:\t") or current_title
                continue

            current_lines.append(line)

        flush_section()
        if sections:
            return sections[:18]

        fallback_chunks = _chunk_text_for_learning(raw, max_chars=360)
        return [
            {
                "anchor_id": f"chunk_{index}",
                "label": f"Chunk {index}",
                "anchor_ref": f"Chunk {index}",
                "text": chunk,
            }
            for index, chunk in enumerate(fallback_chunks, start=1)
        ][:18]

    def _normalize_source_sections(
        self,
        item: KnowledgeItem,
    ) -> list[dict[str, Any]]:
        metadata = self._knowledge_item_metadata(item)
        raw_source_map = metadata.get("source_map")
        normalized_sections: list[dict[str, Any]] = []

        def append_section(section: Any, default_index: int) -> None:
            if not isinstance(section, dict):
                return
            text = str(section.get("text") or section.get("excerpt") or "").strip()
            if not text:
                return
            label = str(section.get("label") or section.get("title") or f"Chunk {default_index}").strip()
            anchor_ref = str(
                section.get("anchor_ref")
                or section.get("page_ref")
                or section.get("slide_ref")
                or label
            ).strip()
            normalized_sections.append({
                "anchor_id": str(section.get("anchor_id") or section.get("id") or f"chunk_{default_index}").strip(),
                "label": label,
                "anchor_ref": anchor_ref,
                "text": text,
            })

        if isinstance(raw_source_map, dict):
            for index, section in enumerate(raw_source_map.get("sections") or [], start=1):
                append_section(section, index)

        if not normalized_sections:
            for candidate_key in ("chunks", "document_chunks", "sections"):
                raw_chunks = metadata.get(candidate_key)
                if not isinstance(raw_chunks, list):
                    continue
                for index, chunk in enumerate(raw_chunks, start=1):
                    append_section(chunk, index)
                if normalized_sections:
                    break

        if not normalized_sections:
            normalized_sections = self._derive_source_map_sections(item.content or "")

        if normalized_sections and (
            not isinstance(raw_source_map, dict)
            or not isinstance(raw_source_map.get("sections"), list)
            or not raw_source_map.get("sections")
        ):
            metadata["source_map"] = {
                "version": "learn_v1",
                "sections": normalized_sections,
            }
            item.metadata_json = _json_dumps(metadata)

        return normalized_sections[:18]

    def _build_source_entry(
        self,
        *,
        item: KnowledgeItem,
        program: LearningProgram,
        focus_terms: list[str],
    ) -> dict[str, Any]:
        content = item.content or ""
        content_sections = self._normalize_source_sections(item)
        best_excerpt = ""
        matched_terms: list[str] = []
        relevance_score = 0.0
        best_anchor_ref = ""
        best_anchor_label = ""

        for section in content_sections[:14]:
            chunk = str(section.get("text") or "").strip()
            if not chunk:
                continue
            score, matched = self._source_chunk_score(chunk, focus_terms)
            if score > relevance_score:
                relevance_score = score
                matched_terms = matched
                best_excerpt = chunk
                best_anchor_ref = str(section.get("anchor_ref") or "").strip()
                best_anchor_label = str(section.get("label") or "").strip()

        warnings: list[str] = []
        if not content.strip():
            warnings.append(
                "Tai lieu nay chua co noi dung text de grounding chi tiet."
                if program.language == "vi"
                else "This resource does not yet have extracted text for detailed grounding."
            )
        elif not best_excerpt:
            warnings.append(
                "Khong tim thay doan noi dung hop tac manh voi focus hien tai."
                if program.language == "vi"
                else "No strong excerpt was found for the current focus."
            )

        if best_excerpt:
            coverage_summary = (
                f"Grounded excerpt at {best_anchor_ref or best_anchor_label} for {', '.join(matched_terms[:3])}."
                if program.language != "vi"
                else f"Co excerpt grounded tai {best_anchor_ref or best_anchor_label} cho {', '.join(matched_terms[:3])}."
            )
        else:
            coverage_summary = (
                "Workspace resource available for guided teaching."
                if program.language != "vi"
                else "Tai nguyen workspace co san de day hoc co huong dan."
            )

        quality_score = round(min(0.97, max(0.16, relevance_score + min(0.18, len(content) / 5000))), 2)
        return {
            "source_id": f"workspace_{item.id}",
            "file_name": item.title,
            "file_type": item.content_type,
            "quality_score": quality_score,
            "coverage_summary": coverage_summary,
            "warnings": warnings or self._source_registry_warnings(program.language),
            "excerpt": best_excerpt[:320] if best_excerpt else None,
            "source_ref": f"{item.title} {best_anchor_ref}".strip() if best_anchor_ref else item.title,
            "anchor_label": best_anchor_label or None,
            "anchor_ref": best_anchor_ref or None,
            "matched_terms": matched_terms[:4],
            "relevance_score": relevance_score,
            "is_favorite": bool(item.is_favorite),
        }

    def _build_source_registry(
        self,
        *,
        program: LearningProgram,
        focus_node: LearningProgramNode | None,
        learning_state: dict[str, Any] | None = None,
        query_text: str | None = None,
    ) -> dict[str, Any]:
        focus_terms = self._collect_focus_terms(
            program=program,
            focus_node=focus_node,
            learning_state=learning_state,
            query_text=query_text,
        )
        sources: list[dict[str, Any]] = []
        if program.workspace_id:
            items = (
                self.db.query(KnowledgeItem)
                .filter(
                    KnowledgeItem.user_id == self.current_user.id,
                    KnowledgeItem.workspace_id == program.workspace_id,
                )
                .order_by(KnowledgeItem.is_favorite.desc(), KnowledgeItem.updated_at.desc(), KnowledgeItem.created_at.desc())
                .limit(8)
                .all()
            )
            for item in items:
                sources.append(self._build_source_entry(
                    item=item,
                    program=program,
                    focus_terms=focus_terms,
                ))

        sources.sort(
            key=lambda item: (
                float(item.get("relevance_score") or 0.0),
                float(item.get("quality_score") or 0.0),
                1 if item.get("is_favorite") else 0,
            ),
            reverse=True,
        )
        return {
            "focus_terms": focus_terms[:8],
            "sources": sources[:5],
        }

    def _default_memory_update_summary(self, language: str) -> dict[str, Any]:
        return {
            "added": [
                (
                    "Da khoi tao learning memory tu muc tieu va lo trinh hien tai."
                    if language == "vi"
                    else "Initialized learning memory from the active goal and roadmap."
                )
            ],
            "revised": [],
            "downgraded": [],
            "confidence": 0.46,
        }

    def _checklist_status_for_node(
        self,
        node: LearningProgramNode,
        *,
        is_focus: bool,
    ) -> str:
        if node.mastery_status == "completed":
            if self._is_review_due(node):
                return "review_due"
            return "verified"
        if node.mastery_status == "reviewing":
            return "partial" if float(node.mastery_score or 0.0) >= 0.42 else "active"
        if node.mastery_status == "locked":
            return "blocked"
        if is_focus:
            return "active"
        return "not_started"

    def _instructional_mode_for_node(
        self,
        node: LearningProgramNode | None,
        *,
        is_review: bool = False,
    ) -> str:
        if node is None:
            return "summarize_progress"
        if is_review or self._is_review_due(node):
            return "review"
        if node.mastery_status == "reviewing" and float(node.mastery_score or 0.0) < 0.58:
            return "remediate"
        if node.stage in {"orientation", "foundation"}:
            return "teach"
        if node.stage in {"practice", "application", "applied", "core"}:
            return "guided_practice"
        if node.stage in {"integration", "capstone"}:
            return "independent_practice"
        return "teach"

    def _build_goal_payload(
        self,
        *,
        program: LearningProgram,
        nodes: list[LearningProgramNode],
    ) -> dict[str, Any]:
        if program.outcome_target:
            operational_goal = program.outcome_target
        elif program.language == "vi":
            operational_goal = (
                f"Hoan thanh lo trinh '{program.title}' va tu xu ly duoc cac bai tap/dau viec cot loi ve {program.topic} "
                "voi bang chung tu bai lam hoac giai thich bang loi cua minh."
            )
        else:
            operational_goal = (
                f"Finish the '{program.title}' roadmap and independently handle the core tasks in {program.topic} "
                "with evidence from performance, not just confidence."
            )

        success_criteria = [
            (
                "Independent performance on at least one unseen or lightly-guided task"
                if program.language != "vi"
                else "Tu hoan thanh it nhat mot bai moi hoac bai co goi y rat nhe"
            ),
            (
                "Explains the reasoning in their own words"
                if program.language != "vi"
                else "Giai thich duoc ly do va cach lam bang loi cua minh"
            ),
            (
                "Can detect and repair recurring mistakes"
                if program.language != "vi"
                else "Phat hien va sua duoc loi lap lai"
            ),
        ]

        constraints = [
            (
                "Prefer workspace learning resources when available."
                if program.language != "vi"
                else "Uu tien tai nguyen hoc tap trong workspace khi co."
            ),
            (
                "Do not mark mastery without evidence from learner performance."
                if program.language != "vi"
                else "Khong danh dau da dat neu chua co bang chung tu performance."
            ),
        ]

        return {
            "goal_id": f"goal_{program.id[:8]}",
            "raw_goal": program.goal,
            "operational_goal": operational_goal,
            "deadline": program.target_date.isoformat() if program.target_date else None,
            "success_criteria": success_criteria,
            "constraints": constraints,
            "status": "active",
            "milestones": [
                {
                    "node_id": node.id,
                    "node_key": node.node_key,
                    "title": node.title,
                }
                for node in nodes
            ],
        }

    def _build_initial_learning_state(
        self,
        *,
        program: LearningProgram,
        nodes: list[LearningProgramNode],
        weekly_minutes: int,
    ) -> dict[str, Any]:
        focus_node = nodes[0] if nodes else None
        initial_mode = self._instructional_mode_for_node(focus_node)
        source_registry = self._build_source_registry(
            program=program,
            focus_node=focus_node,
        )

        progress_checklist: list[dict[str, Any]] = []
        knowledge_skills: list[dict[str, Any]] = []
        for index, node in enumerate(nodes):
            status = "active" if index == 0 else "not_started"
            progress_checklist.append({
                "item_id": f"chk_{node.id[:8]}",
                "node_id": node.id,
                "label": node.title,
                "status": status,
                "linked_skill_ids": [node.node_key],
                "reason": (
                    "Current teaching focus."
                    if status == "active" and program.language != "vi"
                    else "Trong tam hien tai."
                    if status == "active"
                    else "Queued behind earlier prerequisites."
                    if program.language != "vi"
                    else "Dang xep sau cac prerequisite truoc do."
                ),
                "evidence_ids": [],
                "next_verification_action": node.practice_task,
            })
            knowledge_skills.append({
                "skill_id": node.node_key,
                "node_id": node.id,
                "name": node.title,
                "prerequisites": _json_load_list(node.prerequisites_json),
                "mastery_status": "partial" if index == 0 else "not_met",
                "confidence": 0.22 if index == 0 else 0.12,
                "last_evidence_ids": [],
                "misconceptions": _json_load_list(node.common_pitfalls_json)[:2],
                "review_due": None,
            })

        learner_memories = [
            {
                "memory_id": f"mem_pref_{program.id[:8]}",
                "kind": "learning_preference",
                "content": (
                    f"Prefers {program.learning_style} guidance for this roadmap."
                    if program.language != "vi"
                    else f"Uu tien kieu hoc {program.learning_style} cho lo trinh nay."
                ),
                "confidence": 0.78,
                "supporting_evidence_ids": [],
                "status": "active",
            }
        ]

        learning_state = {
            "session_id": program.id,
            "learner_id": self.current_user.id,
            "locale": self._locale_for_language(program.language),
            "current_goal": self._build_goal_payload(program=program, nodes=nodes),
            "learner_profile": {
                "estimated_level": program.current_level,
                "pace": self._pace_for_minutes(weekly_minutes),
                "preferred_explanation_style": self._preferred_explanation_style(program.learning_style),
                "language_preference": "Vietnamese" if program.language == "vi" else "English",
                "motivation_notes": [program.goal],
            },
            "knowledge_map": {
                "skills": knowledge_skills,
            },
            "progress_checklist": progress_checklist,
            "evidence_log": [],
            "source_registry": source_registry,
            "memory": {
                "session_memory": {
                    "active_subtopic": focus_node.node_key if focus_node is not None else None,
                    "current_mode": initial_mode,
                    "current_question": focus_node.practice_task if focus_node is not None else None,
                },
                "learner_long_term_memory": learner_memories,
                "source_grounded_memory": [],
            },
            "last_memory_update_summary": self._default_memory_update_summary(program.language),
            "last_turn_output": None,
        }
        return self._refresh_learning_state_views(
            program=program,
            learning_state=learning_state,
            focus_node=focus_node,
        )

    def _ensure_learning_state(self, program: LearningProgram) -> dict[str, Any]:
        metadata = self._program_metadata(program)
        learning_state = metadata.get("learning_state")
        if isinstance(learning_state, dict) and learning_state.get("current_goal"):
            return learning_state

        nodes = sorted(program.nodes or [], key=lambda item: item.position)
        learning_state = self._build_initial_learning_state(
            program=program,
            nodes=nodes,
            weekly_minutes=int(program.weekly_minutes or 180),
        )
        metadata["learning_state_version"] = "guided_learning_v2"
        metadata["learning_state"] = learning_state
        program.metadata_json = _json_dumps(metadata)
        self.db.flush()
        return learning_state

    def _persist_learning_state(self, program: LearningProgram, learning_state: dict[str, Any]) -> None:
        metadata = self._program_metadata(program)
        metadata["learning_state_version"] = "guided_learning_v2"
        metadata["learning_state"] = learning_state
        program.metadata_json = _json_dumps(metadata)

    def _selected_source_refs(self, learning_state: dict[str, Any], limit: int = 2) -> list[str]:
        source_registry = learning_state.get("source_registry")
        if not isinstance(source_registry, dict):
            return []
        sources = source_registry.get("sources")
        if not isinstance(sources, list):
            return []
        refs: list[str] = []
        for source in sources[:limit]:
            if not isinstance(source, dict):
                continue
            label = str(source.get("source_ref") or source.get("file_name") or source.get("source_id") or "").strip()
            excerpt = str(source.get("excerpt") or "").strip()
            if label and excerpt:
                refs.append(f"{label}: {excerpt[:88]}")
                continue
            source_id = str(source.get("source_id") or "").strip()
            if source_id:
                refs.append(source_id)
        return refs

    def _program_attempt_summary(self, program_id: str) -> dict[str, dict[str, Any]]:
        attempts = (
            self.db.query(LearningAssessmentAttempt)
            .filter(
                LearningAssessmentAttempt.user_id == self.current_user.id,
                LearningAssessmentAttempt.program_id == program_id,
            )
            .order_by(LearningAssessmentAttempt.created_at.desc())
            .all()
        )
        summary: dict[str, dict[str, Any]] = {}
        for attempt in attempts:
            node_summary = summary.setdefault(attempt.node_id, {
                "attempts": 0,
                "passes": 0,
                "failures": 0,
                "latest_score": 0.0,
                "latest_feedback": attempt.feedback,
                "latest_at": attempt.created_at.isoformat() if attempt.created_at else None,
                "latest_recorded": False,
            })
            node_summary["attempts"] += 1
            if attempt.passed:
                node_summary["passes"] += 1
            else:
                node_summary["failures"] += 1
            if not node_summary["latest_recorded"]:
                node_summary["latest_score"] = float(attempt.score or 0.0)
                node_summary["latest_recorded"] = True
        return summary

    def _build_review_summary(self, program: LearningProgram) -> dict[str, Any]:
        nodes = sorted(program.nodes or [], key=lambda item: item.position)
        now = _utcnow()
        due_now = 0
        due_soon = 0
        verified = 0
        weak = 0
        for node in nodes:
            review_due_at = _coerce_utc(node.review_due_at)
            if review_due_at and review_due_at <= now:
                due_now += 1
            elif review_due_at and review_due_at <= now + timedelta(days=7):
                due_soon += 1
            if node.mastery_status == "completed":
                verified += 1
            elif float(node.mastery_score or 0.0) < 0.68:
                weak += 1

        pressure = "low"
        if due_now >= 3 or weak >= 3:
            pressure = "high"
        elif due_now >= 1 or weak >= 2 or due_soon >= 2:
            pressure = "medium"

        return {
            "due_now": due_now,
            "due_soon": due_soon,
            "verified_nodes": verified,
            "weak_nodes": weak,
            "review_pressure": pressure,
        }

    def _build_focus_snapshot(
        self,
        *,
        program: LearningProgram,
        focus_node: LearningProgramNode | None,
        learning_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        if focus_node is None:
            return None

        checklist_items = learning_state.get("progress_checklist") if isinstance(learning_state.get("progress_checklist"), list) else []
        focus_checklist = next(
            (item for item in checklist_items if isinstance(item, dict) and str(item.get("node_id") or "") == focus_node.id),
            None,
        )
        knowledge_map = learning_state.get("knowledge_map") if isinstance(learning_state.get("knowledge_map"), dict) else {}
        focus_skill = next(
            (
                item for item in knowledge_map.get("skills", [])
                if isinstance(item, dict) and str(item.get("node_id") or item.get("skill_id") or "") in {focus_node.id, focus_node.node_key}
            ),
            None,
        )
        return {
            "node_id": focus_node.id,
            "title": focus_node.title,
            "stage": focus_node.stage,
            "instructional_mode": self._instructional_mode_for_node(focus_node, is_review=self._is_review_due(focus_node)),
            "summary": focus_node.summary,
            "mastery_status": focus_node.mastery_status,
            "mastery_score": round(float(focus_node.mastery_score or 0.0), 2),
            "review_due_at": focus_node.review_due_at.isoformat() if focus_node.review_due_at else None,
            "evidence_count": int(focus_node.evidence_count or 0),
            "next_verification_action": (
                str(focus_checklist.get("next_verification_action") or "").strip()
                if isinstance(focus_checklist, dict)
                else focus_node.practice_task
            ),
            "reason": str(focus_checklist.get("reason") or "").strip() if isinstance(focus_checklist, dict) else None,
            "misconceptions": list((focus_skill or {}).get("misconceptions") or [])[:3] if isinstance(focus_skill, dict) else [],
            "success_criteria": _json_load_list(focus_node.success_criteria_json)[:3],
        }

    def _build_adaptive_plan(
        self,
        *,
        program: LearningProgram,
        learning_state: dict[str, Any],
        focus_node: LearningProgramNode | None,
    ) -> dict[str, Any]:
        nodes = sorted(program.nodes or [], key=lambda item: item.position)
        remaining_nodes = [node for node in nodes if node.mastery_status != "completed"]
        attempt_summary = self._program_attempt_summary(program.id)
        review_summary = self._build_review_summary(program)
        metadata = self._program_metadata(program)
        current_weekly_sessions = int(metadata.get("sessions_per_week") or max(1, round(int(program.weekly_minutes or 180) / 45)))
        recommended_sessions = current_weekly_sessions
        deadline_status = "none"
        days_left: int | None = None

        if program.target_date:
            deadline = _coerce_utc(program.target_date)
            if deadline is not None:
                days_left = max(0, (deadline - _utcnow()).days)
                weeks_left = max(1, math.ceil(max(1, days_left) / 7))
                required_sessions = max(1, len(remaining_nodes) + int(review_summary.get("due_now") or 0))
                recommended_sessions = max(current_weekly_sessions, math.ceil(required_sessions / weeks_left))
                if days_left <= 3 and remaining_nodes:
                    deadline_status = "urgent"
                elif recommended_sessions > current_weekly_sessions or review_summary.get("review_pressure") == "high":
                    deadline_status = "at_risk"
                else:
                    deadline_status = "on_track"

        stalled_nodes: list[dict[str, Any]] = []
        for node in nodes:
            node_attempts = attempt_summary.get(node.id, {})
            failures = int(node_attempts.get("failures") or 0)
            if node.mastery_status == "completed" or failures < 2:
                continue
            stalled_nodes.append({
                "node_id": node.id,
                "title": node.title,
                "failures": failures,
                "latest_score": round(float(node_attempts.get("latest_score") or 0.0), 2),
                "reason": (
                    "Needs remediation before moving on."
                    if program.language != "vi"
                    else "Can cuong co truoc khi di tiep."
                ),
            })

        recommended_minutes = max(20, round(int(program.weekly_minutes or 180) / max(1, recommended_sessions)))
        return {
            "deadline_status": deadline_status,
            "days_left": days_left,
            "remaining_nodes": len(remaining_nodes),
            "recommended_sessions_per_week": recommended_sessions,
            "recommended_minutes_per_session": recommended_minutes,
            "review_pressure": review_summary.get("review_pressure"),
            "due_now": review_summary.get("due_now"),
            "due_soon": review_summary.get("due_soon"),
            "stalled_nodes": stalled_nodes[:3],
            "focus_reason": (
                "Review is due now."
                if focus_node is not None and self._is_review_due(focus_node) and program.language != "vi"
                else "Da den lich on."
                if focus_node is not None and self._is_review_due(focus_node)
                else "This is the next unlocked learning block."
                if focus_node is not None and program.language != "vi"
                else "Day la block tiep theo da mo khoa."
                if focus_node is not None
                else (
                    "The roadmap is complete; stay on review maintenance."
                    if program.language != "vi"
                    else "Lo trinh da xong; uu tien che do on."
                )
            ),
        }

    def _refresh_learning_state_views(
        self,
        *,
        program: LearningProgram,
        learning_state: dict[str, Any],
        focus_node: LearningProgramNode | None,
        query_text: str | None = None,
    ) -> dict[str, Any]:
        self._sync_goal_milestones(program, learning_state)
        learning_state["source_registry"] = self._build_source_registry(
            program=program,
            focus_node=focus_node,
            learning_state=learning_state,
            query_text=query_text,
        )
        learning_state["review_summary"] = self._build_review_summary(program)
        learning_state["adaptive_plan"] = self._build_adaptive_plan(
            program=program,
            learning_state=learning_state,
            focus_node=focus_node,
        )
        learning_state["focus_snapshot"] = self._build_focus_snapshot(
            program=program,
            focus_node=focus_node,
            learning_state=learning_state,
        )
        return learning_state

    def _checklist_reason(
        self,
        *,
        node: LearningProgramNode,
        status: str,
        latest_result: dict[str, Any] | None,
        language: str,
        is_focus: bool,
    ) -> str:
        if latest_result and latest_result.get("node_id") == node.id:
            misconceptions = latest_result.get("misconceptions")
            if latest_result.get("passed"):
                return (
                    "Verified with fresh learner performance evidence."
                    if language != "vi"
                    else "Da co bang chung performance moi de xac nhan."
                )
            if isinstance(misconceptions, list) and misconceptions:
                return str(misconceptions[0])
        if status == "review_due":
            return (
                "Previously verified and now scheduled for spaced review."
                if language != "vi"
                else "Da tung duoc xac nhan va den lich on lai."
            )
        if status == "verified":
            return (
                "The learner has already shown enough evidence here."
                if language != "vi"
                else "Nguoi hoc da co du bang chung o muc nay."
            )
        if status == "partial":
            return (
                "Progress exists, but the evidence is still not strong enough."
                if language != "vi"
                else "Da co tien bo nhung bang chung chua du manh."
            )
        if status == "blocked":
            return (
                "Waiting on prerequisite work first."
                if language != "vi"
                else "Can di qua prerequisite truoc."
            )
        if is_focus:
            return (
                "This is the current instructional focus."
                if language != "vi"
                else "Day la trong tam day hoc hien tai."
            )
        return (
            "Not started yet."
            if language != "vi"
            else "Chua bat dau."
        )

    def _rebuild_progress_checklist(
        self,
        *,
        program: LearningProgram,
        learning_state: dict[str, Any],
        focus_node: LearningProgramNode | None,
        latest_result: dict[str, Any] | None = None,
        evidence_id: str | None = None,
    ) -> list[dict[str, Any]]:
        existing_items = {}
        for item in learning_state.get("progress_checklist", []):
            if isinstance(item, dict):
                node_id = str(item.get("node_id") or "").strip()
                if node_id:
                    existing_items[node_id] = item

        checklist: list[dict[str, Any]] = []
        for node in sorted(program.nodes or [], key=lambda item: item.position):
            existing = existing_items.get(node.id, {})
            evidence_ids = list(existing.get("evidence_ids") or [])
            if latest_result and latest_result.get("node_id") == node.id and evidence_id and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
            status = self._checklist_status_for_node(node, is_focus=focus_node is not None and focus_node.id == node.id)
            checklist.append({
                "item_id": existing.get("item_id") or f"chk_{node.id[:8]}",
                "node_id": node.id,
                "label": node.title,
                "status": status,
                "linked_skill_ids": existing.get("linked_skill_ids") or [node.node_key],
                "reason": self._checklist_reason(
                    node=node,
                    status=status,
                    latest_result=latest_result,
                    language=program.language,
                    is_focus=focus_node is not None and focus_node.id == node.id,
                ),
                "evidence_ids": evidence_ids[-5:],
                "next_verification_action": node.practice_task,
            })
        return checklist

    def _mastery_status_from_checklist(self, status: str, node: LearningProgramNode) -> str:
        if status == "verified":
            return "strong" if node.stage in {"integration", "capstone"} and float(node.mastery_score or 0.0) >= 0.84 else "operational"
        if status in {"partial", "review_due"}:
            return "partial"
        return "not_met"

    def _skill_confidence(
        self,
        *,
        node: LearningProgramNode,
        existing_confidence: float,
        latest_result: dict[str, Any] | None,
    ) -> float:
        if latest_result and latest_result.get("node_id") == node.id:
            score = float(latest_result.get("score") or 0.0)
            if latest_result.get("passed"):
                return round(min(0.98, max(existing_confidence, 0.35 + score * 0.6)), 2)
            return round(max(0.12, min(0.84, existing_confidence * 0.6 + score * 0.35 + 0.08)), 2)
        base_score = float(node.mastery_score or 0.0)
        if node.mastery_status == "completed":
            return round(max(existing_confidence, min(0.92, 0.42 + base_score * 0.5)), 2)
        return round(max(0.1, min(0.8, existing_confidence * 0.85 + base_score * 0.15)), 2)

    def _rebuild_knowledge_map(
        self,
        *,
        program: LearningProgram,
        learning_state: dict[str, Any],
        checklist: list[dict[str, Any]],
        latest_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing_skills = {}
        raw_skills = learning_state.get("knowledge_map", {}).get("skills", [])
        if isinstance(raw_skills, list):
            for skill in raw_skills:
                if isinstance(skill, dict):
                    existing_skills[str(skill.get("node_id") or skill.get("skill_id") or "")] = skill

        checklist_by_node = {
            str(item.get("node_id") or ""): item
            for item in checklist
            if isinstance(item, dict)
        }
        skills: list[dict[str, Any]] = []
        for node in sorted(program.nodes or [], key=lambda item: item.position):
            existing = existing_skills.get(node.id) or existing_skills.get(node.node_key) or {}
            checklist_item = checklist_by_node.get(node.id, {})
            latest_misconceptions = latest_result.get("misconceptions") if latest_result and latest_result.get("node_id") == node.id else None
            misconceptions = latest_misconceptions if isinstance(latest_misconceptions, list) and latest_misconceptions else existing.get("misconceptions") or _json_load_list(node.common_pitfalls_json)[:2]
            skills.append({
                "skill_id": node.node_key,
                "node_id": node.id,
                "name": node.title,
                "prerequisites": _json_load_list(node.prerequisites_json),
                "mastery_status": self._mastery_status_from_checklist(str(checklist_item.get("status") or "not_started"), node),
                "confidence": self._skill_confidence(
                    node=node,
                    existing_confidence=float(existing.get("confidence") or 0.12),
                    latest_result=latest_result,
                ),
                "last_evidence_ids": list(checklist_item.get("evidence_ids") or [])[-3:],
                "misconceptions": list(misconceptions)[:3],
                "review_due": node.review_due_at.isoformat() if node.review_due_at else None,
            })
        return {"skills": skills}

    def _upsert_memory_entry(
        self,
        *,
        memories: list[dict[str, Any]],
        kind: str,
        node_id: str,
        content: str,
        evidence_id: str,
        base_confidence: float,
    ) -> tuple[str, str]:
        for entry in memories:
            if not isinstance(entry, dict):
                continue
            if entry.get("kind") == kind and entry.get("node_id") == node_id and entry.get("status") != "inactive":
                supporting = list(entry.get("supporting_evidence_ids") or [])
                if evidence_id not in supporting:
                    supporting.append(evidence_id)
                entry["supporting_evidence_ids"] = supporting[-5:]
                entry["content"] = content
                entry["confidence"] = round(min(0.97, max(float(entry.get("confidence") or 0.0), base_confidence) + 0.06), 2)
                return "revised", content

        memories.append({
            "memory_id": f"mem_{uuid4().hex[:8]}",
            "kind": kind,
            "node_id": node_id,
            "content": content,
            "confidence": round(base_confidence, 2),
            "supporting_evidence_ids": [evidence_id],
            "status": "active",
        })
        return "added", content

    def _build_memory_update_summary(
        self,
        *,
        program: LearningProgram,
        learning_state: dict[str, Any],
        node: LearningProgramNode,
        next_focus_node: LearningProgramNode | None,
        latest_result: dict[str, Any],
        evidence_id: str,
    ) -> dict[str, Any]:
        memory = learning_state.setdefault("memory", {})
        session_memory = memory.setdefault("session_memory", {})
        long_term_memory = memory.setdefault("learner_long_term_memory", [])
        if not isinstance(long_term_memory, list):
            long_term_memory = []
            memory["learner_long_term_memory"] = long_term_memory

        added: list[str] = []
        revised: list[str] = []
        downgraded: list[str] = []

        session_memory["active_subtopic"] = next_focus_node.node_key if next_focus_node is not None else node.node_key
        session_memory["current_mode"] = self._instructional_mode_for_node(next_focus_node or node, is_review=next_focus_node is None and self._is_review_due(node))
        session_memory["current_question"] = (
            next_focus_node.practice_task
            if next_focus_node is not None
            else str(latest_result.get("next_action") or node.practice_task)
        )

        if latest_result.get("passed"):
            strength_content = (
                f"The learner can now move through '{node.title}' with performance evidence."
                if program.language != "vi"
                else f"Nguoi hoc da co bang chung de di qua '{node.title}'."
            )
            action, content = self._upsert_memory_entry(
                memories=long_term_memory,
                kind="verified_strength",
                node_id=node.id,
                content=strength_content,
                evidence_id=evidence_id,
                base_confidence=0.74,
            )
            (added if action == "added" else revised).append(content)

            for entry in long_term_memory:
                if not isinstance(entry, dict):
                    continue
                if entry.get("kind") != "recurring_misconception" or entry.get("node_id") != node.id or entry.get("status") == "inactive":
                    continue
                previous_confidence = float(entry.get("confidence") or 0.0)
                new_confidence = round(max(0.18, previous_confidence - 0.18), 2)
                entry["confidence"] = new_confidence
                if new_confidence <= 0.28:
                    entry["status"] = "inactive"
                downgraded.append(str(entry.get("content") or ""))
        else:
            misconceptions = latest_result.get("misconceptions")
            if isinstance(misconceptions, list) and misconceptions:
                misconception_content = str(misconceptions[0])
                action, content = self._upsert_memory_entry(
                    memories=long_term_memory,
                    kind="recurring_misconception",
                    node_id=node.id,
                    content=misconception_content,
                    evidence_id=evidence_id,
                    base_confidence=0.64,
                )
                (added if action == "added" else revised).append(content)
            for entry in long_term_memory:
                if not isinstance(entry, dict):
                    continue
                if entry.get("kind") != "verified_strength" or entry.get("node_id") != node.id or entry.get("status") == "inactive":
                    continue
                previous_confidence = float(entry.get("confidence") or 0.0)
                new_confidence = round(max(0.2, previous_confidence - 0.16), 2)
                entry["confidence"] = new_confidence
                if new_confidence <= 0.3:
                    entry["status"] = "inactive"
                downgraded.append(str(entry.get("content") or ""))

        score = float(latest_result.get("score") or 0.0)
        confidence = round(min(0.95, 0.35 + score * 0.5), 2)
        return {
            "added": added[:3],
            "revised": revised[:3],
            "downgraded": [item for item in downgraded[:2] if item],
            "confidence": confidence,
        }

    def _build_assistant_message(
        self,
        *,
        program: LearningProgram,
        node: LearningProgramNode,
        instructional_mode: str,
        next_step: str,
        score_payload: dict[str, Any] | None = None,
    ) -> str:
        if score_payload is not None:
            feedback = str(score_payload.get("feedback") or "").strip()
            if feedback:
                return f"{feedback}\n\nNext step: {next_step}" if program.language != "vi" else f"{feedback}\n\nBuoc tiep theo: {next_step}"

        if program.language == "vi":
            mode_line = {
                "teach": "Luot nay uu tien lam ro mental model va giu muc tieu hoc that.",
                "guided_practice": "Luot nay uu tien luyen co huong dan, khong nhay thang vao dap an cuoi.",
                "independent_practice": "Luot nay uu tien bai lam doc lap de lay bang chung that.",
                "remediate": "Luot nay uu tien go dung lo hong va sua misconception truoc khi tang do kho.",
                "review": "Luot nay uu tien goi lai va kiem tra do ben cua kien thuc.",
                "summarize_progress": "Luot nay uu tien tom tat tien do va buoc tiep theo.",
            }.get(instructional_mode, "Luot nay uu tien day dung trong tam.")
            return f"{mode_line}\n\nTrong tam: {node.title}.\nHay xu ly buoc nay: {next_step}"

        mode_line = {
            "teach": "This turn is for clarifying the core mental model before pushing difficulty.",
            "guided_practice": "This turn is guided practice, so we keep the learner thinking instead of jumping to the final answer.",
            "independent_practice": "This turn is for independent evidence, not just comfortable discussion.",
            "remediate": "This turn is for repairing a weak link before the learner moves forward.",
            "review": "This turn is a review check to see whether the idea still holds over time.",
            "summarize_progress": "This turn is for a concise progress summary and next step.",
        }.get(instructional_mode, "This turn stays focused on one concrete learning purpose.")
        return f"{mode_line}\n\nCurrent focus: {node.title}.\nDo this next: {next_step}"

    def _visible_checklist_slice(
        self,
        checklist: list[dict[str, Any]],
        focus_node: LearningProgramNode | None,
    ) -> list[dict[str, Any]]:
        if focus_node is None:
            return checklist[:3]
        focus_index = 0
        for index, item in enumerate(checklist):
            if str(item.get("node_id") or "") == focus_node.id:
                focus_index = index
                break
        start = max(0, focus_index - 1)
        end = min(len(checklist), focus_index + 2)
        return checklist[start:end]

    def _build_turn_output(
        self,
        *,
        program: LearningProgram,
        node: LearningProgramNode | None,
        learning_state: dict[str, Any],
        instructional_mode: str,
        assistant_message: str,
        progress_checklist: list[dict[str, Any]],
        memory_update_summary: dict[str, Any],
        evidence_collected: list[Any],
        next_step: str | None,
        confidence_level: float,
    ) -> dict[str, Any]:
        return {
            "instructional_mode": instructional_mode,
            "assistant_message": assistant_message,
            "progress_checklist": progress_checklist,
            "memory_update_summary": memory_update_summary,
            "evidence_collected": evidence_collected,
            "selected_source_refs": self._selected_source_refs(learning_state),
            "confidence_level": round(max(0.0, min(1.0, confidence_level)), 2),
            "next_step": next_step,
            "focus_node_id": node.id if node is not None else None,
            "focus_node_title": node.title if node is not None else None,
        }

    def list_programs(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            self.db.query(LearningProgram)
            .options(joinedload(LearningProgram.nodes), joinedload(LearningProgram.workspace))
            .filter(LearningProgram.user_id == self.current_user.id)
        )
        if workspace_id is not None:
            if workspace_id == "":
                query = query.filter(LearningProgram.workspace_id.is_(None))
            else:
                query = query.filter(LearningProgram.workspace_id == workspace_id)
        programs = query.order_by(LearningProgram.updated_at.desc(), LearningProgram.created_at.desc()).all()
        return [self._serialize_program_summary(program) for program in programs]

    def list_due_reviews(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        now = _utcnow()
        query = (
            self.db.query(LearningProgramNode)
            .join(LearningProgram, LearningProgram.id == LearningProgramNode.program_id)
            .filter(LearningProgram.user_id == self.current_user.id)
            .filter(LearningProgramNode.review_due_at.is_not(None))
            .filter(LearningProgramNode.review_due_at <= now)
        )
        if workspace_id is not None:
            if workspace_id == "":
                query = query.filter(LearningProgram.workspace_id.is_(None))
            else:
                query = query.filter(LearningProgram.workspace_id == workspace_id)
        nodes = (
            query.options(joinedload(LearningProgramNode.program))
            .order_by(LearningProgramNode.review_due_at.asc())
            .all()
        )
        return [self._serialize_review_item(node) for node in nodes]

    def get_live_state(
        self,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        program_id: str | None = None,
    ) -> dict[str, Any]:
        program = self._resolve_live_program(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            program_id=program_id,
        )
        if program is None:
            return {
                "enabled": False,
                "source": "none",
                "conversation_id": conversation_id,
                "program": None,
                "focus_node": None,
                "active_session": None,
                "assumptions": [],
                "next_action": None,
                "coach_brief": None,
                "learning_state": None,
                "progress_checklist": [],
                "memory_update_summary": None,
                "turn_output": None,
            }

        learning_state = self._ensure_learning_state(program)
        metadata = self._program_metadata(program)
        copilot = metadata.get("copilot") if isinstance(metadata.get("copilot"), dict) else {}
        linked_conversation_id = str(copilot.get("conversation_id") or "").strip() or None
        resolved_conversation_id = conversation_id or linked_conversation_id
        focus_node = self._get_next_focus_node(program)
        learning_state = self._refresh_learning_state_views(
            program=program,
            learning_state=learning_state,
            focus_node=focus_node,
        )
        active_session = self._get_latest_session_for_program(program, conversation_id=resolved_conversation_id)
        next_action = (
            str(copilot.get("next_action") or "").strip()
            or (focus_node.title if focus_node is not None else None)
        )
        coach_brief = str(copilot.get("coach_brief") or "").strip() or self._build_coach_brief(program, focus_node)
        assumptions = self._normalize_assumptions(copilot.get("assumptions"))

        return {
            "enabled": True,
            "source": self._resolve_live_source(
                program=program,
                conversation_id=resolved_conversation_id,
                program_id=program_id,
            ),
            "conversation_id": resolved_conversation_id,
            "program": self._serialize_program_detail(program),
            "focus_node": self._serialize_node(focus_node),
            "active_session": self._serialize_session(active_session),
            "assumptions": assumptions,
            "next_action": next_action,
            "coach_brief": coach_brief,
            "learning_state": learning_state,
            "progress_checklist": learning_state.get("progress_checklist", []),
            "memory_update_summary": learning_state.get("last_memory_update_summary"),
            "turn_output": learning_state.get("last_turn_output"),
        }

    def sync_chat_copilot(
        self,
        *,
        conversation_id: str | None,
        workspace_id: str | None,
        message_text: str,
        learning_mode: str = "auto",
        preferred_program_id: str | None = None,
    ) -> dict[str, Any]:
        clean_message = _strip_learning_transport_artifacts(message_text or "")
        if learning_mode == "off" or not clean_message:
            return {"enabled": False}

        program = self._resolve_live_program(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            program_id=preferred_program_id,
        )
        had_existing_program = program is not None
        should_activate = (
            learning_mode == "teacher"
            or had_existing_program
            or self._looks_like_learning_intent(clean_message)
        )
        if not should_activate:
            return {"enabled": False}

        if program is None:
            seed = self._infer_program_seed(clean_message)
            created = self.create_program(
                title=seed["title"],
                topic=seed["topic"],
                goal=seed["goal"],
                outcome_target=seed["outcome_target"],
                current_level=seed["current_level"],
                learning_style="socratic",
                weekly_minutes=seed["weekly_minutes"],
                workspace_id=workspace_id,
                target_date=None,
                language=seed["language"],
            )
            program = self._get_program_or_404(created["id"])

        learning_state = self._ensure_learning_state(program)
        self._unlock_following_nodes(program)
        focus_node = self._get_next_focus_node(program)
        learning_state = self._refresh_learning_state_views(
            program=program,
            learning_state=learning_state,
            focus_node=focus_node,
            query_text=clean_message,
        )
        assessment_result: dict[str, Any] | None = None
        turn_output: dict[str, Any] | None = learning_state.get("last_turn_output") if isinstance(learning_state.get("last_turn_output"), dict) else None
        if (
            had_existing_program
            and focus_node is not None
            and self._should_grade_chat_turn(clean_message, focus_node)
        ):
            session = self._ensure_chat_session(
                program=program,
                node=focus_node,
                conversation_id=conversation_id,
            )
            submission = self.submit_session_response(session.id, clean_message)
            assessment_result = submission.get("result") if isinstance(submission, dict) else None
            turn_output = submission.get("turn_output") if isinstance(submission, dict) else None
            program = self._get_program_or_404(program.id)
            learning_state = self._ensure_learning_state(program)
            focus_node = self._get_next_focus_node(program)
            learning_state = self._refresh_learning_state_views(
                program=program,
                learning_state=learning_state,
                focus_node=focus_node,
                query_text=clean_message,
            )

        metadata = self._touch_copilot_metadata(
            program=program,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            latest_message=clean_message,
            assessment_result=assessment_result,
        )
        coach_brief = self._build_coach_brief(program, focus_node)
        copilot = metadata.setdefault("copilot", {})
        if isinstance(copilot, dict):
            copilot["coach_brief"] = coach_brief
            copilot["next_action"] = self._build_next_action(program, focus_node, assessment_result)
        program.metadata_json = _json_dumps(metadata)
        self.db.commit()
        self.db.refresh(program)

        return {
            "enabled": True,
            "program_id": program.id,
            "program_title": program.title,
            "focus_node": self._serialize_node(focus_node),
            "assumptions": self._normalize_assumptions(copilot.get("assumptions")),
            "coach_brief": coach_brief,
            "next_action": copilot.get("next_action"),
            "assessment": assessment_result,
            "learning_state": learning_state,
            "progress_checklist": learning_state.get("progress_checklist", []),
            "memory_update_summary": learning_state.get("last_memory_update_summary"),
            "turn_output": turn_output or learning_state.get("last_turn_output"),
            "system_prompt": self._build_chat_teacher_prompt(
                program=program,
                focus_node=focus_node,
                assumptions=self._normalize_assumptions(copilot.get("assumptions")),
                assessment_result=assessment_result,
            ),
        }

    def get_program(self, program_id: str) -> dict[str, Any]:
        program = self._get_program_or_404(program_id)
        return self._serialize_program_detail(program)

    def delete_program(self, program_id: str) -> None:
        program = self._get_program_or_404(program_id)
        self.db.delete(program)
        self.db.commit()

    def create_program(
        self,
        *,
        title: str | None,
        topic: str,
        goal: str,
        outcome_target: str | None,
        current_level: str,
        learning_style: str,
        weekly_minutes: int,
        workspace_id: str | None,
        target_date: datetime | None,
        language: str,
    ) -> dict[str, Any]:
        topic_clean = topic.strip()
        goal_clean = goal.strip()
        if not topic_clean:
            raise HTTPException(status_code=400, detail="Topic is required")
        if not goal_clean:
            raise HTTPException(status_code=400, detail="Goal is required")

        workspace = None
        if workspace_id:
            workspace = (
                self.db.query(Workspace)
                .filter(Workspace.id == workspace_id, Workspace.user_id == self.current_user.id)
                .first()
            )
            if workspace is None:
                raise HTTPException(status_code=404, detail="Workspace not found")

        normalized_level = self._normalize_level(current_level)
        normalized_style = self._normalize_style(learning_style)
        normalized_language = self._normalize_language(language)
        weekly_minutes = max(30, min(int(weekly_minutes or 180), 2400))
        domain = self._detect_domain(topic_clean, goal_clean)
        blueprints = self._build_program_blueprints(
            topic=topic_clean,
            goal=goal_clean,
            outcome_target=(outcome_target or "").strip() or None,
            domain=domain,
            current_level=normalized_level,
            learning_style=normalized_style,
            language=normalized_language,
        )
        resolved_title = (title or "").strip() or self._default_program_title(topic_clean, normalized_language)
        metadata = self._build_program_metadata(
            weekly_minutes=weekly_minutes,
            current_level=normalized_level,
            domain=domain,
            language=normalized_language,
            target_date=target_date,
        )

        program = LearningProgram(
            user_id=self.current_user.id,
            workspace_id=workspace.id if workspace else None,
            title=resolved_title,
            topic=topic_clean,
            domain=domain,
            goal=goal_clean,
            outcome_target=(outcome_target or "").strip() or None,
            current_level=normalized_level,
            learning_style=normalized_style,
            language=normalized_language,
            weekly_minutes=weekly_minutes,
            status="active",
            metadata_json=_json_dumps(metadata),
            target_date=target_date,
        )
        self.db.add(program)
        self.db.flush()

        created_nodes: list[LearningProgramNode] = []
        for index, blueprint in enumerate(blueprints, start=1):
            node = LearningProgramNode(
                program_id=program.id,
                position=index,
                node_key=blueprint.node_key,
                stage=blueprint.stage,
                title=blueprint.title,
                summary=blueprint.summary,
                explanation=blueprint.explanation,
                worked_example=blueprint.worked_example,
                practice_task=blueprint.practice_task,
                reflection_prompt=blueprint.reflection_prompt,
                estimated_minutes=blueprint.estimated_minutes,
                difficulty=blueprint.difficulty,
                prerequisites_json=_json_dumps([bp.node_key for bp in blueprints[: index - 1]]),
                common_pitfalls_json=_json_dumps(blueprint.common_pitfalls),
                expected_keywords_json=_json_dumps(blueprint.expected_keywords),
                success_criteria_json=_json_dumps(blueprint.success_criteria),
                resources_json=_json_dumps(blueprint.resources),
                metadata_json=_json_dumps({"domain": domain, "style": normalized_style}),
                mastery_status="ready" if index == 1 else "locked",
            )
            self.db.add(node)
            created_nodes.append(node)

        self.db.flush()
        metadata["learning_state"] = self._build_initial_learning_state(
            program=program,
            nodes=created_nodes,
            weekly_minutes=weekly_minutes,
        )
        metadata["learning_state_version"] = "guided_learning_v2"
        program.metadata_json = _json_dumps(metadata)

        self.db.commit()
        self.db.refresh(program)
        return self.get_program(program.id)

    def start_session(self, program_id: str, *, node_id: str | None = None) -> dict[str, Any]:
        program = self._get_program_or_404(program_id)
        learning_state = self._ensure_learning_state(program)
        self._unlock_following_nodes(program)
        node = self._resolve_session_node(program, node_id)
        checklist = self._rebuild_progress_checklist(
            program=program,
            learning_state=learning_state,
            focus_node=node,
        )
        learning_state["progress_checklist"] = checklist
        session_memory = learning_state.setdefault("memory", {}).setdefault("session_memory", {})
        session_memory["active_subtopic"] = node.node_key
        session_memory["current_mode"] = self._instructional_mode_for_node(node, is_review=self._is_review_due(node))
        session_memory["current_question"] = node.practice_task
        learning_state = self._refresh_learning_state_views(
            program=program,
            learning_state=learning_state,
            focus_node=node,
        )
        lesson = self._build_session_packet(program, node, learning_state=learning_state)
        learning_state["last_turn_output"] = lesson.get("turn_output")
        self._persist_learning_state(program, learning_state)

        session = LearningSession(
            user_id=self.current_user.id,
            program_id=program.id,
            node_id=node.id,
            status="active",
            lesson_snapshot_json=_json_dumps(lesson),
        )
        node.mastery_status = "reviewing" if node.mastery_status == "completed" else "ready"
        node.last_practiced_at = _utcnow()
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return self._serialize_session(session, lesson_override=lesson) or {}

    def submit_session_response(self, session_id: str, answer_text: str) -> dict[str, Any]:
        session = self._get_session_or_404(session_id)
        answer = (answer_text or "").strip()
        if not answer:
            raise HTTPException(status_code=400, detail="Answer is required")

        node = session.node
        program = session.program
        learning_state = self._ensure_learning_state(program)
        session_lesson = _json_load_dict(session.lesson_snapshot_json)
        instructional_mode = str(
            session_lesson.get("instructional_mode")
            or session_lesson.get("turn_output", {}).get("instructional_mode")
            or self._instructional_mode_for_node(node, is_review=self._is_review_due(node))
        )
        score_payload = self._grade_node_answer(
            program,
            node,
            answer,
            instructional_mode=instructional_mode,
            learning_state=learning_state,
        )
        session.attempt_count = int(session.attempt_count or 0) + 1
        session.status = "completed"
        session.completed_at = _utcnow()
        now = _utcnow()
        evidence_id = f"ev_{uuid4().hex[:8]}"
        score_payload["instructional_mode"] = instructional_mode
        score_payload["node_id"] = node.id
        score_payload["evidence_id"] = evidence_id
        score_payload["used_hints"] = instructional_mode in {"teach", "guided_practice", "diagnose", "remediate"}
        base_score = float(score_payload["score"])
        if base_score >= 0.88 and instructional_mode in {"independent_practice", "review"} and not score_payload["used_hints"]:
            evidence_strength = "strong"
        elif base_score >= 0.7:
            evidence_strength = "medium"
        else:
            evidence_strength = "weak"
        score_payload["evidence_strength"] = evidence_strength
        score_payload["evidence_type"] = {
            "teach": "recognition_or_explanation",
            "guided_practice": "assisted_practice",
            "independent_practice": "independent_problem_solving",
            "remediate": "misconception_repair",
            "evaluate": "independent_problem_solving",
            "review": "review_recall",
        }.get(instructional_mode, "learner_response")
        evidence_summary = (
            f"Passed '{node.title}' with score {round(float(score_payload['score']) * 100)}%."
            if score_payload["passed"]
            else f"Still partial on '{node.title}' with score {round(float(score_payload['score']) * 100)}%."
        )
        score_payload["evidence_summary"] = evidence_summary

        self.db.add(LearningAssessmentAttempt(
            user_id=self.current_user.id,
            program_id=program.id,
            node_id=node.id,
            session_id=session.id,
            answer_text=answer,
            score=score_payload["score"],
            passed=score_payload["passed"],
            strengths_json=_json_dumps(score_payload["strengths"]),
            misconceptions_json=_json_dumps(score_payload["misconceptions"]),
            feedback=score_payload["feedback"],
        ))
        self.db.flush()

        node.last_practiced_at = now
        node.mastery_score = max(float(node.mastery_score or 0.0), float(score_payload["score"]))
        if score_payload["passed"]:
            node.evidence_count = int(node.evidence_count or 0) + 1
            node.completed_at = now
            node.mastery_status = "completed"
            node.review_due_at = now + timedelta(days=self._review_day_for_evidence(
                node.evidence_count,
                latest_score=float(score_payload["score"]),
                stage=node.stage,
            ))
            if self._is_generated_remedial_node(node):
                remedial_metadata = self._node_metadata(node)
                self._clear_remedial_flag(program, str(remedial_metadata.get("parent_node_id") or "").strip() or None)
            else:
                self._clear_remedial_flag(program, node.id)
        else:
            node.mastery_status = "reviewing"
            node.review_due_at = now + timedelta(hours=18 if float(score_payload["score"]) < 0.45 else 24)
            self._maybe_insert_remedial_node(
                program=program,
                parent_node=node,
                latest_result=score_payload,
                evidence_id=evidence_id,
            )

        self._unlock_following_nodes(program)
        if all(item.mastery_status == "completed" for item in program.nodes):
            program.status = "completed"

        focus_node = self._get_next_focus_node(program)
        evidence_log = learning_state.setdefault("evidence_log", [])
        if not isinstance(evidence_log, list):
            evidence_log = []
            learning_state["evidence_log"] = evidence_log
        evidence_event = {
            "evidence_id": evidence_id,
            "type": score_payload["evidence_type"],
            "skill_ids": [node.node_key],
            "strength": score_payload["evidence_strength"],
            "summary": evidence_summary,
            "used_hints": score_payload["used_hints"],
            "source_turn_id": session.id,
            "timestamp": now.isoformat(),
        }
        evidence_log.append(evidence_event)
        learning_state["evidence_log"] = evidence_log[-80:]

        checklist = self._rebuild_progress_checklist(
            program=program,
            learning_state=learning_state,
            focus_node=focus_node,
            latest_result=score_payload,
            evidence_id=evidence_id,
        )
        learning_state["progress_checklist"] = checklist
        learning_state["knowledge_map"] = self._rebuild_knowledge_map(
            program=program,
            learning_state=learning_state,
            checklist=checklist,
            latest_result=score_payload,
        )
        memory_update_summary = self._build_memory_update_summary(
            program=program,
            learning_state=learning_state,
            node=node,
            next_focus_node=focus_node,
            latest_result=score_payload,
            evidence_id=evidence_id,
        )
        learning_state["last_memory_update_summary"] = memory_update_summary
        learning_state = self._refresh_learning_state_views(
            program=program,
            learning_state=learning_state,
            focus_node=focus_node,
            query_text=answer,
        )
        next_step = (
            focus_node.practice_task
            if focus_node is not None and focus_node.id != node.id
            else str(score_payload.get("next_action") or node.practice_task)
        )
        assistant_message = self._build_assistant_message(
            program=program,
            node=focus_node or node,
            instructional_mode=self._instructional_mode_for_node(focus_node or node, is_review=focus_node is None and self._is_review_due(node)),
            next_step=next_step,
            score_payload=score_payload,
        )
        turn_output = self._build_turn_output(
            program=program,
            node=focus_node or node,
            learning_state=learning_state,
            instructional_mode=self._instructional_mode_for_node(focus_node or node, is_review=focus_node is None and self._is_review_due(node)),
            assistant_message=assistant_message,
            progress_checklist=self._visible_checklist_slice(checklist, focus_node or node),
            memory_update_summary=memory_update_summary,
            evidence_collected=[evidence_event],
            next_step=next_step,
            confidence_level=0.42 + float(score_payload["score"]) * 0.5,
        )
        learning_state["last_turn_output"] = turn_output
        self._persist_learning_state(program, learning_state)
        score_payload["progress_checklist"] = turn_output["progress_checklist"]
        score_payload["memory_update_summary"] = memory_update_summary
        score_payload["evidence_collected"] = [evidence_event]
        score_payload["selected_source_refs"] = turn_output["selected_source_refs"]
        score_payload["confidence_level"] = turn_output["confidence_level"]
        score_payload["assistant_message"] = assistant_message
        score_payload["next_step"] = next_step
        session.feedback_json = _json_dumps(score_payload)

        self.db.commit()
        self.db.refresh(session)
        self.db.refresh(node)
        self.db.refresh(program)

        next_node = self._get_next_focus_node(program)
        return {
            "session": self._serialize_session(session),
            "result": score_payload,
            "node": self._serialize_node(node),
            "program": self._serialize_program_summary(program),
            "next_node": self._serialize_node(next_node),
            "learning_state": learning_state,
            "turn_output": turn_output,
        }

    def _get_program_or_404(self, program_id: str) -> LearningProgram:
        program = (
            self.db.query(LearningProgram)
            .options(
                joinedload(LearningProgram.nodes),
                joinedload(LearningProgram.workspace),
                joinedload(LearningProgram.sessions),
            )
            .filter(
                LearningProgram.id == program_id,
                LearningProgram.user_id == self.current_user.id,
            )
            .first()
        )
        if program is None:
            raise HTTPException(status_code=404, detail="Learning program not found")
        return program

    def _get_session_or_404(self, session_id: str) -> LearningSession:
        session = (
            self.db.query(LearningSession)
            .options(
                joinedload(LearningSession.program).joinedload(LearningProgram.nodes),
                joinedload(LearningSession.node),
            )
            .filter(
                LearningSession.id == session_id,
                LearningSession.user_id == self.current_user.id,
            )
            .first()
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Learning session not found")
        return session

    def _serialize_program_summary(self, program: LearningProgram) -> dict[str, Any]:
        nodes = sorted(program.nodes or [], key=lambda item: item.position)
        total_nodes = len(nodes)
        completed_nodes = sum(1 for node in nodes if node.mastery_status == "completed")
        next_node = self._get_next_focus_node(program)
        due_reviews = sum(1 for node in nodes if self._is_review_due(node))
        metadata = _json_load_dict(program.metadata_json)
        learning_state = metadata.get("learning_state") if isinstance(metadata.get("learning_state"), dict) else None
        return {
            "id": program.id,
            "title": program.title,
            "topic": program.topic,
            "domain": program.domain,
            "goal": program.goal,
            "outcome_target": program.outcome_target,
            "current_level": program.current_level,
            "learning_style": program.learning_style,
            "language": program.language,
            "weekly_minutes": int(program.weekly_minutes or 0),
            "status": program.status,
            "workspace_id": program.workspace_id,
            "workspace_name": program.workspace.name if program.workspace else None,
            "completed_nodes": completed_nodes,
            "total_nodes": total_nodes,
            "completion_ratio": (completed_nodes / total_nodes) if total_nodes else 0.0,
            "estimated_total_minutes": sum(int(node.estimated_minutes or 0) for node in nodes),
            "due_review_count": due_reviews,
            "next_node_id": next_node.id if next_node is not None else None,
            "next_node_title": next_node.title if next_node is not None else None,
            "target_date": program.target_date.isoformat() if program.target_date else None,
            "pace_label": metadata.get("pace_label"),
            "active_goal": learning_state.get("current_goal") if learning_state else None,
            "last_turn_output": learning_state.get("last_turn_output") if learning_state else None,
            "created_at": program.created_at.isoformat() if program.created_at else None,
            "updated_at": program.updated_at.isoformat() if program.updated_at else None,
        }

    def _serialize_program_detail(self, program: LearningProgram) -> dict[str, Any]:
        summary = self._serialize_program_summary(program)
        learning_state = self._ensure_learning_state(program)
        learning_state = self._refresh_learning_state_views(
            program=program,
            learning_state=learning_state,
            focus_node=self._get_next_focus_node(program),
        )
        latest_session = None
        if program.sessions:
            latest_session = sorted(
                program.sessions,
                key=lambda item: _coerce_utc(item.started_at) or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )[0]
        return {
            **summary,
            "nodes": [self._serialize_node(node) for node in sorted(program.nodes or [], key=lambda item: item.position)],
            "latest_session": self._serialize_session(latest_session),
            "workspace_resources": self._load_workspace_resources(program.workspace_id),
            "learning_state": learning_state,
            "progress_checklist": learning_state.get("progress_checklist", []),
            "source_registry": learning_state.get("source_registry", {"sources": []}),
            "memory_update_summary": learning_state.get("last_memory_update_summary"),
        }

    def _serialize_node(self, node: LearningProgramNode | None) -> dict[str, Any] | None:
        if node is None:
            return None
        return {
            "id": node.id,
            "program_id": node.program_id,
            "position": int(node.position or 0),
            "node_key": node.node_key,
            "stage": node.stage,
            "title": node.title,
            "summary": node.summary,
            "explanation": node.explanation,
            "worked_example": node.worked_example,
            "practice_task": node.practice_task,
            "reflection_prompt": node.reflection_prompt,
            "estimated_minutes": int(node.estimated_minutes or 0),
            "difficulty": int(node.difficulty or 1),
            "prerequisites": _json_load_list(node.prerequisites_json),
            "common_pitfalls": _json_load_list(node.common_pitfalls_json),
            "expected_keywords": _json_load_list(node.expected_keywords_json),
            "success_criteria": _json_load_list(node.success_criteria_json),
            "resources": _json_load_list(node.resources_json),
            "mastery_status": node.mastery_status,
            "mastery_score": float(node.mastery_score or 0.0),
            "evidence_count": int(node.evidence_count or 0),
            "last_practiced_at": node.last_practiced_at.isoformat() if node.last_practiced_at else None,
            "review_due_at": node.review_due_at.isoformat() if node.review_due_at else None,
            "completed_at": node.completed_at.isoformat() if node.completed_at else None,
        }

    def _serialize_session(
        self,
        session: LearningSession | None,
        *,
        lesson_override: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if session is None:
            return None
        lesson = lesson_override or _json_load_dict(session.lesson_snapshot_json)
        feedback = _json_load_dict(session.feedback_json)
        return {
            "id": session.id,
            "program_id": session.program_id,
            "node_id": session.node_id,
            "conversation_id": session.conversation_id,
            "status": session.status,
            "attempt_count": int(session.attempt_count or 0),
            "lesson": lesson,
            "feedback": feedback if feedback else None,
            "turn_output": lesson.get("turn_output") if isinstance(lesson, dict) else None,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        }

    def _serialize_review_item(self, node: LearningProgramNode) -> dict[str, Any]:
        return {
            "program_id": node.program.id,
            "program_title": node.program.title,
            "node": self._serialize_node(node),
        }

    def _load_workspace_resources(self, workspace_id: str | None) -> list[dict[str, Any]]:
        if not workspace_id:
            return []
        items = (
            self.db.query(KnowledgeItem)
            .filter(KnowledgeItem.user_id == self.current_user.id, KnowledgeItem.workspace_id == workspace_id)
            .order_by(KnowledgeItem.updated_at.desc(), KnowledgeItem.created_at.desc())
            .limit(5)
            .all()
        )
        return [
            {
                "id": item.id,
                "title": item.title,
                "content_type": item.content_type,
                "is_favorite": bool(item.is_favorite),
            }
            for item in items
        ]

    def _program_metadata(self, program: LearningProgram) -> dict[str, Any]:
        return _json_load_dict(program.metadata_json)

    def _normalize_assumptions(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _program_matches_conversation(
        self,
        program: LearningProgram,
        conversation_id: str | None,
    ) -> bool:
        if not conversation_id:
            return False
        metadata = self._program_metadata(program)
        copilot = metadata.get("copilot") if isinstance(metadata.get("copilot"), dict) else {}
        linked_conversation_id = str(copilot.get("conversation_id") or "").strip()
        return linked_conversation_id == conversation_id

    def _resolve_live_source(
        self,
        *,
        program: LearningProgram,
        conversation_id: str | None,
        program_id: str | None,
    ) -> str:
        if program_id:
            return "selected"
        if self._program_matches_conversation(program, conversation_id):
            return "conversation"
        return "workspace_recent"

    def _resolve_live_program(
        self,
        *,
        conversation_id: str | None,
        workspace_id: str | None,
        program_id: str | None,
    ) -> LearningProgram | None:
        if program_id:
            try:
                return self._get_program_or_404(program_id)
            except HTTPException:
                return None

        query = (
            self.db.query(LearningProgram)
            .options(
                joinedload(LearningProgram.nodes),
                joinedload(LearningProgram.workspace),
                joinedload(LearningProgram.sessions),
            )
            .filter(LearningProgram.user_id == self.current_user.id)
        )
        if workspace_id is not None:
            if workspace_id == "":
                query = query.filter(LearningProgram.workspace_id.is_(None))
            else:
                query = query.filter(LearningProgram.workspace_id == workspace_id)
        programs = query.order_by(LearningProgram.updated_at.desc(), LearningProgram.created_at.desc()).all()
        if conversation_id:
            for program in programs:
                if self._program_matches_conversation(program, conversation_id):
                    return program
            return None
        if programs:
            return programs[0]
        return None

    def _get_latest_session_for_program(
        self,
        program: LearningProgram,
        *,
        conversation_id: str | None = None,
    ) -> LearningSession | None:
        sessions = list(program.sessions or [])
        if conversation_id:
            matching = [item for item in sessions if (item.conversation_id or "") == conversation_id]
            if matching:
                sessions = matching
        if not sessions:
            return None
        return sorted(
            sessions,
            key=lambda item: _coerce_utc(item.started_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[0]

    def _looks_like_learning_intent(self, message_text: str) -> bool:
        text = _normalize_text(message_text, ascii_fold=True)
        if len(text) < 8:
            return False

        explicit_patterns = (
            "muon hoc",
            "toi muon hoc",
            "minh muon hoc",
            "em muon hoc",
            "bat dau tu dau",
            "lo trinh",
            "roadmap",
            "tu hoc",
            "on thi",
            "muon hieu",
            "learn ",
            "want to learn",
            "study ",
            "how do i start learning",
            "teach me",
            "help me learn",
        )
        return any(marker in text for marker in explicit_patterns)

    def _infer_program_seed(self, message_text: str) -> dict[str, Any]:
        text = _strip_learning_transport_artifacts(" ".join((message_text or "").strip().split()))
        normalized = _normalize_text(text, ascii_fold=True)
        language = (
            "vi"
            if any(marker in normalized for marker in ("muon", "hoc", "lo trinh", "bat dau", "tim hieu", "on thi"))
            else "en"
        )

        topic = self._extract_topic_from_message(text)
        outcome_target = self._extract_outcome_target(text, language)
        current_level = self._infer_current_level(normalized)
        weekly_minutes = self._extract_weekly_minutes(normalized)
        title = self._default_program_title(topic, language)

        goal = text
        if len(goal) < 24:
            goal = (
                f"Toi muon hoc {topic} theo kieu tro chuyen, co roadmap va feedback that."
                if language == "vi"
                else f"I want to learn {topic} through conversation with a structured roadmap and real feedback."
            )

        return {
            "title": title,
            "topic": topic,
            "goal": goal,
            "outcome_target": outcome_target,
            "current_level": current_level,
            "weekly_minutes": weekly_minutes,
            "language": language,
        }

    def _extract_topic_from_message(self, message_text: str) -> str:
        text = _strip_learning_transport_artifacts(" ".join((message_text or "").strip().split()))
        patterns = (
            r"(?:tôi muốn học|toi muon hoc|mình muốn học|minh muon hoc|em muốn học|em muon hoc|muốn học|muon hoc|học|hoc|tìm hiểu|tim hieu|ôn|on|study|learn|understand)\s+(.+?)(?:\s+(?:để|de|so that|to)\s+.+)?$",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = re.split(r"[,.!?;]", match.group(1).strip())[0].strip()
                candidate = re.sub(r"^(ve|about)\s+", "", candidate, flags=re.IGNORECASE).strip()
                candidate = re.sub(r"^(?:một|mot)\s+ngôn\s+ngữ(?:\s+mới)?\s+là\s+", "", candidate, flags=re.IGNORECASE).strip()
                candidate = re.sub(r"^(?:mot)\s+ngon\s+ngu(?:\s+moi)?\s+la\s+", "", candidate, flags=re.IGNORECASE).strip()
                candidate = re.sub(r"^(?:a|an|new)\s+language\s+(?:called|named|is)\s+", "", candidate, flags=re.IGNORECASE).strip()
                if 2 <= len(candidate) <= 80:
                    return candidate

        fallback = re.split(r"[.!?\n]", text)[0].strip()
        fallback = re.sub(r"^(tôi|toi|mình|minh|em|i)\s+(muốn|muon|want)\s+", "", fallback, flags=re.IGNORECASE).strip()
        return fallback[:80] if fallback else "chu de moi"

    def _extract_outcome_target(self, message_text: str, language: str) -> str | None:
        text = " ".join((message_text or "").strip().split())
        patterns = (
            r"\bde\s+(.+)$",
            r"\bso that\s+(.+)$",
            r"\bto\s+(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip(" .!?")
                if len(candidate) >= 12:
                    return candidate[:240]
        if language == "vi":
            return "Co the giai thich lai bang loi cua minh va ap dung vao mot viec that."
        return "Be able to explain it clearly in my own words and apply it to a real task."

    def _infer_current_level(self, normalized_text: str) -> str:
        if any(marker in normalized_text for marker in ("mat goc", "chua biet gi", "beginner", "new to", "from scratch")):
            return "beginner"
        if any(marker in normalized_text for marker in ("nang cao", "advanced", "chuyen sau", "already know")):
            return "advanced"
        if any(marker in normalized_text for marker in ("co ban roi", "intermediate", "biet mot it", "have some basics")):
            return "intermediate"
        return "beginner"

    def _extract_weekly_minutes(self, normalized_text: str) -> int:
        minute_week_match = re.search(r"(\d{1,3})\s*(?:phut|phut\/tuan|min(?:ute)?s?)\s*(?:\/|\s*(?:per)?\s*)?(?:tuan|week)?", normalized_text)
        if minute_week_match:
            return max(30, min(2400, int(minute_week_match.group(1))))

        hour_week_match = re.search(r"(\d{1,2})\s*(?:gio|hour|hours)\s*(?:\/|\s*(?:per)?\s*)?(?:tuan|week)", normalized_text)
        if hour_week_match:
            return max(30, min(2400, int(hour_week_match.group(1)) * 60))

        minute_day_match = re.search(r"(\d{1,3})\s*(?:phut|min(?:ute)?s?)\s*(?:moi|per)?\s*(?:ngay|day)", normalized_text)
        if minute_day_match:
            return max(60, min(2400, int(minute_day_match.group(1)) * 7))

        hour_day_match = re.search(r"(\d{1,2})\s*(?:gio|hour|hours)\s*(?:moi|per)?\s*(?:ngay|day)", normalized_text)
        if hour_day_match:
            return max(120, min(2400, int(hour_day_match.group(1)) * 60 * 7))

        return 180

    def _touch_copilot_metadata(
        self,
        *,
        program: LearningProgram,
        conversation_id: str | None,
        workspace_id: str | None,
        latest_message: str,
        assessment_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata = self._program_metadata(program)
        learning_state = self._ensure_learning_state(program)
        current_goal = learning_state.get("current_goal") if isinstance(learning_state.get("current_goal"), dict) else {}
        copilot = metadata.get("copilot")
        if not isinstance(copilot, dict):
            copilot = {}
            metadata["copilot"] = copilot

        assumptions = [
            (
                "PigTex dang gia dinh ban muon hoc bang hoi thoai, it setup, nhung van co roadmap that."
                if program.language == "vi"
                else "PigTex is assuming you want a conversation-first experience with a real roadmap behind it."
            ),
            (
                f"PigTex dang tam xem ban o muc {program.current_level}."
                if program.language == "vi"
                else f"PigTex is currently treating your level as {program.current_level}."
            ),
            (
                f"PigTex dang toi uu cho muc tieu: {current_goal.get('operational_goal') or program.outcome_target or program.goal}"
                if program.language == "vi"
                else f"PigTex is optimizing for the real target: {current_goal.get('operational_goal') or program.outcome_target or program.goal}"
            ),
        ]
        if assessment_result and assessment_result.get("passed"):
            assumptions.append(
                "PigTex vua nang muc tiep theo vi ban da dua ra duoc bang chung hoc tap."
                if program.language == "vi"
                else "PigTex just advanced the focus because you showed enough learning evidence."
            )

        copilot["conversation_id"] = conversation_id
        copilot["workspace_id"] = workspace_id
        copilot["source"] = "chat_autopilot"
        copilot["assumptions"] = assumptions
        copilot["last_user_message"] = latest_message[:800]
        copilot["updated_at"] = _utcnow().isoformat()
        return metadata

    def _build_coach_brief(
        self,
        program: LearningProgram,
        focus_node: LearningProgramNode | None,
    ) -> str:
        learning_state = self._ensure_learning_state(program)
        current_goal = learning_state.get("current_goal") if isinstance(learning_state.get("current_goal"), dict) else {}
        session_memory = learning_state.get("memory", {}).get("session_memory", {}) if isinstance(learning_state.get("memory"), dict) else {}
        current_mode = str(session_memory.get("current_mode") or self._instructional_mode_for_node(focus_node))
        if focus_node is None:
            return (
                "PigTex dang giu nhac hoc tap va se tiep tuc bang review queue."
                if program.language == "vi"
                else "PigTex is holding the learning thread and will continue through the review queue."
            )
        if program.language == "vi":
            return (
                f"PigTex dang dan ban qua '{focus_node.title}' theo mode {current_mode} de tien toi target that: "
                f"{current_goal.get('operational_goal') or program.outcome_target or program.goal}"
            )
        return (
            f"PigTex is guiding you through '{focus_node.title}' in {current_mode} mode on the path to the real target: "
            f"{current_goal.get('operational_goal') or program.outcome_target or program.goal}"
        )

    def _build_next_action(
        self,
        program: LearningProgram,
        focus_node: LearningProgramNode | None,
        assessment_result: dict[str, Any] | None,
    ) -> str | None:
        learning_state = self._ensure_learning_state(program)
        last_turn_output = learning_state.get("last_turn_output")
        if isinstance(last_turn_output, dict) and isinstance(last_turn_output.get("next_step"), str):
            candidate = str(last_turn_output.get("next_step") or "").strip()
            if candidate:
                return candidate
        if assessment_result and isinstance(assessment_result.get("next_action"), str):
            return str(assessment_result.get("next_action")).strip() or None
        if focus_node is None:
            return None
        return focus_node.practice_task

    def _should_grade_chat_turn(
        self,
        message_text: str,
        focus_node: LearningProgramNode,
    ) -> bool:
        normalized = _normalize_text(message_text)
        word_count = len(re.findall(r"\w+", message_text, re.UNICODE))
        if word_count < 24:
            return False
        if normalized.endswith("?") and word_count < 48:
            return False
        self_explaining_markers = (
            "theo em",
            "theo minh",
            "toi hieu",
            "my understanding",
            "i think",
            "in my own words",
            "vi du",
            "for example",
        )
        if any(marker in normalized for marker in self_explaining_markers):
            return True
        return word_count >= (55 if focus_node.stage in {"integration", "capstone"} else 38)

    def _ensure_chat_session(
        self,
        *,
        program: LearningProgram,
        node: LearningProgramNode,
        conversation_id: str | None,
    ) -> LearningSession:
        existing = (
            self.db.query(LearningSession)
            .filter(
                LearningSession.user_id == self.current_user.id,
                LearningSession.program_id == program.id,
                LearningSession.node_id == node.id,
                LearningSession.status == "active",
                LearningSession.conversation_id == conversation_id,
            )
            .order_by(LearningSession.started_at.desc())
            .first()
        )
        if existing is not None:
            return existing

        lesson = self._build_session_packet(program, node)
        session = LearningSession(
            user_id=self.current_user.id,
            program_id=program.id,
            node_id=node.id,
            conversation_id=conversation_id,
            status="active",
            lesson_snapshot_json=_json_dumps(lesson),
        )
        node.mastery_status = "reviewing" if node.mastery_status == "completed" else "ready"
        node.last_practiced_at = _utcnow()
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def _build_chat_teacher_prompt(
        self,
        *,
        program: LearningProgram,
        focus_node: LearningProgramNode | None,
        assumptions: list[str],
        assessment_result: dict[str, Any] | None,
    ) -> str:
        learning_state = self._ensure_learning_state(program)
        current_goal = learning_state.get("current_goal") if isinstance(learning_state.get("current_goal"), dict) else {}
        progress_checklist = learning_state.get("progress_checklist") if isinstance(learning_state.get("progress_checklist"), list) else []
        visible_checklist = self._visible_checklist_slice(progress_checklist, focus_node)
        session_memory = learning_state.get("memory", {}).get("session_memory", {}) if isinstance(learning_state.get("memory"), dict) else {}
        current_mode = str(session_memory.get("current_mode") or self._instructional_mode_for_node(focus_node))
        lines = [
            "You are PigTex Learn operating as a paid 1:1 lecturer, not a generic assistant.",
            "Teach with ownership. Keep one instructional purpose per turn and do not count conversational smoothness as mastery.",
            "Prefer evidence, checklist movement, and next-step clarity over vague encouragement.",
            "When materials are available in the learning state, treat them as preferred grounding sources.",
            "Do not mark anything complete unless the learner has shown performance evidence.",
            f"Real target: {current_goal.get('operational_goal') or program.outcome_target or program.goal}",
            f"Learner level assumption: {program.current_level}",
            f"Instructional mode for this turn: {current_mode}",
        ]
        if focus_node is not None:
            lines.extend([
                f"Current teaching focus: {focus_node.title}",
                f"Focus summary: {focus_node.summary}",
                f"Success criteria: {'; '.join(_json_load_list(focus_node.success_criteria_json)[:3])}",
            ])
        if assumptions:
            lines.append("Current assumptions: " + " | ".join(assumptions[:3]))
        if visible_checklist:
            checklist_parts: list[str] = []
            for item in visible_checklist:
                if not isinstance(item, dict):
                    continue
                checklist_parts.append(f"{item.get('label')}: {item.get('status')}")
            if checklist_parts:
                lines.append("Checklist snapshot: " + " | ".join(checklist_parts))
        if assessment_result:
            strengths = assessment_result.get("strengths") if isinstance(assessment_result.get("strengths"), list) else []
            gaps = assessment_result.get("misconceptions") if isinstance(assessment_result.get("misconceptions"), list) else []
            if strengths:
                lines.append("What the learner already showed: " + " | ".join(str(item) for item in strengths[:2]))
            if gaps:
                lines.append("What still needs work: " + " | ".join(str(item) for item in gaps[:2]))
        lines.extend([
            "Preferred response pattern:",
            "1. Briefly state what the learner currently understands or where the evidence is still weak.",
            "2. Teach one core mental model or scaffold one concrete next move.",
            "3. Keep the response grounded in the current focus and checklist.",
            "4. End with one small action that can generate evidence.",
            "5. Avoid hidden shortcuts, fake completion, and generic praise.",
        ])
        return "\n".join(lines)

    def _normalize_level(self, value: str) -> str:
        normalized = _normalize_text(value, ascii_fold=True)
        if normalized in {"intermediate", "trung cap", "middle"}:
            return "intermediate"
        if normalized in {"advanced", "nang cao"}:
            return "advanced"
        return "beginner"

    def _normalize_style(self, value: str) -> str:
        normalized = _normalize_text(value, ascii_fold=True)
        if normalized in {"practice_first", "practice-first", "practice"}:
            return "practice_first"
        if normalized in {"socratic", "coach"}:
            return "socratic"
        if normalized in {"project_first", "project-first", "project"}:
            return "project_first"
        return "guided"

    def _normalize_language(self, value: str) -> str:
        normalized = _normalize_text(value, ascii_fold=True)
        if normalized.startswith("en"):
            return "en"
        return "vi"

    def _default_program_title(self, topic: str, language: str) -> str:
        return f"Học {topic}" if language == "vi" else f"Learn {topic}"

    def _detect_domain(self, topic: str, goal: str) -> str:
        text = _normalize_text(f"{topic} {goal}", ascii_fold=True)
        ai_markers = ("ai", "ml", "machine learning", "deep learning", "llm", "data science", "prompt", "rag", "agent")
        programming_markers = (
            "python", "javascript", "typescript", "react", "node", "programming", "coding",
            "code", "backend", "frontend", "lap trinh", "sql", "java", "golang", "rust", "c++",
        )
        academic_markers = (
            "math", "toan", "algebra", "calculus", "physics", "vat ly", "chemistry", "hoa hoc", "biology",
            "sinh hoc", "history", "lich su", "economics", "statistics", "literature", "exam", "on thi",
        )
        if any(marker in text for marker in ai_markers):
            return "ai"
        if any(marker in text for marker in programming_markers):
            return "programming"
        if any(marker in text for marker in academic_markers):
            return "academic"
        return "general"

    def _build_program_metadata(
        self,
        *,
        weekly_minutes: int,
        current_level: str,
        domain: str,
        language: str,
        target_date: datetime | None,
    ) -> dict[str, Any]:
        sessions_per_week = max(1, round(weekly_minutes / 45))
        if language == "vi":
            pace_label = f"{sessions_per_week} buổi/tuần, khoảng {round(weekly_minutes / sessions_per_week)} phút mỗi buổi"
        else:
            pace_label = f"{sessions_per_week} sessions/week, about {round(weekly_minutes / sessions_per_week)} minutes each"
        return {
            "pace_label": pace_label,
            "sessions_per_week": sessions_per_week,
            "current_level": current_level,
            "domain": domain,
            "target_date": target_date.isoformat() if target_date else None,
        }

    def _build_program_blueprints(
        self,
        *,
        topic: str,
        goal: str,
        outcome_target: str | None,
        domain: str,
        current_level: str,
        learning_style: str,
        language: str,
    ) -> list[NodeBlueprint]:
        if domain == "ai":
            return self._build_ai_blueprints(topic, outcome_target, learning_style, language)
        if domain == "programming":
            return self._build_programming_blueprints(topic, outcome_target, learning_style, language)
        if domain == "academic":
            return self._build_academic_blueprints(topic, outcome_target, learning_style, language)
        return self._build_general_blueprints(topic, outcome_target, learning_style, language)

    def _coach_voice(self, style: str, language: str) -> str:
        if language == "vi":
            mapping = {
                "guided": "Đi theo từng bước nhỏ, luôn nói rõ vì sao phải học bước này trước.",
                "practice_first": "Đưa người học vào bài tập sớm, rồi quay lại giải nghĩa sau khi họ va vào khó khăn.",
                "socratic": "Ưu tiên câu hỏi gợi mở, buộc người học tự diễn giải thay vì chỉ đọc đáp án.",
                "project_first": "Luôn neo kiến thức vào một đầu ra cụ thể và nhắc người học liên hệ với sản phẩm cuối.",
            }
            return mapping.get(style, mapping["guided"])
        mapping_en = {
            "guided": "Move step by step and explain why this step comes before the next one.",
            "practice_first": "Push the learner into a small task early, then explain after they hit friction.",
            "socratic": "Prefer guiding questions that force the learner to restate the idea in their own words.",
            "project_first": "Anchor every concept to a concrete output and keep pulling back to the final artifact.",
        }
        return mapping_en.get(style, mapping_en["guided"])

    def _build_ai_blueprints(
        self,
        topic: str,
        outcome_target: str | None,
        learning_style: str,
        language: str,
    ) -> list[NodeBlueprint]:
        if language == "vi":
            final_target = outcome_target or f"tự giải thích và xây được một mini project về {topic}"
            return [
                NodeBlueprint(
                    node_key="ai-map",
                    stage="orientation",
                    title=f"Bản đồ {topic}",
                    summary=f"Dựng bản đồ khái niệm để phân biệt AI, ML, Deep Learning và nơi {topic} đứng trong bức tranh lớn.",
                    explanation=(
                        "Node này dùng để xóa mù khái niệm. Bạn cần tách rõ AI là chiếc ô lớn, Machine Learning là cách để máy học từ dữ liệu, "
                        "Deep Learning là một nhánh dùng mạng nơ-ron sâu, còn chatbot hay recommendation chỉ là ứng dụng cuối. "
                        f"{self._coach_voice(learning_style, language)}"
                    ),
                    worked_example="Một hệ thống lọc spam email là AI system. Nếu nó học từ email đã gắn nhãn thì đó là ML. Nếu nó dùng neural network sâu thì đó là Deep Learning.",
                    practice_task=f"Viết 5-7 câu giải thích sự khác nhau giữa AI, ML, Deep Learning và liên hệ chúng với mục tiêu học {topic} của bạn.",
                    reflection_prompt="Khái niệm nào trước đây bạn hay gom chung làm một, và bây giờ bạn tách chúng ra như thế nào?",
                    estimated_minutes=35,
                    difficulty=1,
                    common_pitfalls=["Đồng nhất AI với chatbot", "Nghĩ rằng mọi AI đều là Deep Learning", "Không phân biệt hệ thống, mô hình và ứng dụng"],
                    expected_keywords=["ai", "machine learning", "deep learning", "ứng dụng", "dữ liệu"],
                    success_criteria=["Tự vẽ lại được sơ đồ AI -> ML -> DL", "Giải thích được 1 ví dụ thực tế mà không lẫn khái niệm"],
                    resources=["Tự tạo sơ đồ khái niệm 1 trang", "Ghi lại 3 ví dụ AI bạn dùng hàng ngày"],
                ),
                NodeBlueprint(
                    node_key="data-problem",
                    stage="foundation",
                    title="Bài toán, dữ liệu và nhãn",
                    summary="Hiểu mọi pipeline AI bắt đầu từ bài toán và dữ liệu, không bắt đầu từ model.",
                    explanation="Trước khi nói về model, bạn phải biết bài toán là dự đoán gì, đầu vào là gì, đầu ra là gì, dữ liệu lấy ở đâu và nhãn được tạo ra như thế nào.",
                    worked_example="Ví dụ churn prediction: input là lịch sử dùng dịch vụ, output là churn / not churn, nhãn đến từ việc khách có rời đi trong khoảng thời gian tiếp theo hay không.",
                    practice_task="Chọn một bài toán AI đơn giản và mô tả rõ input, output, nguồn dữ liệu, cách có nhãn, và 2 rủi ro dữ liệu.",
                    reflection_prompt="Bạn có đang nghĩ về model quá sớm trước khi mô tả xong bài toán không?",
                    estimated_minutes=40,
                    difficulty=2,
                    common_pitfalls=["Chọn model trước khi hiểu dữ liệu", "Nhầm feature với label", "Không nghĩ tới bias và missing data"],
                    expected_keywords=["input", "output", "dữ liệu", "nhãn", "feature"],
                    success_criteria=["Mô tả được bài toán theo format input-output-label", "Chỉ ra ít nhất 2 lỗi dữ liệu có thể làm hỏng kết quả"],
                    resources=["Bảng 4 cột: bài toán / input / output / label"],
                ),
                NodeBlueprint(
                    node_key="training-evaluation",
                    stage="core",
                    title="Huấn luyện, validation và đánh giá",
                    summary="Nắm vòng đời train/validate/test và chọn metric phù hợp với mục tiêu thật.",
                    explanation="Một mô hình không tốt chỉ vì accuracy cao. Bạn cần hiểu khác nhau giữa train set, validation set, test set; hiểu overfitting; và chọn metric theo mục tiêu.",
                    worked_example="Bài toán fraud detection có thể đạt accuracy cao nhưng vẫn vô dụng nếu recall với giao dịch gian lận quá thấp.",
                    practice_task="Giải thích train/validation/test bằng ví dụ đời thường, rồi chọn 2 metric phù hợp cho một bài toán AI bạn quan tâm.",
                    reflection_prompt="Nếu mô hình rất giỏi trên train nhưng kém trên dữ liệu mới, bạn sẽ nghi ngờ điều gì đầu tiên?",
                    estimated_minutes=45,
                    difficulty=2,
                    common_pitfalls=["Dùng test set nhiều lần để chỉnh model", "Chỉ nhìn accuracy", "Không gắn metric với tác động thực tế"],
                    expected_keywords=["train", "validation", "test", "overfitting", "metric"],
                    success_criteria=["Giải thích được vai trò của từng tập dữ liệu", "Chọn metric hợp lý và nêu lý do"],
                    resources=["Bảng so sánh accuracy / precision / recall / F1"],
                ),
                NodeBlueprint(
                    node_key="modern-ai-systems",
                    stage="applied",
                    title="Từ model đến hệ thống AI hiện đại",
                    summary="Ghép mô hình vào hệ thống thực tế: prompt, retrieval, tools, guardrails và feedback loop.",
                    explanation="Ngày nay giá trị không chỉ nằm ở model. Một sản phẩm AI tốt thường là tổ hợp của model + prompt + memory + retrieval + tool use + guardrails + observability.",
                    worked_example="Ví dụ trợ lý CSKH: model trả lời, retrieval kéo policy, guardrail chặn bịa thông tin hoàn tiền, analytics theo dõi lỗi.",
                    practice_task=f"Mô tả một hệ thống {topic} đơn giản gồm model, nguồn tri thức, guardrail và cách nhận feedback từ người dùng.",
                    reflection_prompt="Nếu chỉ đổi model mà hệ thống vẫn tệ, bạn sẽ kiểm tra lớp nào tiếp theo?",
                    estimated_minutes=45,
                    difficulty=3,
                    common_pitfalls=["Tin rằng model càng mạnh là đủ", "Không tách model layer và product layer", "Không có vòng phản hồi để sửa lỗi"],
                    expected_keywords=["prompt", "retrieval", "tool", "guardrail", "feedback"],
                    success_criteria=["Phân rã được một hệ thống AI thành các lớp chính", "Giải thích vai trò của ít nhất 3 lớp ngoài model"],
                    resources=["Sơ đồ kiến trúc 1 trang cho một AI app"],
                ),
                NodeBlueprint(
                    node_key="mini-project",
                    stage="integration",
                    title="Mini project có đầu ra thật",
                    summary=f"Biến phần đã học thành một đầu ra thật để tiến gần mục tiêu {final_target}.",
                    explanation="Ưu tiên làm ra sản phẩm nhỏ nhưng hoàn chỉnh: mục tiêu, input-output, pipeline, cách đánh giá và demo.",
                    worked_example="Ví dụ mini project: chatbot hỏi đáp tài liệu nội bộ với prompt hệ thống, trích dẫn nguồn và checklist đánh giá câu trả lời.",
                    practice_task=f"Viết proposal mini project cho {topic}: mục tiêu, input-output, thành phần hệ thống, cách demo và cách đo chất lượng.",
                    reflection_prompt="Phần nào trong proposal của bạn vẫn đang mơ hồ hoặc không đo được?",
                    estimated_minutes=50,
                    difficulty=3,
                    common_pitfalls=["Project quá to", "Không có tiêu chí đánh giá", "Thiếu ranh giới phạm vi v1"],
                    expected_keywords=["mục tiêu", "pipeline", "demo", "đánh giá", "phạm vi"],
                    success_criteria=["Có proposal rõ ràng cho v1", "Có cách chứng minh project hoạt động hoặc không hoạt động"],
                    resources=["Template proposal 1 trang", "Checklist demo v1"],
                ),
                NodeBlueprint(
                    node_key="capstone-review",
                    stage="capstone",
                    title="Teach-back và củng cố",
                    summary="Khóa lại lỗ hổng bằng teach-back, tự kiểm tra và kế hoạch học tiếp.",
                    explanation="Bạn chỉ thật sự hiểu khi tự giải thích được, so sánh được, và áp dụng được. Node cuối dùng để rà lại misconception và khóa outcome.",
                    worked_example="Một câu teach-back tốt không chỉ nhắc lại định nghĩa, mà còn nói khi nào nên dùng, khi nào không nên dùng, và vì sao.",
                    practice_task=f"Viết một đoạn teach-back 150-250 từ giải thích {topic} cho người mới, rồi nêu 3 bước tiếp theo để đạt mục tiêu {final_target}.",
                    reflection_prompt="Nếu phải dạy lại chủ đề này cho người mới trong 10 phút, phần nào bạn còn thiếu tự tin nhất?",
                    estimated_minutes=35,
                    difficulty=2,
                    common_pitfalls=["Nhớ từ khóa nhưng không giải thích được", "Không liên hệ được với đầu ra thật", "Không có kế hoạch ôn lại"],
                    expected_keywords=["giải thích", "ví dụ", "ứng dụng", "giới hạn", "bước tiếp theo"],
                    success_criteria=["Teach-back mạch lạc", "Nêu được bước học tiếp theo dựa trên khoảng trống hiện tại"],
                    resources=["Flashcard misconception", "Checklist ôn 1-3-7-14 ngày"],
                ),
            ]

        final_target_en = outcome_target or f"explain {topic} clearly and ship a small project"
        return [
            NodeBlueprint(
                node_key="ai-map",
                stage="orientation",
                title=f"The map of {topic}",
                summary=f"Separate AI, ML, Deep Learning, and where {topic} fits in the bigger picture.",
                explanation=f"This node removes category confusion first. {self._coach_voice(learning_style, language)}",
                worked_example="A spam filter is an AI system. If it learns from labeled spam/not-spam data, it is Machine Learning. If it uses deep neural nets, it is Deep Learning.",
                practice_task=f"Write 5-7 sentences distinguishing AI, ML, Deep Learning, and connect them to your goal of learning {topic}.",
                reflection_prompt="Which ideas were you collapsing together before, and how do you separate them now?",
                estimated_minutes=35,
                difficulty=1,
                common_pitfalls=["Equating AI with chatbots", "Assuming all AI is deep learning", "Mixing up system, model, and application"],
                expected_keywords=["ai", "machine learning", "deep learning", "application", "data"],
                success_criteria=["Redraw the AI -> ML -> DL hierarchy", "Explain one real example without mixing categories"],
                resources=["One-page concept map", "Three AI examples from your daily life"],
            ),
            NodeBlueprint(
                node_key="data-problem",
                stage="foundation",
                title="Problem framing, data, and labels",
                summary="Learn why every useful AI workflow starts with problem framing and data.",
                explanation="Before models, define the task, the input, the output, the source of data, and how labels are created.",
                worked_example="Predicting churn: inputs are usage signals; output is churn or not churn; labels come from actual cancellations.",
                practice_task="Choose one AI use case and describe the input, output, data source, label strategy, and two data risks.",
                reflection_prompt="Are you thinking about model choice too early?",
                estimated_minutes=40,
                difficulty=2,
                common_pitfalls=["Picking a model before understanding data", "Mixing features and labels", "Ignoring bias and missing data"],
                expected_keywords=["input", "output", "data", "label", "feature"],
                success_criteria=["Describe a task with clear input-output-label structure", "Name at least two data risks"],
                resources=["Task framing table"],
            ),
            NodeBlueprint(
                node_key="training-evaluation",
                stage="core",
                title="Training, validation, and evaluation",
                summary="Understand train/validation/test and pick metrics that match real outcomes.",
                explanation="High accuracy alone does not guarantee usefulness. Learn validation logic, overfitting, and task-appropriate metrics.",
                worked_example="Fraud detection can show high accuracy but still fail if recall on fraud is poor.",
                practice_task="Explain train/validation/test with a real-world analogy, then pick two metrics for an AI problem you care about.",
                reflection_prompt="If a model does well on train data but poorly on new data, what would you suspect first?",
                estimated_minutes=45,
                difficulty=2,
                common_pitfalls=["Reusing test data for tuning", "Looking only at accuracy", "Ignoring business impact of metrics"],
                expected_keywords=["train", "validation", "test", "overfitting", "metric"],
                success_criteria=["Explain the role of each dataset split", "Pick sensible metrics with justification"],
                resources=["Metric comparison table"],
            ),
            NodeBlueprint(
                node_key="modern-ai-systems",
                stage="applied",
                title="From model to modern AI system",
                summary="Connect models to prompts, retrieval, tools, guardrails, and feedback loops.",
                explanation="Useful AI products are usually systems, not isolated models.",
                worked_example="A support assistant uses a model for generation, retrieval for company policies, and guardrails to avoid hallucinated rules.",
                practice_task=f"Sketch a simple {topic} system with a model, a knowledge source, a guardrail, and a feedback loop.",
                reflection_prompt="If changing the model is not enough, which layer do you inspect next?",
                estimated_minutes=45,
                difficulty=3,
                common_pitfalls=["Assuming a stronger model solves everything", "Not separating model and product layers", "Missing a feedback loop"],
                expected_keywords=["prompt", "retrieval", "tool", "guardrail", "feedback"],
                success_criteria=["Break a system into its major layers", "Explain why at least three non-model layers matter"],
                resources=["One-page architecture sketch"],
            ),
            NodeBlueprint(
                node_key="mini-project",
                stage="integration",
                title="Mini project with a real output",
                summary=f"Turn the theory into a small but real artifact that moves you toward {final_target_en}.",
                explanation="The goal is a small, defensible v1 with a clear scope and evaluation plan.",
                worked_example="A small internal Q&A assistant with citations over 10 PDFs.",
                practice_task=f"Write a mini project proposal for {topic}: goal, inputs/outputs, system parts, demo plan, and quality checks.",
                reflection_prompt="Which part of your proposal is still vague or untestable?",
                estimated_minutes=50,
                difficulty=3,
                common_pitfalls=["Project too large", "No evaluation criteria", "Vague scope"],
                expected_keywords=["goal", "pipeline", "demo", "evaluation", "scope"],
                success_criteria=["Produce a credible v1 proposal", "Define how success and failure will be observed"],
                resources=["One-page proposal template"],
            ),
            NodeBlueprint(
                node_key="capstone-review",
                stage="capstone",
                title="Teach-back and reinforcement",
                summary="Lock in the gains with teach-back, self-checks, and a next-step plan.",
                explanation="You understand the topic when you can restate, compare, and apply it.",
                worked_example="A strong teach-back explains not only what something is, but when and why it should be used.",
                practice_task=f"Write a 150-250 word teach-back explaining {topic} to a beginner, then name the next three steps toward {final_target_en}.",
                reflection_prompt="If you had to teach this in 10 minutes, which part would still feel shaky?",
                estimated_minutes=35,
                difficulty=2,
                common_pitfalls=["Remembering terms without explanation", "No link to a concrete outcome", "No spaced review plan"],
                expected_keywords=["explain", "example", "application", "limits", "next steps"],
                success_criteria=["Produce a coherent teach-back", "Define the next steps based on current gaps"],
                resources=["Misconception flashcards", "1-3-7-14 review checklist"],
            ),
        ]

    def _programming_focus_label(self, topic: str) -> str:
        text = _normalize_text(topic)
        if "python" in text:
            return "Python"
        if "javascript" in text or "typescript" in text:
            return "JavaScript / TypeScript"
        if "react" in text or "frontend" in text:
            return "frontend"
        if "backend" in text or "api" in text:
            return "backend"
        if "sql" in text or "database" in text:
            return "data and databases"
        return topic.strip() or "programming"

    def _build_programming_blueprints(
        self,
        topic: str,
        outcome_target: str | None,
        learning_style: str,
        language: str,
    ) -> list[NodeBlueprint]:
        focus = self._programming_focus_label(topic)
        final_target = outcome_target or (
            f"xay duoc mot mini project ve {topic}" if language == "vi"
            else f"ship a small working project in {topic}"
        )
        if language == "vi":
            return [
                NodeBlueprint(
                    node_key="prog-map",
                    stage="orientation",
                    title=f"Ban do {topic}",
                    summary=f"Xac dinh {topic} la gi, dung de giai quyet bai toan nao, va vi sao {focus} la dung diem bat dau.",
                    explanation=f"Node nay tao mental model truoc khi hoc cu phap. {self._coach_voice(learning_style, language)}",
                    worked_example="Mot app nho luon co input, logic xu ly, output va cach kiem tra ket qua.",
                    practice_task=f"Mo ta trong 5-7 cau: {topic} duoc dung de lam gi, input-output cua no la gi, va ban muon tao ra dieu gi.",
                    reflection_prompt="Ban dang hoc mot ngon ngu, mot framework, hay mot cach giai quyet van de?",
                    estimated_minutes=30,
                    difficulty=1,
                    common_pitfalls=["Hoc syntax truoc khi biet minh dang giai bai toan nao", "Nho lenh nhung khong hieu data flow"],
                    expected_keywords=["input", "output", "logic", "problem", "project"],
                    success_criteria=["Mo ta duoc mot bai toan ma code se giai quyet", "Giai thich duoc vi sao chon huong hoc nay"],
                    resources=["One-page note: problem -> input -> logic -> output"],
                ),
                NodeBlueprint(
                    node_key="prog-core",
                    stage="foundation",
                    title="Bien, dieu kien va lap lai",
                    summary="Nang luc cot loi cua lap trinh la mo ta trang thai va dieu khien luong chay.",
                    explanation="Neu ban khong vung bien, dieu kien va loop, moi bai toan ve sau se rat mo ho.",
                    worked_example="Nhap diem, kiem tra nguong dat, sau do lap qua danh sach de tinh trung binh.",
                    practice_task=f"Viet bang loi mo ta cach dung bien, if/else va loop de xu ly mot bai toan nho trong {topic}.",
                    reflection_prompt="Phan nao dang kho hon voi ban: luu gia tri, ra quyet dinh, hay lap lai?",
                    estimated_minutes=40,
                    difficulty=1,
                    common_pitfalls=["Loi off-by-one", "Khong cap nhat bien dung luc", "Nhieu lenh nhung khong co thu tu ro rang"],
                    expected_keywords=["variable", "condition", "loop", "state", "flow"],
                    success_criteria=["Mo ta dung vai tro cua bien, if/else, loop", "Tao duoc mot thu tu xu ly ro rang"],
                    resources=["Trace table cho tung buoc chay"],
                ),
                NodeBlueprint(
                    node_key="prog-functions",
                    stage="core",
                    title="Ham, module va tai su dung",
                    summary="Chia nho bai toan thanh cac khoi co ten ro rang de doc, sua va test de hon.",
                    explanation="Ham la cach bien y tuong lon thanh cac don vi nho co dau vao va dau ra ro rang.",
                    worked_example="Tach app thanh ham parse_input, validate_data, calculate_result va render_output.",
                    practice_task=f"Chia mot bai toan {topic} thanh 3-5 ham. Ghi ro moi ham nhan gi va tra ve gi.",
                    reflection_prompt="Ham nao trong bai cua ban dang lam qua nhieu viec?",
                    estimated_minutes=45,
                    difficulty=2,
                    common_pitfalls=["Ham qua dai", "Ten ham mo ho", "Khong ro input-output"],
                    expected_keywords=["function", "input", "output", "reuse", "module"],
                    success_criteria=["Tach duoc bai toan thanh cac ham hop ly", "Giai thich vai tro cua tung ham"],
                    resources=["Function design checklist"],
                ),
                NodeBlueprint(
                    node_key="prog-debug",
                    stage="applied",
                    title="Debug va test co he thong",
                    summary="Khong chi viet code, ban can biet tim loi, dat gia thuyet va xac minh.",
                    explanation="Nguoi hoc tien bo nhanh khi debug co quy trinh: tai tao loi, quan sat, khoanh vung, sua, test lai.",
                    worked_example="Khi ham tra ket qua sai, truoc tien in ra input trung gian va kiem tra gia tri qua tung buoc.",
                    practice_task="Viet quy trinh 5 buoc de debug mot loi logic, va dua ra 2 test case de bat loi do.",
                    reflection_prompt="Ban thuong sua theo cam tinh hay theo gia thuyet co kiem tra?",
                    estimated_minutes=40,
                    difficulty=2,
                    common_pitfalls=["Sua nhieu cho cung luc", "Khong co test case truoc va sau khi sua", "Doc loi nhung khong khoanh vung du lieu gay loi"],
                    expected_keywords=["bug", "test", "input", "expected", "actual"],
                    success_criteria=["Mo ta duoc quy trinh debug", "Tao duoc test case expected vs actual"],
                    resources=["Bug log template", "Expected vs actual checklist"],
                ),
                NodeBlueprint(
                    node_key="prog-project",
                    stage="integration",
                    title="Mini project",
                    summary=f"Chuyen kien thuc thanh mot dau ra that de tien toi {final_target}.",
                    explanation="Node nay ep ban dong goi mot bai toan nho thanh san pham co pham vi, luong chay va cach demo ro rang.",
                    worked_example="CLI todo app, trang note app nho, hoac API mini co mot duong dan va mot bai test.",
                    practice_task=f"Viet proposal mini project cho {topic}: muc tieu, input-output, chuc nang v1, cach demo, cach test.",
                    reflection_prompt="Pham vi v1 cua ban da nho du chua?",
                    estimated_minutes=50,
                    difficulty=3,
                    common_pitfalls=["Project qua to", "Khong co tieu chi done", "Khong co ke hoach demo"],
                    expected_keywords=["scope", "feature", "input", "output", "test"],
                    success_criteria=["Co de bai ro rang cho v1", "Co cach demo va test duoc"],
                    resources=["One-page project brief"],
                ),
                NodeBlueprint(
                    node_key="prog-teachback",
                    stage="capstone",
                    title="Teach-back va next steps",
                    summary="Tu giai thich lai cach ban xay he thong va xac dinh buoc hoc tiep theo.",
                    explanation="Neu ban giai thich duoc luong chay, diem de vo, va cach mo rong thi ban da hieu hon muc nho syntax.",
                    worked_example="Mot teach-back tot se noi duoc vi sao tach ham, vi sao test o dau, va khi nao can refactor.",
                    practice_task=f"Viet 150-250 tu giai thich cach ban se xay mot san pham {topic}, roi neu 3 buoc tiep theo de dat {final_target}.",
                    reflection_prompt="Neu nguoi moi hoi tai sao code cua ban duoc to chuc nhu vay, ban tra loi the nao?",
                    estimated_minutes=35,
                    difficulty=2,
                    common_pitfalls=["Mo ta theo tung lenh ma khong co cau truc", "Khong lien he voi bai toan that"],
                    expected_keywords=["explain", "design", "test", "tradeoff", "next"],
                    success_criteria=["Teach-back mach lac", "Xac dinh duoc next steps dua tren lo hong hien tai"],
                    resources=["Reflection checklist 1-3-7-14"],
                ),
            ]

        return [
            NodeBlueprint(
                node_key="prog-map",
                stage="orientation",
                title=f"The map of {topic}",
                summary=f"Frame what {topic} is for, which problems it solves, and why {focus} is the right lens.",
                explanation=f"Build the problem map before memorizing syntax. {self._coach_voice(learning_style, language)}",
                worked_example="A tiny app always has inputs, logic, outputs, and a way to verify results.",
                practice_task=f"Write 5-7 sentences explaining what {topic} is used for, what the input/output looks like, and what you want to build.",
                reflection_prompt="Are you learning a language, a framework, or a way to solve problems?",
                estimated_minutes=30,
                difficulty=1,
                common_pitfalls=["Learning syntax before the problem", "Remembering commands without data flow"],
                expected_keywords=["input", "output", "logic", "problem", "project"],
                success_criteria=["Describe one problem code will solve", "Explain why this learning direction fits"],
                resources=["Problem -> input -> logic -> output note"],
            ),
            NodeBlueprint(
                node_key="prog-core",
                stage="foundation",
                title="Variables, conditions, and loops",
                summary="Programming starts with state, branching, and repetition.",
                explanation="Without variables, conditions, and loops, later concepts remain fuzzy.",
                worked_example="Read scores, branch on pass/fail, then loop to compute an average.",
                practice_task=f"Explain how variables, if/else, and loops would solve one small {topic} task.",
                reflection_prompt="Which part is harder right now: storing values, branching, or repetition?",
                estimated_minutes=40,
                difficulty=1,
                common_pitfalls=["Off-by-one errors", "Updating state at the wrong time", "No clear execution order"],
                expected_keywords=["variable", "condition", "loop", "state", "flow"],
                success_criteria=["Explain the role of variables, branching, and loops", "Create a clear execution order"],
                resources=["Execution trace table"],
            ),
            NodeBlueprint(
                node_key="prog-functions",
                stage="core",
                title="Functions, modules, and reuse",
                summary="Break large problems into named pieces with clear inputs and outputs.",
                explanation="Functions turn a large fuzzy task into smaller reliable units.",
                worked_example="Split an app into parse_input, validate_data, calculate_result, and render_output.",
                practice_task=f"Break one {topic} problem into 3-5 functions and describe each input and output.",
                reflection_prompt="Which function in your design is doing too many jobs?",
                estimated_minutes=45,
                difficulty=2,
                common_pitfalls=["Functions that are too large", "Vague names", "Unclear inputs and outputs"],
                expected_keywords=["function", "input", "output", "reuse", "module"],
                success_criteria=["Decompose a task into sensible functions", "Explain the role of each function"],
                resources=["Function design checklist"],
            ),
            NodeBlueprint(
                node_key="prog-debug",
                stage="applied",
                title="Debugging and tests",
                summary="Progress accelerates when you can reproduce errors, isolate them, and verify fixes.",
                explanation="A strong debugging loop is: reproduce, observe, narrow the cause, fix, and retest.",
                worked_example="When output is wrong, log or inspect intermediate values before changing multiple lines.",
                practice_task="Write a five-step debugging process and define two test cases for one logic bug.",
                reflection_prompt="Do you usually patch by intuition or by a tested hypothesis?",
                estimated_minutes=40,
                difficulty=2,
                common_pitfalls=["Changing too many things at once", "No before/after test", "Ignoring the actual failing input"],
                expected_keywords=["bug", "test", "input", "expected", "actual"],
                success_criteria=["Describe a debugging workflow", "Write expected vs actual tests"],
                resources=["Bug log template"],
            ),
            NodeBlueprint(
                node_key="prog-project",
                stage="integration",
                title="Mini project",
                summary=f"Turn the concepts into a real artifact that moves you toward {final_target}.",
                explanation="This node forces scope control, implementation order, and a demo plan.",
                worked_example="A tiny todo CLI, a small note app, or a mini API with one route and one test.",
                practice_task=f"Write a mini project proposal for {topic}: goal, inputs/outputs, v1 features, demo plan, and tests.",
                reflection_prompt="Is your v1 scope actually small enough to finish?",
                estimated_minutes=50,
                difficulty=3,
                common_pitfalls=["Project too large", "No done criteria", "No demo plan"],
                expected_keywords=["scope", "feature", "input", "output", "test"],
                success_criteria=["Produce a credible v1 brief", "Define how the demo and tests will work"],
                resources=["One-page project brief"],
            ),
            NodeBlueprint(
                node_key="prog-teachback",
                stage="capstone",
                title="Teach-back and next steps",
                summary="Explain how your solution works and identify the next gaps to close.",
                explanation="Understanding shows up when you can explain the flow, the failure modes, and the tradeoffs.",
                worked_example="A good teach-back covers structure, testing, and why the code was organized that way.",
                practice_task=f"Write 150-250 words explaining how you would build a small {topic} product, then name the next three steps toward {final_target}.",
                reflection_prompt="If a beginner asked why your code is structured this way, what would you say?",
                estimated_minutes=35,
                difficulty=2,
                common_pitfalls=["Explaining line by line without structure", "No link back to a real problem"],
                expected_keywords=["explain", "design", "test", "tradeoff", "next"],
                success_criteria=["Produce a coherent teach-back", "Name next steps based on current gaps"],
                resources=["1-3-7-14 review checklist"],
            ),
        ]

    def _build_academic_blueprints(
        self,
        topic: str,
        outcome_target: str | None,
        learning_style: str,
        language: str,
    ) -> list[NodeBlueprint]:
        final_target = outcome_target or (
            f"giai thich va giai duoc bai tap ve {topic}" if language == "vi"
            else f"explain and solve core problems in {topic}"
        )
        if language == "vi":
            return [
                NodeBlueprint(
                    node_key="acad-map",
                    stage="orientation",
                    title=f"Ban do mon {topic}",
                    summary=f"Xac dinh pham vi, chu de lon, va tieu chi thanh cong khi hoc {topic}.",
                    explanation=f"Neu khong co ban do chu de, nguoi hoc rat de hoc lan man. {self._coach_voice(learning_style, language)}",
                    worked_example="Mot mon hoc tot thuong co: khai niem cot loi, cong cu, dang bai, cach kiem tra.",
                    practice_task=f"Liet ke 4-6 chu de lon cua {topic}, muc tieu cua ban, va dau hieu nao cho thay ban dang tien bo that.",
                    reflection_prompt="Ban dang hoc de qua mon, de thi, hay de hieu ban chat?",
                    estimated_minutes=30,
                    difficulty=1,
                    common_pitfalls=["Hoc khong co pham vi", "Khong biet dang bai se gap"],
                    expected_keywords=["concept", "topic", "problem", "goal", "review"],
                    success_criteria=["Vach duoc pham vi hoc tap", "Noi duoc muc tieu hoc that su"],
                    resources=["Subject map note"],
                ),
                NodeBlueprint(
                    node_key="acad-language",
                    stage="foundation",
                    title="Tu vung, ky hieu va quy tac cot loi",
                    summary="Hoc mon hoc qua ngon ngu cua mon do: ten goi, ky hieu, quy tac va dinh nghia.",
                    explanation="Day la lop de tranh hoc thuoc vet: moi ky hieu phai gan voi y nghia va cach dung.",
                    worked_example="Trong toan, ky hieu khong chi la hinh thuc; no cho biet quan he giua cac dai luong.",
                    practice_task=f"Chon 5-8 thuat ngu hoac ky hieu quan trong trong {topic}, roi giai thich y nghia va khi nao dung chung.",
                    reflection_prompt="Ky hieu nao ban hay thay nhung chua that su hieu?",
                    estimated_minutes=40,
                    difficulty=1,
                    common_pitfalls=["Nho dinh nghia ma khong biet ap dung", "Nhieu ky hieu nhung khong co vi du"],
                    expected_keywords=["definition", "symbol", "rule", "meaning", "example"],
                    success_criteria=["Giai thich duoc cac thuat ngu cot loi", "Gan duoc quy tac voi vi du"],
                    resources=["Glossary card set"],
                ),
                NodeBlueprint(
                    node_key="acad-worked-example",
                    stage="core",
                    title="Worked example",
                    summary="Di qua mot bai giai mau de nhin thay cach suy nghi thay vi chi thay dap an.",
                    explanation="Nguoi hoc tien bo nhanh khi hoc tu quy trinh: nhan dang dang bai, chon cong cu, giai, kiem tra.",
                    worked_example="Mot bai giai tot khong nhay coc. No noi ro vi sao dung buoc nay, gia dinh nao dang duoc dung.",
                    practice_task=f"Chon mot bai trong {topic}. Giai thich tung buoc: bai toan cho gi, can tim gi, cong cu nao duoc dung, va vi sao.",
                    reflection_prompt="Ban thuong bi mat dau o cho nhan dang dang bai hay chon cong cu?",
                    estimated_minutes=45,
                    difficulty=2,
                    common_pitfalls=["Nhay coc sang dap an", "Chep cach giai ma khong hieu vi sao"],
                    expected_keywords=["step", "method", "reason", "result", "check"],
                    success_criteria=["Mo ta duoc cac buoc giai va ly do", "Co buoc kiem tra ket qua"],
                    resources=["Worked-example template"],
                ),
                NodeBlueprint(
                    node_key="acad-practice",
                    stage="applied",
                    title="Luyen tap co chu dich",
                    summary="Chon bai tap dung do kho va phan tich loi de dong lo hong.",
                    explanation="Luyen tap co gia tri khi ban biet dang bai, muc dich bai, loi mac phai, va cach sua.",
                    worked_example="Lam 3 bai cung dang, sau do doi 1 bai transfer de xem minh co thuc su hieu khong.",
                    practice_task=f"Tao ke hoach luyen tap cho {topic}: 2 bai co ban, 1 bai trung binh, 1 bai transfer, va cach review loi.",
                    reflection_prompt="Loi cua ban thuong do thieu kien thuc hay thieu quy trinh?",
                    estimated_minutes=45,
                    difficulty=2,
                    common_pitfalls=["Lam nhieu bai giong nhau", "Khong ghi nhan loi lap lai"],
                    expected_keywords=["practice", "mistake", "review", "transfer", "difficulty"],
                    success_criteria=["Co ke hoach luyen tap ro rang", "Co cach ghi lai va sua loi"],
                    resources=["Mistake log"],
                ),
                NodeBlueprint(
                    node_key="acad-transfer",
                    stage="integration",
                    title="Ap dung va transfer",
                    summary=f"Kiem tra xem ban co the dem y tuong cua {topic} sang bai moi de tien toi {final_target}.",
                    explanation="Node nay tach biet viec nho cach lam voi viec that su hieu.",
                    worked_example="Neu doi so lieu, doi ngu canh ma ban van chon duoc huong giai hop ly, do la transfer.",
                    practice_task=f"Giai thich cach ban se xu ly mot bai moi trong {topic} ma khong giong het bai mau. Neu ro gia thuyet, huong giai va cach kiem tra.",
                    reflection_prompt="Khi bai toan doi ngu canh, ban co mat luon huong giai khong?",
                    estimated_minutes=50,
                    difficulty=3,
                    common_pitfalls=["Chi lam duoc bai da quen", "Mat huong khi doi du lieu hoac ngu canh"],
                    expected_keywords=["apply", "transfer", "method", "assumption", "check"],
                    success_criteria=["Neu duoc cach tiep can bai moi", "Biet cach tu kiem tra huong giai"],
                    resources=["Transfer checklist"],
                ),
                NodeBlueprint(
                    node_key="acad-review",
                    stage="capstone",
                    title="Tong ket va ke hoach on",
                    summary="Khoa lai phan da hoc bang teach-back, on cach quang, va buoc hoc tiep theo.",
                    explanation="Muc tieu cuoi khong phai la cam giac quen, ma la giai thich duoc va lam duoc.",
                    worked_example="Mot tong ket tot chi ra khai niem then chot, dang bai thuong gap, va loi de gap nhat.",
                    practice_task=f"Viet 150-250 tu tong ket {topic}, neu 3 dang bai quan trong, 3 loi de mac, va lich on de dat {final_target}.",
                    reflection_prompt="Neu phai day lai cho ban hoc, phan nao ban chua tu tin nhat?",
                    estimated_minutes=35,
                    difficulty=2,
                    common_pitfalls=["Nho y chinh nhung khong lam duoc bai", "Khong co ke hoach on"],
                    expected_keywords=["summary", "example", "mistake", "review", "next"],
                    success_criteria=["Tong ket duoc mon theo ngon ngu cua minh", "Co ke hoach on va hoc tiep"],
                    resources=["1-3-7-14 review plan"],
                ),
            ]

        return [
            NodeBlueprint(
                node_key="acad-map",
                stage="orientation",
                title=f"The map of {topic}",
                summary=f"Define the scope, major units, and success criteria for learning {topic}.",
                explanation=f"Without a topic map, learners drift. {self._coach_voice(learning_style, language)}",
                worked_example="A strong subject map includes core concepts, tools, problem types, and review loops.",
                practice_task=f"List 4-6 major units in {topic}, your goal, and the signals that show real progress.",
                reflection_prompt="Are you learning to pass, to perform, or to understand?",
                estimated_minutes=30,
                difficulty=1,
                common_pitfalls=["No scope", "No awareness of common problem types"],
                expected_keywords=["concept", "topic", "problem", "goal", "review"],
                success_criteria=["Define the study scope", "Name the real learning outcome"],
                resources=["Subject map note"],
            ),
            NodeBlueprint(
                node_key="acad-language",
                stage="foundation",
                title="Core language, symbols, and rules",
                summary="Learn the subject through its terms, symbols, and the meaning behind them.",
                explanation="Definitions matter only when they are tied to examples and usage.",
                worked_example="In math or science, notation is not decoration. It encodes relationships and assumptions.",
                practice_task=f"Pick 5-8 key terms or symbols in {topic} and explain what each means and when it is used.",
                reflection_prompt="Which symbol or term do you see often but still not fully understand?",
                estimated_minutes=40,
                difficulty=1,
                common_pitfalls=["Memorizing definitions without use", "No examples attached to terms"],
                expected_keywords=["definition", "symbol", "rule", "meaning", "example"],
                success_criteria=["Explain the key terms", "Tie a rule to an example"],
                resources=["Glossary card set"],
            ),
            NodeBlueprint(
                node_key="acad-worked-example",
                stage="core",
                title="Worked example",
                summary="Walk through a model solution to see reasoning, not just the final answer.",
                explanation="Progress comes from noticing the problem type, tool choice, sequence, and checks.",
                worked_example="A good worked example explains why each move is made and what assumption is being used.",
                practice_task=f"Choose one {topic} problem and explain each step: what is given, what is asked, which method is used, and why.",
                reflection_prompt="Do you usually get stuck at problem recognition or method selection?",
                estimated_minutes=45,
                difficulty=2,
                common_pitfalls=["Jumping straight to the answer", "Copying steps without understanding"],
                expected_keywords=["step", "method", "reason", "result", "check"],
                success_criteria=["Explain the steps and reasons", "Include a result check"],
                resources=["Worked-example template"],
            ),
            NodeBlueprint(
                node_key="acad-practice",
                stage="applied",
                title="Deliberate practice",
                summary="Use targeted exercises, track mistakes, and correct the pattern instead of the single answer.",
                explanation="Practice works best when difficulty, error patterns, and review are visible.",
                worked_example="Solve 3 familiar problems, then 1 transfer problem to test real understanding.",
                practice_task=f"Create a practice plan for {topic}: 2 basic problems, 1 medium problem, 1 transfer problem, and how you will review mistakes.",
                reflection_prompt="Are your errors conceptual or procedural?",
                estimated_minutes=45,
                difficulty=2,
                common_pitfalls=["Too many nearly identical problems", "Not logging repeated mistakes"],
                expected_keywords=["practice", "mistake", "review", "transfer", "difficulty"],
                success_criteria=["Create a clear practice plan", "Define how mistakes will be reviewed"],
                resources=["Mistake log"],
            ),
            NodeBlueprint(
                node_key="acad-transfer",
                stage="integration",
                title="Application and transfer",
                summary=f"Check whether you can carry the ideas in {topic} into a fresh problem on the path to {final_target}.",
                explanation="This separates recognition from understanding.",
                worked_example="If the context changes but you still choose a sensible path, that is transfer.",
                practice_task=f"Explain how you would approach a new {topic} problem that is not identical to the example. Name assumptions, method, and checks.",
                reflection_prompt="When the context changes, do you lose the method completely?",
                estimated_minutes=50,
                difficulty=3,
                common_pitfalls=["Only solving familiar formats", "Losing direction on fresh variants"],
                expected_keywords=["apply", "transfer", "method", "assumption", "check"],
                success_criteria=["Propose a valid approach to a new problem", "Define how you will verify it"],
                resources=["Transfer checklist"],
            ),
            NodeBlueprint(
                node_key="acad-review",
                stage="capstone",
                title="Review and next-step plan",
                summary="Consolidate the subject with teach-back, spaced review, and the next priorities.",
                explanation="The goal is not familiarity. The goal is being able to explain and solve.",
                worked_example="A strong review highlights key ideas, recurring problem types, and the most common mistakes.",
                practice_task=f"Write 150-250 words summarizing {topic}, then name three important problem types, three common mistakes, and a review plan toward {final_target}.",
                reflection_prompt="If you had to teach this to a classmate, which part still feels unstable?",
                estimated_minutes=35,
                difficulty=2,
                common_pitfalls=["Remembering headings without being able to solve", "No review plan"],
                expected_keywords=["summary", "example", "mistake", "review", "next"],
                success_criteria=["Summarize the subject in your own words", "Set a review and next-step plan"],
                resources=["1-3-7-14 review plan"],
            ),
        ]

    def _build_general_blueprints(
        self,
        topic: str,
        outcome_target: str | None,
        learning_style: str,
        language: str,
    ) -> list[NodeBlueprint]:
        final_target = outcome_target or (
            f"giai thich va ap dung duoc {topic}" if language == "vi"
            else f"explain and apply {topic}"
        )
        if language == "vi":
            return [
                NodeBlueprint(
                    node_key="general-scope",
                    stage="orientation",
                    title=f"Xac dinh pham vi {topic}",
                    summary="Bien muc tieu mo ho thanh pham vi ro rang, dau ra ro rang, va ly do hoc ro rang.",
                    explanation=f"Neu khong ro pham vi, moi lo trinh deu de tro thanh target ao. {self._coach_voice(learning_style, language)}",
                    worked_example="Tu 'hoc AI' doi thanh 'phan biet AI/ML/DL, mo ta pipeline, va de xuat mini project'.",
                    practice_task=f"Viet lai muc tieu hoc {topic} thanh 3 phan: muon biet gi, muon lam duoc gi, va muon dat ket qua nao.",
                    reflection_prompt="Dau ra that cua ban la biet, lam, hay day lai cho nguoi khac?",
                    estimated_minutes=30,
                    difficulty=1,
                    common_pitfalls=["Muc tieu qua chung chung", "Khong co dau ra xac minh"],
                    expected_keywords=["goal", "scope", "output", "problem", "result"],
                    success_criteria=["Tuyen bo duoc muc tieu ro rang", "Co dau ra xac minh duoc"],
                    resources=["Goal reframing note"],
                ),
                NodeBlueprint(
                    node_key="general-vocab",
                    stage="foundation",
                    title="Tu vung va khai niem cot loi",
                    summary="Hoc ngon ngu cua chu de de khong bi mo ho ngay tu dau.",
                    explanation="Ten goi dung giup ban tim tai lieu dung va dat cau hoi dung.",
                    worked_example="Chi can goi sai ten khai niem, ca lo trinh hoc co the lech huong.",
                    practice_task=f"Chon 5-8 tu khoa cot loi trong {topic}, dinh nghia chung, va moi tu cho 1 vi du ngan.",
                    reflection_prompt="Tu khoa nao ban nghe nhieu nhat nhung chua dung duoc?",
                    estimated_minutes=35,
                    difficulty=1,
                    common_pitfalls=["Doc nhieu nhung khong tao glossary", "Dung tu khoa khong nhat quan"],
                    expected_keywords=["term", "definition", "concept", "example", "meaning"],
                    success_criteria=["Tao duoc glossary co vi du", "Biet tu nao la cot loi"],
                    resources=["Glossary note"],
                ),
                NodeBlueprint(
                    node_key="general-model",
                    stage="core",
                    title="Mental model",
                    summary="Ghep cac khai niem thanh mot cau truc de nho va ap dung.",
                    explanation="Mental model la thu dung de ra quyet dinh, khong chi de nho.",
                    worked_example="Mot mental model tot tra loi duoc: no la gi, dung khi nao, va gioi han o dau.",
                    practice_task=f"Xay mot mental model cho {topic}: cac thanh phan chinh, quan he giua chung, va khi nao nen dung.",
                    reflection_prompt="Ban dang nho danh sach hay dang thay quan he giua cac y?",
                    estimated_minutes=40,
                    difficulty=2,
                    common_pitfalls=["Chi hoc theo checklist", "Khong biet khi nao khong nen dung y tuong do"],
                    expected_keywords=["model", "relationship", "when", "limit", "use"],
                    success_criteria=["Mo ta duoc cau truc va quan he", "Neu duoc gioi han cua y tuong"],
                    resources=["One-page concept map"],
                ),
                NodeBlueprint(
                    node_key="general-example",
                    stage="applied",
                    title="Vi du va truong hop cu the",
                    summary="Dua y tuong xuong mat dat bang mot vi du co input, quyet dinh va ket qua.",
                    explanation="Neu khong co vi du, khai niem rat de tro thanh loi noi chung chung.",
                    worked_example="Lay mot tinh huong cu the, chi ra input, cach chon huong, va ket qua mong doi.",
                    practice_task=f"Chon mot truong hop cu the cho {topic}. Giai thich input, cach xu ly, ket qua, va rui ro neu lam sai.",
                    reflection_prompt="Vi du cua ban da cu the den muc nguoi khac co the lam lai chua?",
                    estimated_minutes=40,
                    difficulty=2,
                    common_pitfalls=["Vi du qua chung", "Khong co input-output ro rang"],
                    expected_keywords=["example", "input", "decision", "output", "risk"],
                    success_criteria=["Mo ta duoc truong hop cu the", "Chi ra duoc rui ro va gioi han"],
                    resources=["Case note template"],
                ),
                NodeBlueprint(
                    node_key="general-apply",
                    stage="integration",
                    title="Ap dung vao muc tieu ca nhan",
                    summary=f"Ket noi kien thuc voi muc tieu that de tien toi {final_target}.",
                    explanation="Day la luc y tuong phai song duoc trong bai toan cua chinh ban.",
                    worked_example="Thay vi chi noi da hieu, ban phai neu duoc ban se dung no o dau, de lam gi, va do bang cach nao.",
                    practice_task=f"Mo ta cach ban se ap dung {topic} vao mot muc tieu ca nhan: boi canh, cac buoc, dau ra, cach do ket qua.",
                    reflection_prompt="Phan nao cua muc tieu ca nhan van chua map duoc voi kien thuc vua hoc?",
                    estimated_minutes=45,
                    difficulty=3,
                    common_pitfalls=["Lien he qua mo ho", "Khong co cach do ket qua"],
                    expected_keywords=["apply", "context", "step", "output", "measure"],
                    success_criteria=["Map duoc kien thuc vao use case that", "Co cach do tien bo hoac ket qua"],
                    resources=["Application brief"],
                ),
                NodeBlueprint(
                    node_key="general-teachback",
                    stage="capstone",
                    title="Teach-back va review",
                    summary="Tu giai thich lai chu de, chi ra gioi han, va lap lich on.",
                    explanation="Day la chot chan de tranh ao tuong da hieu.",
                    worked_example="Teach-back tot se co dinh nghia, vi du, gioi han, va buoc tiep theo.",
                    practice_task=f"Viet 150-250 tu giai thich {topic} cho nguoi moi, them 1 vi du, 1 gioi han, va 3 buoc tiep theo de dat {final_target}.",
                    reflection_prompt="Neu khong duoc nhin note, ban con giai thich mach lac duoc khong?",
                    estimated_minutes=35,
                    difficulty=2,
                    common_pitfalls=["Nho tu khoa nhung khong noi thanh cau", "Khong chi ra gioi han"],
                    expected_keywords=["explain", "example", "limit", "review", "next"],
                    success_criteria=["Teach-back ro rang", "Co review plan va next steps"],
                    resources=["Reflection checklist"],
                ),
            ]

        return [
            NodeBlueprint(
                node_key="general-scope",
                stage="orientation",
                title=f"Scoping {topic}",
                summary="Turn a vague goal into a clear scope, a concrete output, and a real reason to learn.",
                explanation=f"Without scope, progress turns into fake progress. {self._coach_voice(learning_style, language)}",
                worked_example="Shift from 'learn AI' to 'distinguish AI/ML/DL, describe the pipeline, and propose a mini project'.",
                practice_task=f"Rewrite your goal for {topic} into three parts: what you want to know, what you want to do, and the outcome you want to reach.",
                reflection_prompt="Is your true target knowledge, execution, or the ability to teach others?",
                estimated_minutes=30,
                difficulty=1,
                common_pitfalls=["Goal too vague", "No observable outcome"],
                expected_keywords=["goal", "scope", "output", "problem", "result"],
                success_criteria=["State a clear goal", "Define an observable outcome"],
                resources=["Goal reframing note"],
            ),
            NodeBlueprint(
                node_key="general-vocab",
                stage="foundation",
                title="Core vocabulary",
                summary="Learn the language of the topic so you can search, ask, and reason precisely.",
                explanation="Correct naming is often the difference between finding the right material and wandering.",
                worked_example="One wrong term can send the entire learning path in the wrong direction.",
                practice_task=f"Pick 5-8 core terms in {topic}, define them simply, and give one example for each.",
                reflection_prompt="Which term do you hear often but still not really use?",
                estimated_minutes=35,
                difficulty=1,
                common_pitfalls=["No glossary", "Inconsistent terms"],
                expected_keywords=["term", "definition", "concept", "example", "meaning"],
                success_criteria=["Build a glossary with examples", "Identify which terms are core"],
                resources=["Glossary note"],
            ),
            NodeBlueprint(
                node_key="general-model",
                stage="core",
                title="Mental model",
                summary="Turn separate ideas into a usable structure you can reason with.",
                explanation="A mental model is for making choices, not just storing facts.",
                worked_example="A strong model answers what it is, when to use it, and where it breaks.",
                practice_task=f"Build a mental model for {topic}: main parts, relationships, and when it should be used.",
                reflection_prompt="Are you memorizing a list, or seeing the relationships between ideas?",
                estimated_minutes=40,
                difficulty=2,
                common_pitfalls=["Checklist learning only", "No sense of limits"],
                expected_keywords=["model", "relationship", "when", "limit", "use"],
                success_criteria=["Describe the structure and relationships", "Name the limits of the idea"],
                resources=["One-page concept map"],
            ),
            NodeBlueprint(
                node_key="general-example",
                stage="applied",
                title="Concrete example",
                summary="Ground the topic in a case with inputs, decisions, and outcomes.",
                explanation="Without examples, concepts stay abstract and slippery.",
                worked_example="Pick a concrete situation, identify the inputs, the decision path, and the expected outcome.",
                practice_task=f"Choose one concrete case for {topic}. Explain the inputs, the process, the output, and the risk if it is done badly.",
                reflection_prompt="Is your example concrete enough that someone else could repeat it?",
                estimated_minutes=40,
                difficulty=2,
                common_pitfalls=["Example too generic", "No input/output framing"],
                expected_keywords=["example", "input", "decision", "output", "risk"],
                success_criteria=["Describe a concrete case", "Point out risks and limits"],
                resources=["Case note template"],
            ),
            NodeBlueprint(
                node_key="general-apply",
                stage="integration",
                title="Apply it to your own goal",
                summary=f"Connect the ideas to a real personal outcome on the path to {final_target}.",
                explanation="This is where the topic has to survive contact with a real use case.",
                worked_example="Instead of saying you understand it, state where you would use it, for what, and how you would measure success.",
                practice_task=f"Describe how you would apply {topic} to a personal goal: context, steps, outputs, and how you would measure the result.",
                reflection_prompt="Which part of your personal goal still does not map cleanly to the topic?",
                estimated_minutes=45,
                difficulty=3,
                common_pitfalls=["Application too vague", "No way to measure the outcome"],
                expected_keywords=["apply", "context", "step", "output", "measure"],
                success_criteria=["Map the topic to a real use case", "Define a way to measure progress or output"],
                resources=["Application brief"],
            ),
            NodeBlueprint(
                node_key="general-teachback",
                stage="capstone",
                title="Teach-back and review",
                summary="Explain the topic clearly, note its limits, and schedule review.",
                explanation="This is the anti-illusion step that turns familiarity into evidence.",
                worked_example="A strong teach-back includes definition, example, limitation, and next step.",
                practice_task=f"Write 150-250 words explaining {topic} to a beginner, then add one example, one limitation, and three next steps toward {final_target}.",
                reflection_prompt="Without notes, can you still explain this cleanly?",
                estimated_minutes=35,
                difficulty=2,
                common_pitfalls=["Term recall without fluent explanation", "No explicit limits"],
                expected_keywords=["explain", "example", "limit", "review", "next"],
                success_criteria=["Produce a clear teach-back", "Set a review plan and next steps"],
                resources=["Reflection checklist"],
            ),
        ]

    def _node_metadata(self, node: LearningProgramNode) -> dict[str, Any]:
        return _json_load_dict(node.metadata_json)

    def _is_generated_remedial_node(self, node: LearningProgramNode | None) -> bool:
        if node is None:
            return False
        metadata = self._node_metadata(node)
        return str(metadata.get("generated") or "").strip().lower() == "remedial"

    def _failure_count_for_node(self, program_id: str, node_id: str) -> int:
        return int(
            self.db.query(LearningAssessmentAttempt)
            .filter(
                LearningAssessmentAttempt.user_id == self.current_user.id,
                LearningAssessmentAttempt.program_id == program_id,
                LearningAssessmentAttempt.node_id == node_id,
                LearningAssessmentAttempt.passed.is_(False),
            )
            .count()
        )

    def _find_active_remedial_node(
        self,
        program: LearningProgram,
        parent_node: LearningProgramNode,
    ) -> LearningProgramNode | None:
        for node in sorted(program.nodes or [], key=lambda item: item.position):
            metadata = self._node_metadata(node)
            if str(metadata.get("generated") or "").strip().lower() != "remedial":
                continue
            if str(metadata.get("parent_node_id") or "").strip() != parent_node.id:
                continue
            if node.mastery_status != "completed":
                return node
        return None

    def _clear_remedial_flag(
        self,
        program: LearningProgram,
        parent_node_id: str | None,
    ) -> None:
        if not parent_node_id:
            return
        parent = next((node for node in (program.nodes or []) if node.id == parent_node_id), None)
        if parent is None:
            return
        metadata = self._node_metadata(parent)
        if "has_active_remedial" in metadata:
            metadata["has_active_remedial"] = False
        parent.metadata_json = _json_dumps(metadata)

    def _sync_goal_milestones(
        self,
        program: LearningProgram,
        learning_state: dict[str, Any],
    ) -> None:
        current_goal = learning_state.get("current_goal")
        if not isinstance(current_goal, dict):
            return
        current_goal["milestones"] = [
            {
                "node_id": node.id,
                "node_key": node.node_key,
                "title": node.title,
            }
            for node in sorted(program.nodes or [], key=lambda item: item.position)
        ]

    def _shift_node_positions_for_insert(
        self,
        program: LearningProgram,
        *,
        from_position: int,
    ) -> None:
        nodes = sorted(
            [node for node in (program.nodes or []) if int(node.position or 0) >= from_position],
            key=lambda item: item.position,
            reverse=True,
        )
        for node in nodes:
            node.position = int(node.position or 0) + 1

    def _build_remedial_node_blueprint(
        self,
        *,
        program: LearningProgram,
        parent_node: LearningProgramNode,
        latest_result: dict[str, Any],
    ) -> NodeBlueprint:
        is_vi = program.language == "vi"
        misconceptions = latest_result.get("misconceptions") if isinstance(latest_result.get("misconceptions"), list) else []
        trigger_gap = str(misconceptions[0] if misconceptions else "").strip()
        expected_keywords = list(dict.fromkeys(
            _json_load_list(parent_node.expected_keywords_json)[:4]
            + _extract_terms(trigger_gap, limit=4)
        ))[:6]
        success_criteria = [
            (
                "Can restate the weak idea cleanly in their own words"
                if not is_vi else "Dien dat lai duoc y dang yeu bang loi cua minh"
            ),
            (
                "Can solve one tiny transfer variant without copying the worked example"
                if not is_vi else "Lam duoc mot bien the nho ma khong chep sat vi du mau"
            ),
            (
                f"Returns to '{parent_node.title}' with fewer mistakes"
                if not is_vi else f"Quay lai '{parent_node.title}' voi it loi hon"
            ),
        ]
        resources = [
            (
                f"Return to {parent_node.title} after this repair block."
                if not is_vi else f"Quay lai {parent_node.title} sau block cuong co nay."
            )
        ]
        title = (
            f"Cường cố: {parent_node.title}"
            if is_vi else f"Repair block: {parent_node.title}"
        )
        summary = (
            f"Chặn target ảo bằng cách vá đúng lỗ hổng đang làm bạn trượt ở '{parent_node.title}'."
            if is_vi else f"Repair the exact weak link that is blocking progress in '{parent_node.title}'."
        )
        explanation = (
            f"PigTex chen block nay vi bang chung cho thay ban dang ket o cho: {trigger_gap or 'một lỗ hổng khái niệm hoặc quy trình'}. "
            "Muc tieu khong phai hoc them rong, ma la sua dung cho dang gay fail."
            if is_vi else
            f"PigTex inserted this block because the evidence suggests a bottleneck here: {trigger_gap or 'a conceptual or procedural weak link'}. "
            "The goal is not wider coverage. The goal is repairing the exact gap that is causing failure."
        )
        worked_example = (
            "Tach y dang yeu thanh 3 phan: no la gi, khi nao dung, va dau hieu cho thay ban dang dung sai."
            if is_vi else
            "Break the weak idea into three parts: what it is, when to use it, and how to detect misuse."
        )
        practice_task = (
            f"Viet lai ngắn gọn y dang yeu trong '{parent_node.title}', them 1 vi du moi, va neu 1 cach tu kiem tra de tranh lap lai loi."
            if is_vi else
            f"Restate the weak idea inside '{parent_node.title}', add one fresh example, and name one self-check to avoid repeating the mistake."
        )
        reflection_prompt = (
            "Loi nay den tu thieu hieu ban chat, hay do ban nhay vao dap an qua som?"
            if is_vi else
            "Is this failure coming from a weak concept, or from jumping to the answer too early?"
        )
        return NodeBlueprint(
            node_key=f"{parent_node.node_key}_repair",
            stage="foundation",
            title=title,
            summary=summary,
            explanation=explanation,
            worked_example=worked_example,
            practice_task=practice_task,
            reflection_prompt=reflection_prompt,
            estimated_minutes=max(15, min(30, int(parent_node.estimated_minutes or 25))),
            difficulty=max(1, int(parent_node.difficulty or 1) - 1),
            common_pitfalls=list(dict.fromkeys(([trigger_gap] if trigger_gap else []) + _json_load_list(parent_node.common_pitfalls_json)[:2]))[:3],
            expected_keywords=expected_keywords or _json_load_list(parent_node.expected_keywords_json)[:4],
            success_criteria=success_criteria,
            resources=resources,
        )

    def _maybe_insert_remedial_node(
        self,
        *,
        program: LearningProgram,
        parent_node: LearningProgramNode,
        latest_result: dict[str, Any],
        evidence_id: str,
    ) -> LearningProgramNode | None:
        if self._is_generated_remedial_node(parent_node):
            return None
        if latest_result.get("passed"):
            return None

        failure_count = self._failure_count_for_node(program.id, parent_node.id)
        score = float(latest_result.get("score") or 0.0)
        has_clear_gap = bool(latest_result.get("misconceptions"))
        if failure_count < 2 and not (score < 0.44 and has_clear_gap):
            return None

        existing = self._find_active_remedial_node(program, parent_node)
        if existing is not None:
            existing.mastery_status = "ready"
            return existing

        blueprint = self._build_remedial_node_blueprint(
            program=program,
            parent_node=parent_node,
            latest_result=latest_result,
        )
        insert_position = int(parent_node.position or 1)
        self._shift_node_positions_for_insert(program, from_position=insert_position)
        parent_metadata = self._node_metadata(parent_node)
        parent_node.metadata_json = _json_dumps({
            **parent_metadata,
            "has_active_remedial": True,
            "latest_remedial_trigger_evidence_id": evidence_id,
        })

        remedial_node = LearningProgramNode(
            program_id=program.id,
            position=insert_position,
            node_key=f"{parent_node.node_key}_repair_{uuid4().hex[:4]}",
            stage=blueprint.stage,
            title=blueprint.title,
            summary=blueprint.summary,
            explanation=blueprint.explanation,
            worked_example=blueprint.worked_example,
            practice_task=blueprint.practice_task,
            reflection_prompt=blueprint.reflection_prompt,
            estimated_minutes=blueprint.estimated_minutes,
            difficulty=blueprint.difficulty,
            prerequisites_json=_json_dumps(_json_load_list(parent_node.prerequisites_json)),
            common_pitfalls_json=_json_dumps(blueprint.common_pitfalls),
            expected_keywords_json=_json_dumps(blueprint.expected_keywords),
            success_criteria_json=_json_dumps(blueprint.success_criteria),
            resources_json=_json_dumps(blueprint.resources),
            metadata_json=_json_dumps({
                **parent_metadata,
                "generated": "remedial",
                "parent_node_id": parent_node.id,
                "trigger_evidence_id": evidence_id,
            }),
            mastery_status="ready",
            mastery_score=0.0,
            evidence_count=0,
        )
        self.db.add(remedial_node)
        self.db.flush()
        return remedial_node

    def _resolve_session_node(
        self,
        program: LearningProgram,
        node_id: str | None,
    ) -> LearningProgramNode:
        nodes = sorted(program.nodes or [], key=lambda item: item.position)
        if not nodes:
            raise HTTPException(status_code=400, detail="Learning program has no nodes")

        if node_id:
            node = next((item for item in nodes if item.id == node_id), None)
            if node is None:
                raise HTTPException(status_code=404, detail="Learning node not found")
            if node.mastery_status == "locked":
                raise HTTPException(status_code=400, detail="Learning node is locked")
            return node

        next_node = self._get_next_focus_node(program)
        if next_node is None:
            return nodes[-1]
        return next_node

    def _get_next_focus_node(self, program: LearningProgram) -> LearningProgramNode | None:
        nodes = sorted(program.nodes or [], key=lambda item: item.position)
        if not nodes:
            return None

        due_reviews = [node for node in nodes if self._is_review_due(node)]
        if due_reviews:
            return due_reviews[0]

        for node in nodes:
            if node.mastery_status in {"ready", "reviewing"}:
                return node

        for node in nodes:
            if node.mastery_status != "completed":
                return node

        return None

    def _is_review_due(self, node: LearningProgramNode) -> bool:
        review_due_at = _coerce_utc(node.review_due_at)
        return bool(review_due_at and review_due_at <= _utcnow())

    def _unlock_following_nodes(self, program: LearningProgram) -> None:
        nodes = sorted(program.nodes or [], key=lambda item: item.position)
        if not nodes:
            return

        if nodes[0].mastery_status == "locked":
            nodes[0].mastery_status = "ready"

        for index in range(1, len(nodes)):
            previous = nodes[index - 1]
            current = nodes[index]
            if previous.mastery_status == "completed" and current.mastery_status == "locked":
                current.mastery_status = "ready"

    def _build_session_packet(
        self,
        program: LearningProgram,
        node: LearningProgramNode,
        *,
        learning_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        learning_state = learning_state or self._ensure_learning_state(program)
        learning_state = self._refresh_learning_state_views(
            program=program,
            learning_state=learning_state,
            focus_node=node,
        )
        is_vi = program.language == "vi"
        success_criteria = _json_load_list(node.success_criteria_json)
        common_pitfalls = _json_load_list(node.common_pitfalls_json)
        workspace_resources = self._load_workspace_resources(program.workspace_id)
        instructional_mode = self._instructional_mode_for_node(node, is_review=self._is_review_due(node))
        session_mode = "review" if instructional_mode == "review" else "learn"
        opening_message = (
            f"Buoi nay chung ta hoc node {node.position}: {node.title}."
            if is_vi else
            f"Today we are focusing on node {node.position}: {node.title}."
        )
        next_action = node.practice_task
        checklist = self._visible_checklist_slice(
            self._rebuild_progress_checklist(
                program=program,
                learning_state=learning_state,
                focus_node=node,
            ),
            node,
        )
        memory_update_summary = learning_state.get("last_memory_update_summary") or self._default_memory_update_summary(program.language)
        assistant_message = self._build_assistant_message(
            program=program,
            node=node,
            instructional_mode=instructional_mode,
            next_step=next_action,
        )
        turn_output = self._build_turn_output(
            program=program,
            node=node,
            learning_state=learning_state,
            instructional_mode=instructional_mode,
            assistant_message=assistant_message,
            progress_checklist=checklist,
            memory_update_summary=memory_update_summary,
            evidence_collected=[],
            next_step=next_action,
            confidence_level=0.42,
        )
        return {
            "mode": session_mode,
            "instructional_mode": instructional_mode,
            "opening_message": opening_message,
            "next_action": next_action,
            "program": {
                "id": program.id,
                "title": program.title,
                "topic": program.topic,
                "goal": program.goal,
                "outcome_target": program.outcome_target,
            },
            "node": self._serialize_node(node),
            "active_goal": learning_state.get("current_goal"),
            "teaching_points": [
                node.summary,
                node.explanation,
                common_pitfalls[0] if common_pitfalls else None,
            ],
            "worked_example": node.worked_example,
            "practice_task": node.practice_task,
            "reflection_prompt": node.reflection_prompt,
            "success_criteria": success_criteria,
            "workspace_resources": workspace_resources,
            "progress_checklist": checklist,
            "memory_update_summary": memory_update_summary,
            "turn_output": turn_output,
            "learning_state_snapshot": learning_state,
        }

    def _review_day_for_evidence(
        self,
        evidence_count: int,
        *,
        latest_score: float | None = None,
        stage: str | None = None,
    ) -> int:
        evidence_index = max(1, int(evidence_count or 1)) - 1
        if evidence_index >= len(self.REVIEW_DAY_STEPS):
            evidence_index = len(self.REVIEW_DAY_STEPS) - 1
        review_days = self.REVIEW_DAY_STEPS[evidence_index]
        if latest_score is not None:
            if latest_score >= 0.9:
                review_days += 2
            elif latest_score >= 0.82:
                review_days += 1
            elif latest_score < 0.74:
                review_days = max(1, review_days - 1)
        if stage in {"integration", "capstone"} and (latest_score or 0.0) >= 0.82:
            review_days += 1
        return min(45, review_days)

    def _grade_node_answer(
        self,
        program: LearningProgram,
        node: LearningProgramNode,
        answer: str,
        *,
        instructional_mode: str = "guided_practice",
        learning_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        is_vi = program.language == "vi"
        words = re.findall(r"\w+", answer, re.UNICODE)
        word_count = len(words)
        line_count = len([line for line in answer.splitlines() if line.strip()])
        expected_keywords = _json_load_list(node.expected_keywords_json)
        success_criteria = _json_load_list(node.success_criteria_json)
        criteria_terms = _extract_terms(success_criteria, limit=18)
        matched_keywords, missing_keywords = _keyword_hits(answer, expected_keywords)
        matched_criteria, missing_criteria = _keyword_hits(answer, criteria_terms)
        keyword_ratio = (
            len(matched_keywords) / len(expected_keywords)
            if expected_keywords else min(1.0, word_count / 50)
        )
        criteria_score = (
            len(matched_criteria) / len(criteria_terms)
            if criteria_terms else max(0.42, keyword_ratio)
        )
        normalized_answer = _normalize_text(answer)
        reasoning_markers = (
            ("because", "why", "if", "when", "for example", "so that", "therefore")
            if not is_vi else
            ("vi sao", "neu", "khi", "vi du", "boi vi", "nen", "de")
        )
        reasoning_hits = sum(1 for marker in reasoning_markers if marker in normalized_answer)
        reasoning_score = min(1.0, reasoning_hits / 2) if reasoning_hits else 0.25
        example_score = 1.0 if ("example" in normalized_answer or "vi du" in normalized_answer) else 0.4
        structure_score = 1.0 if line_count >= 2 or ":" in answer else 0.55
        transfer_markers = (
            ("apply", "transfer", "assumption", "compare", "tradeoff", "limit", "different context")
            if not is_vi else
            ("ap dung", "transfer", "gia thuyet", "so sanh", "doi ngu canh", "gioi han", "khi doi")
        )
        transfer_hits = sum(1 for marker in transfer_markers if marker in normalized_answer)
        transfer_floor = 0.3 if node.stage in {"integration", "capstone"} or instructional_mode in {"independent_practice", "review"} else 0.5
        transfer_score = min(1.0, transfer_floor + transfer_hits * 0.25)

        source_terms: list[str] = []
        if isinstance(learning_state, dict):
            source_registry = learning_state.get("source_registry")
            if isinstance(source_registry, dict):
                source_terms.extend(_extract_terms(source_registry.get("focus_terms"), limit=8))
                for source in source_registry.get("sources", [])[:3]:
                    if isinstance(source, dict):
                        source_terms.extend(_extract_terms(source.get("matched_terms"), limit=6))
        source_terms = list(dict.fromkeys(source_terms))[:8]
        source_hits = [term for term in source_terms if term in _normalize_text(answer, ascii_fold=True)]
        grounding_score = 0.55
        if source_terms:
            grounding_score = min(1.0, 0.35 + len(source_hits) / max(2, min(6, len(source_terms))))

        similarity_to_example = _text_similarity(answer, node.worked_example)
        similarity_to_explanation = _text_similarity(answer, node.explanation)
        copy_similarity = max(similarity_to_example, similarity_to_explanation)
        originality_score = 1.0 if copy_similarity < 0.48 else 0.74 if copy_similarity < 0.68 else 0.34

        length_target = 95 if node.stage in {"integration", "capstone"} or instructional_mode in {"independent_practice", "review"} else 55
        length_score = min(1.0, word_count / length_target)

        if node.stage in {"integration", "capstone"} or instructional_mode in {"independent_practice", "review"}:
            weights = {
                "concept": 0.20,
                "criteria": 0.22,
                "reasoning": 0.12,
                "example": 0.08,
                "structure": 0.07,
                "transfer": 0.16,
                "grounding": 0.06,
                "originality": 0.05,
                "length": 0.04,
            }
        else:
            weights = {
                "concept": 0.28,
                "criteria": 0.18,
                "reasoning": 0.14,
                "example": 0.08,
                "structure": 0.08,
                "transfer": 0.08,
                "grounding": 0.06,
                "originality": 0.04,
                "length": 0.06,
            }

        score = (
            weights["concept"] * keyword_ratio
            + weights["criteria"] * criteria_score
            + weights["reasoning"] * reasoning_score
            + weights["example"] * example_score
            + weights["structure"] * structure_score
            + weights["transfer"] * transfer_score
            + weights["grounding"] * grounding_score
            + weights["originality"] * originality_score
            + weights["length"] * length_score
        )
        score = round(max(0.0, min(1.0, score)), 3)

        minimum_words = 34 if node.stage in {"integration", "capstone"} or instructional_mode in {"independent_practice", "review"} else 22
        require_example = node.stage in {"integration", "capstone"} or instructional_mode in {"independent_practice", "review"}
        minimum_keyword_ratio = 0.34 if node.stage not in {"orientation", "foundation"} else 0.28
        minimum_criteria_ratio = 0.24 if criteria_terms else 0.0
        score_threshold = 0.74 if node.stage in {"integration", "capstone"} or instructional_mode in {"independent_practice", "review"} else 0.66
        passed = (
            word_count >= minimum_words
            and keyword_ratio >= minimum_keyword_ratio
            and criteria_score >= minimum_criteria_ratio
            and score >= score_threshold
            and reasoning_hits >= 1
            and (not require_example or example_score >= 1.0)
            and not (instructional_mode in {"independent_practice", "review"} and copy_similarity >= 0.78)
        )

        strengths: list[str] = []
        misconceptions: list[str] = []
        if matched_keywords:
            strengths.append(
                ("Cham duoc nhieu tu khoa cot loi: " if is_vi else "You covered core keywords: ")
                + ", ".join(matched_keywords[:5])
            )
        if word_count >= minimum_words:
            strengths.append(
                "Cau tra loi du do dai de the hien lap luan."
                if is_vi else
                "The answer is long enough to show actual reasoning."
            )
        if reasoning_hits:
            strengths.append(
                "Ban co giai thich vi sao hoac khi nao nen dung y tuong nay."
                if is_vi else
                "You explained why or when the idea should be used."
            )
        if example_score >= 1.0:
            strengths.append(
                "Ban da dua them vi du/cu the hoa y."
                if is_vi else
                "You grounded the answer with an example."
            )
        if transfer_hits:
            strengths.append(
                "Ban da cho thay kha nang ap dung hoac doi ngu canh, khong chi nhac lai noi dung."
                if is_vi else
                "You showed some transfer or adaptation, not just recall."
            )
        if copy_similarity < 0.48:
            strengths.append(
                "Cau tra loi co dau hieu dien dat bang loi rieng cua ban."
                if is_vi else
                "The answer sounds meaningfully like your own explanation."
            )

        if word_count < minimum_words:
            misconceptions.append(
                f"Cau tra loi con qua ngan. Hay mo rong them de dat it nhat {minimum_words} tu."
                if is_vi else
                f"The answer is too short. Expand it to at least {minimum_words} words."
            )
        if missing_keywords:
            misconceptions.append(
                ("Dang thieu mot so y cot loi: " if is_vi else "Some core ideas are still missing: ")
                + ", ".join(missing_keywords[:5])
            )
        if missing_criteria:
            misconceptions.append(
                ("Chua cham du cac dau hieu thanh cong: " if is_vi else "The answer still misses success signals like: ")
                + ", ".join(missing_criteria[:4])
            )
        if not reasoning_hits:
            misconceptions.append(
                "Can noi ro vi sao, khi nao, hoac dieu kien nao de dung y nay."
                if is_vi else
                "Add more reasoning: why it works, when to use it, or under what conditions."
            )
        if structure_score < 1.0:
            misconceptions.append(
                "Thu chia cau tra loi thanh cac y ro rang hon."
                if is_vi else
                "Try structuring the answer into clearer parts."
            )
        if require_example and example_score < 1.0:
            misconceptions.append(
                "Can them mot vi du moi de chung minh ban thuc su ap dung duoc y nay."
                if is_vi else
                "Add one fresh example to prove you can apply the idea."
            )
        if transfer_score < 0.55 and node.stage in {"integration", "capstone"}:
            misconceptions.append(
                "Ban chua cho thay ro cach ap dung y nay sang bai moi/ngu canh moi."
                if is_vi else
                "You have not yet shown how the idea transfers to a new problem or context."
            )
        if instructional_mode in {"independent_practice", "review"} and copy_similarity >= 0.78:
            misconceptions.append(
                "Phan nay dang qua sat voi vi du giai san. Hay viet lai bang loi va vi du rieng cua ban."
                if is_vi else
                "This is too close to the worked example. Re-express it in your own words with your own example."
            )

        mastery_band = "strong" if score >= 0.85 and passed else "operational" if passed else "partial" if score >= 0.56 else "not_met"
        if passed:
            feedback = (
                f"Tot. Ban da dat node '{node.title}' voi bang chung kha chac. Tiep theo hay tang muc do doc lap hoac chuyen sang node ke."
                if is_vi else
                f"Good work. You passed '{node.title}' with credible evidence. Move forward or raise the independence level on the next turn."
            )
            next_action = (
                "Mo session tiep theo de tang do kho, hoac lam bai transfer nho de cung co bang chung."
                if is_vi else
                "Open the next session to increase difficulty, or do one small transfer task to strengthen the evidence."
            )
        else:
            feedback = (
                f"Chua dat node '{node.title}'. Hien tai PigTex van chua thay du bang chung ve do hieu va kha nang ap dung cua ban."
                if is_vi else
                f"Not passed yet for '{node.title}'. PigTex still needs clearer evidence that you understand and can apply the idea."
            )
            if instructional_mode in {"independent_practice", "review"} and copy_similarity >= 0.78:
                next_action = (
                    "Viet lai bang loi cua ban, doi vi du sang tinh huong moi, roi nop lai."
                    if is_vi else
                    "Rewrite it in your own words, switch to a fresh example, and submit again."
                )
            elif transfer_score < 0.55 and node.stage in {"integration", "capstone"}:
                next_action = (
                    "Lam mot bai moi/ngu canh moi va neu ro gia thuyet, cach lam, cach kiem tra."
                    if is_vi else
                    "Try a new problem or context and state the assumptions, method, and checks."
                )
            else:
                next_action = (
                    "Doc lai explanation, bo sung y con thieu, them 1 vi du, roi nop lai."
                    if is_vi else
                    "Re-read the explanation, add the missing ideas, include one example, and submit again."
                )

        return {
            "score": score,
            "passed": passed,
            "word_count": word_count,
            "matched_keywords": matched_keywords,
            "missing_keywords": missing_keywords,
            "matched_criteria": matched_criteria[:6],
            "missing_criteria": missing_criteria[:6],
            "strengths": strengths[:3],
            "misconceptions": misconceptions[:5],
            "feedback": feedback,
            "next_action": next_action,
            "success_criteria": success_criteria,
            "mastery_band": mastery_band,
            "rubric": {
                "keyword_ratio": round(keyword_ratio, 3),
                "criteria_score": round(criteria_score, 3),
                "length_score": round(length_score, 3),
                "reasoning_score": round(reasoning_score, 3),
                "example_score": round(example_score, 3),
                "structure_score": round(structure_score, 3),
                "transfer_score": round(transfer_score, 3),
                "grounding_score": round(grounding_score, 3),
                "originality_score": round(originality_score, 3),
                "copy_similarity": round(copy_similarity, 3),
            },
        }
