from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..database import get_db
from ..models import AdminAuditEvent, User
from ..prompting import LLMFoundryJudge, SkillFoundry, SkillJudgeConfig, build_foundry_from_env
from ..memory.prompt_injector import PromptInjector
from .auth_utils import require_permissions

router = APIRouter(prefix="/skill-foundry", tags=["Skill Foundry"])


class SkillFoundryCompileRequest(BaseModel):
    input_path: Optional[str] = Field(default=None, description="Relative path inside data/skill_foundry/incoming")
    dry_run: bool = False
    max_files: Optional[int] = Field(default=None, ge=1, le=2000)
    judge_model: Optional[str] = None
    judge_api_key: Optional[str] = None
    judge_api_base_url: Optional[str] = None


class SkillFoundryResolveRequest(BaseModel):
    message: str = Field(..., min_length=1)
    intent: Optional[str] = None
    keywords: Optional[list[str]] = None


class SkillFoundryPublishRequest(BaseModel):
    note: str = Field(default="", max_length=500)
    force: bool = False


class SkillFoundryRollbackRequest(BaseModel):
    release_id: str = Field(..., min_length=6, max_length=64)
    note: str = Field(default="", max_length=500)


def _build_route_foundry(request: Optional[SkillFoundryCompileRequest] = None) -> SkillFoundry:
    if request and request.judge_model and request.judge_api_key and request.judge_api_base_url:
        judge = LLMFoundryJudge(
            SkillJudgeConfig(
                model=request.judge_model,
                api_key=request.judge_api_key,
                api_base_url=request.judge_api_base_url,
            )
        )
        return SkillFoundry(judge=judge)
    return build_foundry_from_env()


def _resolve_compile_path(foundry: SkillFoundry, raw_path: Optional[str]) -> Path:
    incoming_root = foundry.incoming_path().resolve()
    if not raw_path:
        return incoming_root

    candidate = Path(raw_path)
    resolved = (incoming_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if incoming_root != resolved and incoming_root not in resolved.parents:
        raise HTTPException(status_code=400, detail="input_path must stay inside data/skill_foundry/incoming")
    return resolved


_MAX_AUDIT_JSON_LEN = 500_000  # ~500 KB safety cap


def _safe_json(data: Optional[dict[str, Any]], *, max_len: int = _MAX_AUDIT_JSON_LEN) -> Optional[str]:
    """Serialize *data* to JSON, truncating if the result exceeds *max_len*."""
    if data is None:
        return None
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw) <= max_len:
        return raw
    # Store a compact summary instead of the oversized blob
    truncated = {
        "__truncated__": True,
        "__original_len__": len(raw),
        "schema_version": data.get("schema_version"),
        "generated_at": data.get("generated_at"),
        "summary": data.get("summary"),
        "state": data.get("state"),
        "report_id": data.get("report_id"),
    }
    return json.dumps(truncated, ensure_ascii=False)


def _record_admin_audit(
    db: Session,
    *,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    status_value: str = "success",
    summary: Optional[str] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    event = AdminAuditEvent(
        actor_user_id=actor.id,
        action=action,
        resource_type=resource_type,
        resource_id=(resource_id or "").strip() or None,
        status=(status_value or "success").strip() or "success",
        summary=(summary or "").strip() or None,
        before_json=_safe_json(before),
        after_json=_safe_json(after),
        metadata_json=_safe_json(metadata),
    )
    db.add(event)
    db.commit()


@router.get("/overview")
async def get_skill_foundry_overview(
    current_user: User = Depends(require_permissions("admin.console:view", "skill.registry:read")),
):
    return build_foundry_from_env().get_admin_overview()


@router.get("/registry")
async def get_skill_foundry_registry(
    current_user: User = Depends(require_permissions("admin.console:view", "skill.registry:read")),
):
    foundry = build_foundry_from_env()
    return {
        "summary": foundry.registry_summary(),
        "active_registry": foundry.load_registry(),
        "draft_registry": foundry.load_draft_registry(),
        "catalog": foundry.load_catalog(),
    }


@router.get("/releases")
async def list_skill_foundry_releases(
    current_user: User = Depends(require_permissions("admin.console:view", "skill.registry:read")),
):
    return {"releases": build_foundry_from_env().list_releases()}


@router.get("/audit")
async def list_skill_foundry_audit(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permissions("admin.console:view", "skill.audit:read")),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AdminAuditEvent)
        .filter(AdminAuditEvent.resource_type == "skill_foundry")
        .order_by(AdminAuditEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "resource_id": row.resource_id,
                "status": row.status,
                "summary": row.summary,
                "created_at": row.created_at,
                "actor_user_id": row.actor_user_id,
                "before_json": row.before_json,
                "after_json": row.after_json,
                "metadata_json": row.metadata_json,
            }
            for row in rows
        ]
    }


