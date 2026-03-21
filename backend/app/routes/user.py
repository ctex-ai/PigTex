import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy import MetaData, Table, func, inspect, select

from ..database import get_db
from ..models import (
    Conversation,
    OAuthAccount,
    UsageRecord,
    User,
    Workspace,
)
from ..schemas import UsageSummary, UsageResponse
from .auth_utils import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    get_current_user,
    get_password_hash,
    get_user_permissions,
    verify_password,
)

router = APIRouter(prefix="/user", tags=["User"])
logger = logging.getLogger(__name__)


class ChangePasswordRequest(BaseModel):
    current_password: str = ""
    new_password: str = Field(min_length=8, max_length=128)


class DeleteAccountRequest(BaseModel):
    confirmation: str
    password: Optional[str] = Field(default=None, max_length=128)


def _build_profile_payload(current_user: User, oauth_account: OAuthAccount | None) -> dict:
    oauth_provider = (oauth_account.provider or "").strip().lower() if oauth_account else None
    avatar_url = (oauth_account.avatar_url or "").strip() if oauth_account else ""
    if not avatar_url and oauth_provider == "github" and oauth_account:
        provider_account_id = (oauth_account.provider_account_id or "").strip()
        if provider_account_id.isdigit():
            avatar_url = f"https://avatars.githubusercontent.com/u/{provider_account_id}?v=4"

    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "plan": current_user.plan,
        "role": current_user.role or "user",
        "is_admin": (current_user.role or "user") in {ROLE_ADMIN, ROLE_SUPER_ADMIN},
        "permissions": get_user_permissions(current_user),
        "is_active": current_user.is_active,
        "created_at": current_user.created_at,
        "last_login": current_user.last_login,
        "has_password": bool((current_user.hashed_password or "").strip()),
        "oauth_provider": oauth_provider,
        "avatar_url": avatar_url or None,
    }


def _delete_rows_referencing_ids(
    db: Session,
    referred_table: str,
    referred_ids: list[str],
    visited: Optional[set[tuple[str, tuple[str, ...]]]] = None,
) -> None:
    if not referred_ids:
        return

    normalized_ids = tuple(sorted({str(item) for item in referred_ids if item is not None}))
    if not normalized_ids:
        return

    if visited is None:
        visited = set()

    visit_key = (referred_table, normalized_ids)
    if visit_key in visited:
        return
    visited.add(visit_key)

    bind = db.get_bind()
    inspector = inspect(bind)
    metadata = MetaData()

    for table_name in inspector.get_table_names():
        if table_name == referred_table:
            continue

        matching_fk_columns: list[str] = []
        for fk in inspector.get_foreign_keys(table_name):
            if fk.get("referred_table") != referred_table:
                continue

            constrained_columns = fk.get("constrained_columns") or []
            referred_columns = fk.get("referred_columns") or []
            if len(constrained_columns) != 1 or referred_columns != ["id"]:
                continue

            matching_fk_columns.extend(constrained_columns)

        if not matching_fk_columns:
            continue

        table = Table(table_name, metadata, autoload_with=bind)
        pk_constraint = inspector.get_pk_constraint(table_name) or {}
        pk_columns = pk_constraint.get("constrained_columns") or []
        single_pk_column = pk_columns[0] if len(pk_columns) == 1 else None

        for column_name in dict.fromkeys(matching_fk_columns):
            column = table.c.get(column_name)
            if column is None:
                continue

            if single_pk_column and single_pk_column in table.c:
                child_ids = db.execute(
                    select(table.c[single_pk_column]).where(column.in_(normalized_ids))
                ).scalars().all()
                if child_ids:
                    _delete_rows_referencing_ids(db, table_name, [str(item) for item in child_ids], visited)

            db.execute(table.delete().where(column.in_(normalized_ids)))


