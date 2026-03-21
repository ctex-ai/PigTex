from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import User
from ..services.cloud_backup_service import (
    CloudBackupConfigError,
    CloudBackupNotFoundError,
    CloudBackupQuotaExceededError,
    CloudBackupService,
    CloudBackupValidationError,
)
from ..services.sync_billing_service import SyncBillingService
from .auth_utils import get_current_user

router = APIRouter(prefix="/cloud", tags=["Cloud Backup"])


class QuotaResponse(BaseModel):
    plan_code: str
    quota_bytes: int
    retention_days: int
    max_devices: int
    max_snapshots: int
    sync_enabled: bool = False
    device_transfer_enabled: bool = False


class DeviceRegisterRequest(BaseModel):
    device_key: str = Field(..., min_length=1, max_length=191)
    device_name: str = Field(..., min_length=1, max_length=100)
    platform: str = Field(..., min_length=1, max_length=32)
    app_version: Optional[str] = Field(default=None, max_length=50)


class DeviceRegisterResponse(BaseModel):
    device_id: str
    quota: QuotaResponse


class BackupUploadRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=36)
    scope_type: str = Field(default="account", min_length=1, max_length=32)
    scope_id: Optional[str] = Field(default=None, max_length=64)
    snapshot_kind: str = Field(default="full", min_length=1, max_length=32)
    payload_size_bytes: int = Field(..., ge=0)
    payload_sha256: str = Field(..., min_length=1, max_length=64)
    encrypted: bool = True
    encryption_scheme: Optional[str] = Field(default=None, max_length=64)


class UploadSessionResponse(BaseModel):
    mode: str
    session_uri: str
    expires_at: Optional[str] = None


class BackupUploadResponse(BaseModel):
    snapshot_id: str
    bucket_name: str
    payload_object_key: str
    manifest_object_key: str
    upload: UploadSessionResponse


class BackupCompleteRequest(BaseModel):
    payload_size_bytes: int = Field(..., ge=0)
    payload_sha256: str = Field(..., min_length=1, max_length=64)
    counts: dict[str, Any] = Field(default_factory=dict)


class BackupCompleteResponse(BaseModel):
    ok: bool
    snapshot_id: str
    status: str


class LocalBackupCreateRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=36)
    scope_type: str = Field(default="account", min_length=1, max_length=32)
    scope_id: Optional[str] = Field(default=None, max_length=64)
    snapshot_kind: str = Field(default="full", min_length=1, max_length=32)


class LocalBackupCreateResponse(BaseModel):
    ok: bool
    snapshot_id: str
    status: str
    counts: dict[str, int]


class BackupListItem(BaseModel):
    snapshot_id: str
    device_id: str
    device_name: str
    scope_type: str
    snapshot_kind: str
    status: str
    payload_size_bytes: int
    created_at: Optional[str] = None


class BackupListResponse(BaseModel):
    items: list[BackupListItem]


class RestoreRequest(BaseModel):
    snapshot_id: str = Field(..., min_length=1, max_length=36)
    target_device_id: str = Field(..., min_length=1, max_length=36)


class RestoreDownloadResponse(BaseModel):
    mode: str
    url: str
    expires_at: Optional[str] = None


class RestoreRequestResponse(BaseModel):
    restore_job_id: str
    download: RestoreDownloadResponse


class RestoreCompleteRequest(BaseModel):
    ok: bool = True
    error_message: Optional[str] = None


class RestoreCompleteResponse(BaseModel):
    ok: bool
    restore_job_id: str
    status: str


class LocalRestoreRequest(BaseModel):
    snapshot_id: str = Field(..., min_length=1, max_length=36)
    merge: bool = False


class LocalRestoreResponse(BaseModel):
    ok: bool
    snapshot_id: str
    stats: dict[str, int]


class CloudUsageResponse(BaseModel):
    plan_code: str
    quota_bytes: int
    usage_bytes: int
    snapshot_count: int
    retention_days: int
    max_devices: int
    max_snapshots: int
    sync_enabled: bool = False
    device_transfer_enabled: bool = False


class SyncStateResponse(BaseModel):
    device_id: str
    auto_sync_enabled: bool
    sync_enabled: bool
    status: str
    can_push: bool
    can_pull: bool
    local_updated_at: Optional[str] = None
    last_sync_push_at: Optional[str] = None
    last_sync_pull_at: Optional[str] = None
    latest_device_snapshot_id: Optional[str] = None
    latest_device_snapshot_at: Optional[str] = None
    latest_remote_snapshot_id: Optional[str] = None
    latest_remote_snapshot_at: Optional[str] = None


class SyncActionRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=36)


