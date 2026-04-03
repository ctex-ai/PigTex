from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..services.learning_service import LearningService
from .auth_utils import get_current_user

router = APIRouter(prefix="/v1/learn", tags=["Learning"])


class CreateLearningProgramRequest(BaseModel):
    title: str | None = None
    topic: str
    goal: str
    outcome_target: str | None = None
    current_level: str = "beginner"
    learning_style: str = "guided"
    weekly_minutes: int = 180
    workspace_id: str | None = None
    target_date: datetime | None = None
    language: str = "vi"


class StartLearningSessionRequest(BaseModel):
    node_id: str | None = None


class SubmitLearningSessionResponseRequest(BaseModel):
    answer: str


def _service(db: Session, current_user: User) -> LearningService:
    return LearningService(db, current_user)


@router.get("/programs")
async def list_learning_programs(
    workspace_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return _service(db, current_user).list_programs(workspace_id)


@router.post("/programs")
async def create_learning_program(
    payload: CreateLearningProgramRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _service(db, current_user).create_program(
        title=payload.title,
        topic=payload.topic,
        goal=payload.goal,
        outcome_target=payload.outcome_target,
        current_level=payload.current_level,
        learning_style=payload.learning_style,
        weekly_minutes=payload.weekly_minutes,
        workspace_id=payload.workspace_id,
        target_date=payload.target_date,
        language=payload.language,
    )


@router.get("/programs/{program_id}")
async def get_learning_program(
    program_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _service(db, current_user).get_program(program_id)


@router.get("/live")
async def get_learning_live_state(
    conversation_id: str | None = None,
    workspace_id: str | None = None,
    program_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _service(db, current_user).get_live_state(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        program_id=program_id,
    )


@router.delete("/programs/{program_id}")
async def delete_learning_program(
    program_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    _service(db, current_user).delete_program(program_id)
    return {"ok": True}


@router.post("/programs/{program_id}/sessions")
async def start_learning_session(
    program_id: str,
    payload: StartLearningSessionRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _service(db, current_user).start_session(
        program_id,
        node_id=payload.node_id if payload else None,
    )


@router.post("/sessions/{session_id}/responses")
async def submit_learning_session_response(
    session_id: str,
    payload: SubmitLearningSessionResponseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _service(db, current_user).submit_session_response(session_id, payload.answer)


@router.get("/reviews")
async def list_learning_reviews(
    workspace_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return _service(db, current_user).list_due_reviews(workspace_id)