@router.post("/resolve")
async def resolve_skill_foundry_matches(
    request: SkillFoundryResolveRequest,
    current_user: User = Depends(require_permissions("admin.console:view", "skill.resolve:read")),
    db: Session = Depends(get_db),
):
    injector = PromptInjector(db)
    detected_intent = request.intent or injector.detect_intent(request.message)
    keywords = request.keywords if request.keywords is not None else injector.extract_keywords(request.message)
    foundry = build_foundry_from_env()
    matches = foundry.resolve_matches(
        user_message=request.message,
        detected_intent=detected_intent,
        keywords=keywords,
    )
    return {
        "intent": detected_intent,
        "keywords": keywords,
        "matches": matches,
        "formatted": foundry.format_runtime_skills(matches),
    }


@router.post("/compile")
async def compile_skill_foundry(
    request: SkillFoundryCompileRequest,
    current_user: User = Depends(require_permissions("admin.console:view", "skill.compile:write")),
    db: Session = Depends(get_db),
):
    foundry = _build_route_foundry(request)
    compile_path = _resolve_compile_path(foundry, request.input_path)
    if not compile_path.exists():
        raise HTTPException(status_code=404, detail="input_path not found")

    before_state = foundry.load_draft_registry()
    try:
        report = await run_in_threadpool(
            foundry.compile_from_path,
            compile_path,
            dry_run=request.dry_run,
            max_files=request.max_files,
        )
    except Exception as exc:
        _record_admin_audit(
            db,
            actor=current_user,
            action="compile",
            resource_type="skill_foundry",
            resource_id=(request.input_path or "").strip() or "incoming",
            status_value="failed",
            summary=f"Compile failed: {exc}",
            before=before_state,
            metadata={
                "input_path": str(compile_path),
                "dry_run": request.dry_run,
                "max_files": request.max_files,
            },
        )
        raise

    after_state = foundry.load_draft_registry() if not request.dry_run else before_state
    publish_gate = report.get("publish_gate")
    _record_admin_audit(
        db,
        actor=current_user,
        action="compile",
        resource_type="skill_foundry",
        resource_id=(request.input_path or "").strip() or "incoming",
        summary="Compiled draft skill registry",
        before=before_state,
        after=after_state,
        metadata={
            "input_path": str(compile_path),
            "dry_run": request.dry_run,
            "report_id": report.get("report_id"),
            "summary": report.get("summary"),
            "publish_gate": publish_gate,
        },
    )
    return report


@router.post("/publish")
async def publish_skill_foundry(
    request: SkillFoundryPublishRequest,
    current_user: User = Depends(require_permissions("admin.console:view", "skill.publish:write")),
    db: Session = Depends(get_db),
):
    foundry = build_foundry_from_env()
    before_state = foundry.load_registry()
    try:
        release = foundry.publish_draft(
            released_by=current_user.email or current_user.username or current_user.id,
            note=request.note,
            force=bool(request.force),
        )
    except Exception as exc:
        publish_gate = foundry.evaluate_publish_gate()
        _record_admin_audit(
            db,
            actor=current_user,
            action="publish",
            resource_type="skill_foundry",
            status_value="failed",
            summary=f"Publish failed: {exc}",
            before=before_state,
            metadata={"note": request.note, "force": request.force, "publish_gate": publish_gate},
        )
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "publish_gate": publish_gate,
            },
        ) from exc

    after_state = foundry.load_registry()
    _record_admin_audit(
        db,
        actor=current_user,
        action="publish",
        resource_type="skill_foundry",
        resource_id=release["release_id"],
        summary="Published draft skill registry",
        before=before_state,
        after=after_state,
        metadata={**release, "force": request.force},
    )
    return {"release": release, "registry": after_state}


@router.post("/rollback")
async def rollback_skill_foundry(
    request: SkillFoundryRollbackRequest,
    current_user: User = Depends(require_permissions("admin.console:view", "skill.rollback:write")),
    db: Session = Depends(get_db),
):
    foundry = build_foundry_from_env()
    before_state = foundry.load_registry()
    try:
        rollback = foundry.rollback_release(
            request.release_id,
            rolled_back_by=current_user.email or current_user.username or current_user.id,
            note=request.note,
        )
    except Exception as exc:
        _record_admin_audit(
            db,
            actor=current_user,
            action="rollback",
            resource_type="skill_foundry",
            resource_id=request.release_id,
            status_value="failed",
            summary=f"Rollback failed: {exc}",
            before=before_state,
            metadata={"note": request.note},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    after_state = foundry.load_registry()
    _record_admin_audit(
        db,
        actor=current_user,
        action="rollback",
        resource_type="skill_foundry",
        resource_id=request.release_id,
        summary="Rolled back active skill registry",
        before=before_state,
        after=after_state,
        metadata=rollback,
    )
    return {"rollback": rollback, "registry": after_state}