class SyncPushResponse(BaseModel):
    ok: bool
    snapshot_id: str
    status: str
    counts: dict[str, int]


class SyncPullResponse(BaseModel):
    ok: bool
    snapshot_id: str
    stats: dict[str, int]


def _require_cloud_backup_enabled() -> None:
    settings = get_settings()
    if not settings.cloud_backup_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloud backup is disabled",
        )


def _build_service(db: Session) -> CloudBackupService:
    settings = get_settings()
    return CloudBackupService(db, settings=settings)


def _build_billing_service(db: Session) -> SyncBillingService:
    settings = get_settings()
    return SyncBillingService(db, settings=settings)


def _raise_entitlement_error(message: str, *, code: str, entitlement: dict[str, Any]) -> None:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "message": message,
            "code": code,
            "entitlement": entitlement,
        },
    )


def _require_cloud_entitlement(
    db: Session,
    user: User,
    *,
    require_cloud: bool = False,
    require_write: bool = False,
    require_restore: bool = False,
    require_sync: bool = False,
) -> dict[str, Any]:
    billing_service = _build_billing_service(db)
    entitlement = billing_service.resolve_entitlement(user)
    serialized = billing_service.serialize_entitlement(entitlement)

    if require_sync and not entitlement.can_use_sync:
        _raise_entitlement_error(
            "PigTex Sync subscription is required to use cloud sync",
            code="sync_upgrade_required",
            entitlement=serialized,
        )

    if require_cloud and not entitlement.can_use_cloud_backup:
        _raise_entitlement_error(
            "PigTex Sync subscription is required to use cloud backup",
            code="cloud_upgrade_required",
            entitlement=serialized,
        )

    if require_write and not entitlement.can_write_snapshots:
        code = "subscription_expired" if entitlement.status == "grace_period" else "cloud_upgrade_required"
        message = (
            "Your PigTex Sync subscription is in grace period. Renew to create new backups."
            if entitlement.status == "grace_period"
            else "PigTex Sync subscription is required to create or update cloud backups"
        )
        _raise_entitlement_error(message, code=code, entitlement=serialized)

    if require_restore and not entitlement.can_restore_snapshots:
        _raise_entitlement_error(
            "This account cannot restore cloud snapshots on the current plan",
            code="cloud_restore_unavailable",
            entitlement=serialized,
        )

    return serialized


@router.post("/devices/register", response_model=DeviceRegisterResponse)
async def register_device(
    payload: DeviceRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_write=True)
    service = _build_service(db)
    try:
        device, quota = service.register_device(
            current_user,
            device_key=payload.device_key,
            device_name=payload.device_name,
            platform=payload.platform,
            app_version=payload.app_version,
        )
    except CloudBackupQuotaExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return DeviceRegisterResponse(
        device_id=device.id,
        quota=QuotaResponse(
            plan_code=quota.plan_code,
            quota_bytes=int(quota.quota_bytes or 0),
            retention_days=int(quota.retention_days or 0),
            max_devices=int(quota.max_devices or 0),
            max_snapshots=int(quota.max_snapshots or 0),
            sync_enabled=bool(quota.sync_enabled),
            device_transfer_enabled=bool(quota.device_transfer_enabled),
        ),
    )


@router.post("/backups/upload-request", response_model=BackupUploadResponse)
async def request_backup_upload(
    payload: BackupUploadRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_write=True)
    service = _build_service(db)
    try:
        snapshot, session_uri = service.request_upload(
            current_user,
            device_id=payload.device_id,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            snapshot_kind=payload.snapshot_kind,
            payload_size_bytes=payload.payload_size_bytes,
            payload_sha256=payload.payload_sha256,
            encrypted=payload.encrypted,
            encryption_scheme=payload.encryption_scheme,
            payload_content_type="application/zip",
            request_id=getattr(request.state, "request_id", None),
        )
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudBackupQuotaExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CloudBackupValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CloudBackupConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return BackupUploadResponse(
        snapshot_id=snapshot.id,
        bucket_name=snapshot.bucket_name,
        payload_object_key=snapshot.payload_object_key,
        manifest_object_key=snapshot.manifest_object_key,
        upload=UploadSessionResponse(mode="presigned_put", session_uri=session_uri),
    )


@router.post("/backups/create-local", response_model=LocalBackupCreateResponse)
async def create_local_backup(
    payload: LocalBackupCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_write=True)
    service = _build_service(db)
    try:
        snapshot, counts = service.create_local_snapshot(
            current_user,
            device_id=payload.device_id,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            snapshot_kind=payload.snapshot_kind,
            request_id=getattr(request.state, "request_id", None),
        )
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudBackupQuotaExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CloudBackupValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CloudBackupConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return LocalBackupCreateResponse(
        ok=True,
        snapshot_id=snapshot.id,
        status=snapshot.status,
        counts=counts,
    )