@router.get("/usage", response_model=UsageSummary)
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current user's usage summary"""
    today = datetime.now(timezone.utc).date()
    start_of_month = today.replace(day=1)
    
    # Today's usage
    today_stats = db.query(
        func.count(UsageRecord.id).label("total_requests"),
        func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(UsageRecord.cost), 0.0).label("total_cost")
    ).filter(
        UsageRecord.user_id == current_user.id,
        func.date(UsageRecord.created_at) == today
    ).first()
    
    # This month's usage
    month_stats = db.query(
        func.count(UsageRecord.id).label("total_requests"),
        func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(UsageRecord.cost), 0.0).label("total_cost")
    ).filter(
        UsageRecord.user_id == current_user.id,
        func.date(UsageRecord.created_at) >= start_of_month
    ).first()
    
    return UsageSummary(
        today=UsageResponse(
            total_requests=today_stats.total_requests or 0,
            total_tokens=int(today_stats.total_tokens or 0),
            total_cost=float(today_stats.total_cost or 0.0),
            period="daily"
        ),
        this_month=UsageResponse(
            total_requests=month_stats.total_requests or 0,
            total_tokens=int(month_stats.total_tokens or 0),
            total_cost=float(month_stats.total_cost or 0.0),
            period="monthly"
        )
    )


@router.get("/profile")
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get user profile with plan info"""
    oauth_account = (
        db.query(OAuthAccount)
        .filter(OAuthAccount.user_id == current_user.id)
        .order_by(OAuthAccount.updated_at.desc(), OAuthAccount.created_at.desc())
        .first()
    )
    return _build_profile_payload(current_user, oauth_account)


@router.post("/password")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change or set a password for the current user."""
    new_password = payload.new_password.strip()
    current_password = payload.current_password
    has_existing_password = bool((current_user.hashed_password or "").strip())

    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới phải có ít nhất 8 ký tự",
        )

    if has_existing_password:
        if not current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vui lòng nhập mật khẩu hiện tại",
            )
        if not verify_password(current_password, current_user.hashed_password or ""):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mật khẩu hiện tại không đúng",
            )
        if verify_password(new_password, current_user.hashed_password or ""):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mật khẩu mới phải khác mật khẩu hiện tại",
            )

    current_user.hashed_password = get_password_hash(new_password)

    try:
        db.add(current_user)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật mật khẩu lúc này",
        )

    return {
        "ok": True,
        "message": "Đã cập nhật mật khẩu",
        "has_password": True,
    }


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    payload: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete the current user account and associated records."""
    user_id = str(current_user.id)
    confirmation = (payload.confirmation or "").strip().lower()
    if confirmation != current_user.email.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hãy nhập đúng email để xác nhận xóa tài khoản",
        )

    has_existing_password = bool((current_user.hashed_password or "").strip())
    if has_existing_password:
        password = (payload.password or "").strip()
        if not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vui lòng nhập mật khẩu để xóa tài khoản",
            )
        if not verify_password(password, current_user.hashed_password or ""):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mật khẩu không đúng",
            )

    workspace_ids = [
        workspace_id
        for (workspace_id,) in db.query(Workspace.id)
        .filter(Workspace.user_id == user_id)
        .all()
    ]
    conversation_ids = [
        conversation_id
        for (conversation_id,) in db.query(Conversation.id)
        .filter(Conversation.user_id == user_id)
        .all()
    ]

    try:
        _delete_rows_referencing_ids(db, "conversations", conversation_ids)

        if workspace_ids:
            db.query(Workspace).filter(
                Workspace.id.in_(workspace_ids)
            ).update({Workspace.parent_id: None}, synchronize_session=False)
            _delete_rows_referencing_ids(db, "workspaces", workspace_ids)

        _delete_rows_referencing_ids(db, "users", [user_id])

        deleted_user_count = db.query(User).filter(
            User.id == user_id
        ).delete(synchronize_session=False)
        if deleted_user_count != 1:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tài khoản không còn tồn tại",
            )

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to delete account user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể xóa tài khoản lúc này",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