@router.post("/backups/{snapshot_id}/complete", response_model=BackupCompleteResponse)
async def complete_backup_upload(
    snapshot_id: str,
    payload: BackupCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_write=True)
    service = _build_service(db)
    try:
        snapshot = service.complete_upload(
            current_user,
            snapshot_id=snapshot_id,
            payload_size_bytes=payload.payload_size_bytes,
            payload_sha256=payload.payload_sha256,
            counts=payload.counts,
        )
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudBackupValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CloudBackupConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return BackupCompleteResponse(ok=True, snapshot_id=snapshot.id, status=snapshot.status)


@router.get("/backups", response_model=BackupListResponse)
async def list_backups(
    scope_type: Optional[str] = None,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True)
    service = _build_service(db)
    snapshots = service.list_snapshots(current_user, scope_type=scope_type, limit=limit)
    items = [
        BackupListItem(
            snapshot_id=snapshot.id,
            device_id=snapshot.device_id,
            device_name=snapshot.device.device_name if snapshot.device else "Unknown device",
            scope_type=snapshot.scope_type,
            snapshot_kind=snapshot.snapshot_kind,
            status=snapshot.status,
            payload_size_bytes=int(snapshot.payload_size_bytes or 0),
            created_at=snapshot.created_at.isoformat() if snapshot.created_at else None,
        )
        for snapshot in snapshots
    ]
    return BackupListResponse(items=items)


@router.post("/restores/request", response_model=RestoreRequestResponse)
async def request_restore(
    payload: RestoreRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_restore=True)
    service = _build_service(db)
    try:
        job, download_url = service.request_restore(
            current_user,
            snapshot_id=payload.snapshot_id,
            target_device_id=payload.target_device_id,
        )
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudBackupConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return RestoreRequestResponse(
        restore_job_id=job.id,
        download=RestoreDownloadResponse(mode="signed_url", url=download_url),
    )


@router.post("/restores/{restore_job_id}/complete", response_model=RestoreCompleteResponse)
async def complete_restore(
    restore_job_id: str,
    payload: RestoreCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_restore=True)
    service = _build_service(db)
    try:
        job = service.complete_restore(
            current_user,
            restore_job_id=restore_job_id,
            ok=payload.ok,
            error_message=payload.error_message,
        )
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return RestoreCompleteResponse(ok=payload.ok, restore_job_id=job.id, status=job.status)


@router.post("/restores/apply-local", response_model=LocalRestoreResponse)
async def apply_local_restore(
    payload: LocalRestoreRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_restore=True)
    service = _build_service(db)
    try:
        stats = service.apply_snapshot_to_local(
            current_user,
            snapshot_id=payload.snapshot_id,
            merge=payload.merge,
        )
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudBackupConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return LocalRestoreResponse(ok=True, snapshot_id=payload.snapshot_id, stats=stats)


@router.get("/usage", response_model=CloudUsageResponse)
async def get_cloud_usage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    service = _build_service(db)
    usage = service.get_usage_summary(current_user)
    return CloudUsageResponse(**usage)


@router.get("/sync/state", response_model=SyncStateResponse)
async def get_sync_state(
    device_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_sync=True)
    service = _build_service(db)
    try:
        state = service.get_sync_state(current_user, device_id=device_id)
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SyncStateResponse(**state)


@router.post("/sync/push", response_model=SyncPushResponse)
async def push_sync_snapshot(
    payload: SyncActionRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_sync=True, require_write=True)
    service = _build_service(db)
    try:
        snapshot, counts = service.push_sync_snapshot(
            current_user,
            device_id=payload.device_id,
            request_id=getattr(request.state, "request_id", None),
        )
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudBackupQuotaExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CloudBackupValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CloudBackupConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return SyncPushResponse(ok=True, snapshot_id=snapshot.id, status=snapshot.status, counts=counts)


@router.post("/sync/pull", response_model=SyncPullResponse)
async def pull_sync_snapshot(
    payload: SyncActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_cloud_backup_enabled()
    _require_cloud_entitlement(db, current_user, require_cloud=True, require_sync=True, require_restore=True)
    service = _build_service(db)
    try:
        snapshot_id, stats = service.pull_latest_sync_snapshot(
            current_user,
            device_id=payload.device_id,
        )
    except CloudBackupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudBackupValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CloudBackupConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return SyncPullResponse(ok=True, snapshot_id=snapshot_id, stats=stats)
