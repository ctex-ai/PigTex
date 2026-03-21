from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..local_storage import LocalDatabase
from ..models import (
    CloudRestoreJob,
    CloudSnapshotManifest,
    CloudStorageQuota,
    User,
    UserDevice,
)
from .spaces_storage import (
    SpacesStorageConfigError,
    SpacesStorageError,
    SpacesStorageService,
)
from .sync_billing_service import SyncBillingService


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


MANAGED_BACKUP_ENCRYPTION_SCHEME = "managed-fernet-v1"


class CloudBackupError(RuntimeError):
    """Base class for cloud backup service errors."""


class CloudBackupConfigError(CloudBackupError):
    """Raised when cloud backup cannot operate due to missing configuration."""


class CloudBackupNotFoundError(CloudBackupError):
    """Raised when a requested device, snapshot, or restore job is missing."""


class CloudBackupQuotaExceededError(CloudBackupError):
    """Raised when a request exceeds the user's configured cloud quota."""


class CloudBackupValidationError(CloudBackupError):
    """Raised when a backup payload or restore request violates security requirements."""


class CloudBackupService:
    """Service layer for cloud backup metadata, quota checks, and Spaces coordination."""

    def __init__(
        self,
        db: Session,
        *,
        spaces_storage: SpacesStorageService | None = None,
        settings: Settings | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.spaces_storage = spaces_storage or SpacesStorageService(settings=self.settings)

    def get_or_create_quota(self, user: User) -> CloudStorageQuota:
        quota, _ = SyncBillingService(self.db, settings=self.settings).materialize_quota(user)
        return quota

    def register_device(
        self,
        user: User,
        *,
        device_key: str,
        device_name: str,
        platform: str,
        app_version: str | None,
    ) -> tuple[UserDevice, CloudStorageQuota]:
        normalized_device_key = (device_key or "").strip()
        normalized_device_name = (device_name or "").strip()
        normalized_platform = (platform or "").strip().lower()
        normalized_app_version = (app_version or "").strip() or None

        if not normalized_device_key:
            raise ValueError("device_key is required")
        if not normalized_device_name:
            raise ValueError("device_name is required")
        if not normalized_platform:
            raise ValueError("platform is required")

        quota = self.get_or_create_quota(user)
        if int(quota.max_devices or 0) <= 0:
            raise CloudBackupQuotaExceededError("Your current PigTex plan does not include cloud devices")

        device = (
            self.db.query(UserDevice)
            .filter(
                UserDevice.user_id == user.id,
                UserDevice.device_key == normalized_device_key,
            )
            .first()
        )

        if device is None:
            device_count = (
                self.db.query(UserDevice)
                .filter(UserDevice.user_id == user.id)
                .count()
            )
            if device_count >= quota.max_devices:
                raise CloudBackupQuotaExceededError(
                    "Maximum devices reached for the current cloud backup quota"
                )

            device = UserDevice(
                id=str(uuid.uuid4()),
                user_id=user.id,
                device_key=normalized_device_key,
                device_name=normalized_device_name,
                platform=normalized_platform,
                app_version=normalized_app_version,
                last_seen_at=utcnow(),
            )
            self.db.add(device)
        else:
            device.device_name = normalized_device_name
            device.platform = normalized_platform
            device.app_version = normalized_app_version
            device.last_seen_at = utcnow()

        self.db.commit()
        self.db.refresh(device)
        return device, quota

    def calculate_usage_bytes(self, user_id: str) -> int:
        value = (
            self.db.query(func.coalesce(func.sum(CloudSnapshotManifest.payload_size_bytes), 0))
            .filter(
                CloudSnapshotManifest.user_id == user_id,
                CloudSnapshotManifest.status.in_(("upload_requested", "uploading", "ready")),
                CloudSnapshotManifest.is_counted_for_quota.is_(True),
            )
            .scalar()
        )
        return int(value or 0)

    def get_usage_summary(self, user: User) -> dict[str, int | str]:
        quota = self.get_or_create_quota(user)
        usage_bytes = self.calculate_usage_bytes(user.id)

        quota.usage_bytes_cached = usage_bytes
        self.db.commit()

        snapshot_count = (
            self.db.query(CloudSnapshotManifest)
            .filter(
                CloudSnapshotManifest.user_id == user.id,
                CloudSnapshotManifest.status != "deleted",
            )
            .count()
        )

        return {
            "plan_code": quota.plan_code,
            "quota_bytes": int(quota.quota_bytes or 0),
            "usage_bytes": usage_bytes,
            "retention_days": int(quota.retention_days or 0),
            "snapshot_count": snapshot_count,
            "max_devices": int(quota.max_devices or 0),
            "max_snapshots": int(quota.max_snapshots or 0),
            "sync_enabled": bool(quota.sync_enabled),
            "device_transfer_enabled": bool(quota.device_transfer_enabled),
        }

    def list_snapshots(
        self,
        user: User,
        *,
        scope_type: str | None = None,
        limit: int = 20,
    ) -> list[CloudSnapshotManifest]:
        query = (
            self.db.query(CloudSnapshotManifest)
            .filter(
                CloudSnapshotManifest.user_id == user.id,
                CloudSnapshotManifest.status != "deleted",
            )
            .order_by(CloudSnapshotManifest.created_at.desc())
            .limit(max(1, min(int(limit), 100)))
        )
        if scope_type:
            query = query.filter(CloudSnapshotManifest.scope_type == scope_type)
        return list(query.all())

    def create_local_snapshot(
        self,
        user: User,
        *,
        device_id: str,
        scope_type: str = "account",
        scope_id: str | None = None,
        snapshot_kind: str = "full",
        trigger_reason: str = "manual",
        request_id: str | None = None,
    ) -> tuple[CloudSnapshotManifest, dict[str, int]]:
        device = self._get_user_device(user.id, device_id)
        quota = self.get_or_create_quota(user)

        if not self.spaces_storage.is_configured():
            raise CloudBackupConfigError("Cloud backup storage is not configured")

        local_db = LocalDatabase(user.id)
        export_data = local_db.export_all_data()
        counts = self._calculate_export_counts(export_data)
        serialized_export = self._serialize_export_data(export_data)
        encrypted_payload = self._encrypt_export_payload(user.id, serialized_export)
        payload_size_bytes = len(encrypted_payload)

        usage_bytes = self.calculate_usage_bytes(user.id)
        if usage_bytes + payload_size_bytes > int(quota.quota_bytes or 0):
            raise CloudBackupQuotaExceededError("Cloud backup quota exceeded")
        self._ensure_snapshot_capacity(user.id, int(quota.max_snapshots or 0))

        snapshot = self._create_snapshot_record(
            user=user,
            device=device,
            scope_type=scope_type,
            scope_id=scope_id,
            snapshot_kind=snapshot_kind,
            trigger_reason=trigger_reason,
            payload_size_bytes=payload_size_bytes,
            payload_sha256=self._compute_bytes_sha256(encrypted_payload),
            encrypted=True,
            encryption_scheme=MANAGED_BACKUP_ENCRYPTION_SCHEME,
            request_id=request_id,
            payload_content_type="application/octet-stream",
        )

        try:
            self.spaces_storage.upload_bytes(
                snapshot.bucket_name,
                snapshot.payload_object_key,
                encrypted_payload,
                content_type="application/octet-stream",
            )
            snapshot.counts_json = json.dumps(counts, ensure_ascii=True, separators=(",", ":"))
            self.spaces_storage.upload_json(
                snapshot.bucket_name,
                snapshot.manifest_object_key,
                self._build_manifest_payload(snapshot, counts),
            )
        except (SpacesStorageConfigError, SpacesStorageError) as exc:
            snapshot.status = "failed"
            snapshot.failed_at = utcnow()
            self.db.commit()
            raise CloudBackupConfigError(str(exc)) from exc

        snapshot.status = "ready"
        snapshot.failed_at = None
        snapshot.updated_at = utcnow()
        device.last_backup_at = utcnow()
        if trigger_reason in {"sync", "auto_sync"}:
            device.last_sync_push_at = utcnow()
        quota.usage_bytes_cached = self.calculate_usage_bytes(user.id)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot, counts

    def apply_snapshot_to_local(
        self,
        user: User,
        *,
        snapshot_id: str,
        merge: bool = False,
    ) -> dict[str, int]:
        snapshot = self._get_snapshot(user.id, snapshot_id)
        if snapshot.status != "ready":
            raise CloudBackupNotFoundError("Snapshot is not ready for restore")

        try:
            export_data = self._load_snapshot_export_data(user.id, snapshot)
        except (SpacesStorageConfigError, SpacesStorageError) as exc:
            raise CloudBackupConfigError(str(exc)) from exc

        local_db = LocalDatabase(user.id)
        return local_db.import_from_data(export_data, merge=merge)

    def request_upload(
        self,
        user: User,
        *,
        device_id: str,
        scope_type: str,
        scope_id: str | None,
        snapshot_kind: str,
        trigger_reason: str = "manual",
        payload_size_bytes: int,
        payload_sha256: str,
        encrypted: bool,
        encryption_scheme: str | None,
        payload_content_type: str = "application/zip",
        request_id: str | None = None,
    ) -> tuple[CloudSnapshotManifest, str]:
        device = self._get_user_device(user.id, device_id)
        quota = self.get_or_create_quota(user)

        if not self.spaces_storage.is_configured():
            raise CloudBackupConfigError("Cloud backup storage is not configured")

        requested_size = max(0, int(payload_size_bytes))
        usage_bytes = self.calculate_usage_bytes(user.id)
        if usage_bytes + requested_size > int(quota.quota_bytes or 0):
            raise CloudBackupQuotaExceededError("Cloud backup quota exceeded")
        self._ensure_snapshot_capacity(user.id, int(quota.max_snapshots or 0))
        if not encrypted:
            raise CloudBackupValidationError("Encrypted cloud backups are required")
        normalized_encryption_scheme = (encryption_scheme or "").strip()
        if not normalized_encryption_scheme:
            raise CloudBackupValidationError("encryption_scheme is required for cloud backups")

        snapshot = self._create_snapshot_record(
            user=user,
            device=device,
            scope_type=scope_type,
            scope_id=scope_id,
            snapshot_kind=snapshot_kind,
            trigger_reason=trigger_reason,
            payload_size_bytes=requested_size,
            payload_sha256=payload_sha256,
            encrypted=True,
            encryption_scheme=normalized_encryption_scheme,
            request_id=request_id,
            payload_content_type=payload_content_type,
        )

        try:
            session_uri = self.spaces_storage.create_presigned_upload_url(
                snapshot.bucket_name,
                snapshot.payload_object_key,
                content_type=payload_content_type,
                size=requested_size if requested_size > 0 else None,
            )
        except (SpacesStorageConfigError, SpacesStorageError) as exc:
            snapshot.status = "failed"
            snapshot.failed_at = utcnow()
            self.db.commit()
            raise CloudBackupConfigError(str(exc)) from exc

        snapshot.status = "uploading"
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot, session_uri

    def complete_upload(
        self,
        user: User,
        *,
        snapshot_id: str,
        payload_size_bytes: int,
        payload_sha256: str,
        counts: dict[str, Any] | None,
    ) -> CloudSnapshotManifest:
        snapshot = self._get_snapshot(user.id, snapshot_id)

        snapshot.payload_size_bytes = max(0, int(payload_size_bytes))
        snapshot.payload_sha256 = (payload_sha256 or "").strip()
        snapshot.counts_json = json.dumps(counts or {}, ensure_ascii=True, separators=(",", ":"))

        manifest_payload = self._build_manifest_payload(snapshot, counts or {})

        try:
            self.spaces_storage.upload_json(
                snapshot.bucket_name,
                snapshot.manifest_object_key,
                manifest_payload,
            )
        except (SpacesStorageConfigError, SpacesStorageError) as exc:
            snapshot.status = "failed"
            snapshot.failed_at = utcnow()
            self.db.commit()
            raise CloudBackupConfigError(str(exc)) from exc

        snapshot.status = "ready"
        snapshot.failed_at = None
        snapshot.updated_at = utcnow()
        if snapshot.device is not None:
            snapshot.device.last_backup_at = utcnow()
            if snapshot.trigger_reason in {"sync", "auto_sync"}:
                snapshot.device.last_sync_push_at = utcnow()

        usage_bytes = self.calculate_usage_bytes(user.id)
        quota = self.get_or_create_quota(user)
        quota.usage_bytes_cached = usage_bytes

        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def request_restore(
        self,
        user: User,
        *,
        snapshot_id: str,
        target_device_id: str,
    ) -> tuple[CloudRestoreJob, str]:
        snapshot = self._get_snapshot(user.id, snapshot_id)
        if snapshot.status != "ready":
            raise CloudBackupNotFoundError("Snapshot is not ready for restore")

        target_device = self._get_user_device(user.id, target_device_id)

        try:
            download_url = self.spaces_storage.generate_download_url(
                snapshot.bucket_name,
                snapshot.payload_object_key,
            )
        except (SpacesStorageConfigError, SpacesStorageError) as exc:
            raise CloudBackupConfigError(str(exc)) from exc

        job = CloudRestoreJob(
            id=str(uuid.uuid4()),
            user_id=user.id,
            snapshot_id=snapshot.id,
            target_device_id=target_device.id,
            status="requested",
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job, download_url

    def complete_restore(
        self,
        user: User,
        *,
        restore_job_id: str,
        ok: bool,
        error_message: str | None = None,
    ) -> CloudRestoreJob:
        job = (
            self.db.query(CloudRestoreJob)
            .filter(
                CloudRestoreJob.id == restore_job_id,
                CloudRestoreJob.user_id == user.id,
            )
            .first()
        )
        if job is None:
            raise CloudBackupNotFoundError("Restore job not found")

        if job.started_at is None:
            job.started_at = utcnow()
        job.status = "completed" if ok else "failed"
        job.error_message = None if ok else (error_message or "Restore failed")
        job.error_code = None if ok else "restore_failed"
        job.completed_at = utcnow()
        if ok and job.target_device is not None:
            job.target_device.last_restore_at = utcnow()

        self.db.commit()
        self.db.refresh(job)
        return job

    def get_sync_state(self, user: User, *, device_id: str) -> dict[str, Any]:
        device = self._get_user_device(user.id, device_id)
        quota = self.get_or_create_quota(user)
        local_db = LocalDatabase(user.id)
        local_updated_at = local_db.get_latest_update_at()
        latest_device_snapshot = self._get_latest_ready_snapshot(user.id, device_id=device.id)
        latest_remote_snapshot = self._get_latest_ready_snapshot(user.id, exclude_device_id=device.id)

        last_push_at = device.last_sync_push_at
        last_pull_at = device.last_sync_pull_at
        push_needed = bool(
            quota.sync_enabled
            and quota.quota_bytes > 0
            and local_updated_at
            and (
                last_push_at is None
                or local_updated_at > last_push_at
                or (
                    latest_device_snapshot is not None
                    and latest_device_snapshot.created_at is not None
                    and local_updated_at > latest_device_snapshot.created_at
                )
            )
        )
        pull_needed = bool(
            quota.sync_enabled
            and latest_remote_snapshot is not None
            and latest_remote_snapshot.created_at is not None
            and (
                last_pull_at is None
                or latest_remote_snapshot.created_at > last_pull_at
            )
        )

        if push_needed and pull_needed:
            status = "bidirectional"
        elif push_needed:
            status = "push_needed"
        elif pull_needed:
            status = "pull_needed"
        else:
            status = "idle"

        return {
            "device_id": device.id,
            "auto_sync_enabled": bool(device.auto_sync_enabled),
            "sync_enabled": bool(quota.sync_enabled),
            "status": status,
            "can_push": bool(quota.sync_enabled and quota.quota_bytes > 0),
            "can_pull": bool(quota.sync_enabled),
            "local_updated_at": local_updated_at.isoformat() if local_updated_at else None,
            "last_sync_push_at": device.last_sync_push_at.isoformat() if device.last_sync_push_at else None,
            "last_sync_pull_at": device.last_sync_pull_at.isoformat() if device.last_sync_pull_at else None,
            "latest_device_snapshot_id": latest_device_snapshot.id if latest_device_snapshot else None,
            "latest_device_snapshot_at": (
                latest_device_snapshot.created_at.isoformat()
                if latest_device_snapshot and latest_device_snapshot.created_at
                else None
            ),
            "latest_remote_snapshot_id": latest_remote_snapshot.id if latest_remote_snapshot else None,
            "latest_remote_snapshot_at": (
                latest_remote_snapshot.created_at.isoformat()
                if latest_remote_snapshot and latest_remote_snapshot.created_at
                else None
            ),
        }

    def push_sync_snapshot(
        self,
        user: User,
        *,
        device_id: str,
        request_id: str | None = None,
    ) -> tuple[CloudSnapshotManifest, dict[str, int]]:
        return self.create_local_snapshot(
            user,
            device_id=device_id,
            scope_type="account",
            scope_id=None,
            snapshot_kind="sync",
            trigger_reason="sync",
            request_id=request_id,
        )

    def pull_latest_sync_snapshot(
        self,
        user: User,
        *,
        device_id: str,
    ) -> tuple[str, dict[str, int]]:
        device = self._get_user_device(user.id, device_id)
        snapshot = self._get_latest_ready_snapshot(user.id, exclude_device_id=device.id)
        if snapshot is None:
            raise CloudBackupNotFoundError("No remote sync snapshot is available")

        stats = self.apply_snapshot_to_local(
            user,
            snapshot_id=snapshot.id,
            merge=True,
        )
        device.last_sync_pull_at = utcnow()
        device.last_restore_at = utcnow()
        self.db.commit()
        return snapshot.id, stats

    def _build_snapshot_keys(
        self,
        user_id: str,
        snapshot_id: str,
        *,
        payload_content_type: str = "application/zip",
    ) -> tuple[str, str]:
        base_key = f"users/{user_id}/snapshots/{snapshot_id}"
        normalized_content_type = (payload_content_type or "application/zip").strip().lower()
        if normalized_content_type == "application/json":
            payload_extension = "json"
        elif normalized_content_type == "application/octet-stream":
            payload_extension = "bin"
        else:
            payload_extension = "zip"
        return f"{base_key}/manifest.json", f"{base_key}/payload.{payload_extension}"

    def _build_manifest_payload(
        self,
        snapshot: CloudSnapshotManifest,
        counts: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = self._load_snapshot_metadata(snapshot)
        return {
            "snapshot_id": snapshot.id,
            "user_id": snapshot.user_id,
            "device_id": snapshot.device_id,
            "scope_type": snapshot.scope_type,
            "scope_id": snapshot.scope_id,
            "snapshot_kind": snapshot.snapshot_kind,
            "manifest_version": snapshot.manifest_version,
            "payload_size_bytes": int(snapshot.payload_size_bytes or 0),
            "payload_sha256": snapshot.payload_sha256,
            "payload_content_type": metadata.get("payload_content_type", "application/zip"),
            "storage_class": snapshot.storage_class,
            "bucket_name": snapshot.bucket_name,
            "payload_object_key": snapshot.payload_object_key,
            "encrypted": bool(snapshot.encrypted),
            "encryption_scheme": snapshot.encryption_scheme,
            "trigger_reason": snapshot.trigger_reason,
            "counts": counts,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
            "updated_at": utcnow().isoformat(),
        }

    def _create_snapshot_record(
        self,
        *,
        user: User,
        device: UserDevice,
        scope_type: str,
        scope_id: str | None,
        snapshot_kind: str,
        trigger_reason: str,
        payload_size_bytes: int,
        payload_sha256: str,
        encrypted: bool,
        encryption_scheme: str | None,
        request_id: str | None,
        payload_content_type: str,
    ) -> CloudSnapshotManifest:
        quota = self.get_or_create_quota(user)
        snapshot_id = str(uuid.uuid4())
        normalized_payload_content_type = (
            (payload_content_type or "application/zip").strip() or "application/zip"
        )
        manifest_object_key, payload_object_key = self._build_snapshot_keys(
            user.id,
            snapshot_id,
            payload_content_type=normalized_payload_content_type,
        )
        expires_at = utcnow() + timedelta(days=max(1, int(quota.retention_days or 1)))
        metadata_payload: dict[str, Any] = {
            "payload_content_type": normalized_payload_content_type
        }
        if request_id:
            metadata_payload["request_id"] = request_id

        snapshot = CloudSnapshotManifest(
            id=snapshot_id,
            user_id=user.id,
            device_id=device.id,
            scope_type=(scope_type or "account").strip() or "account",
            scope_id=(scope_id or "").strip() or None,
            snapshot_kind=(snapshot_kind or "full").strip() or "full",
            status="upload_requested",
            manifest_version=1,
            bucket_name=(self.settings.spaces_bucket_backups or "").strip(),
            manifest_object_key=manifest_object_key,
            payload_object_key=payload_object_key,
            storage_class="STANDARD",
            payload_size_bytes=max(0, int(payload_size_bytes)),
            payload_sha256=(payload_sha256 or "").strip(),
            encrypted=bool(encrypted),
            encryption_scheme=(encryption_scheme or "").strip() or None,
            trigger_reason=(trigger_reason or "manual").strip() or "manual",
            is_counted_for_quota=True,
            metadata_json=json.dumps(metadata_payload, ensure_ascii=True, separators=(",", ":")),
            expires_at=expires_at,
        )
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def _load_snapshot_metadata(self, snapshot: CloudSnapshotManifest) -> dict[str, Any]:
        raw = snapshot.metadata_json or ""
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _calculate_export_counts(self, export_data: dict[str, Any]) -> dict[str, int]:
        return {
            "workspaces": len(export_data.get("workspaces", [])),
            "knowledge_items": len(export_data.get("knowledge_items", [])),
            "conversations": len(export_data.get("conversations", [])),
            "messages": len(export_data.get("messages", [])),
            "facts": len(export_data.get("facts", [])),
            "preferences": len(export_data.get("preferences", [])),
            "memory_assertions": len(export_data.get("memory_assertions", [])),
            "memory_evidence": len(export_data.get("memory_evidence", [])),
            "memory_pending_changes": len(export_data.get("memory_pending_changes", [])),
        }

    def _serialize_export_data(self, payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def _build_managed_backup_fernet(self, user_id: str) -> Fernet:
        secret = (self.settings.jwt_secret_key or "").strip().encode("utf-8")
        if not secret:
            raise CloudBackupConfigError("JWT secret key is required to encrypt managed backups")
        digest = hmac.new(secret, user_id.encode("utf-8"), hashlib.sha256).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def _encrypt_export_payload(self, user_id: str, payload: bytes) -> bytes:
        return self._build_managed_backup_fernet(user_id).encrypt(payload)

    def _decrypt_export_payload(self, user_id: str, payload: bytes) -> dict[str, Any]:
        try:
            decrypted = self._build_managed_backup_fernet(user_id).decrypt(payload)
        except InvalidToken as exc:
            raise CloudBackupValidationError("Managed backup payload could not be decrypted") from exc

        try:
            parsed = json.loads(decrypted.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CloudBackupValidationError("Managed backup payload is not valid JSON") from exc

        if not isinstance(parsed, dict):
            raise CloudBackupValidationError("Managed backup payload is not a valid export object")

        return parsed

    def _load_snapshot_export_data(self, user_id: str, snapshot: CloudSnapshotManifest) -> dict[str, Any]:
        metadata = self._load_snapshot_metadata(snapshot)
        payload_content_type = (metadata.get("payload_content_type") or "application/zip").strip().lower()

        if snapshot.encrypted:
            if snapshot.encryption_scheme != MANAGED_BACKUP_ENCRYPTION_SCHEME:
                raise CloudBackupValidationError(
                    "Server-side local restore only supports managed encrypted snapshots"
                )
            encrypted_payload = self.spaces_storage.download_bytes(
                snapshot.bucket_name,
                snapshot.payload_object_key,
            )
            return self._decrypt_export_payload(user_id, encrypted_payload)

        if payload_content_type != "application/json":
            raise CloudBackupValidationError(
                "Server-side local restore only supports JSON snapshots"
            )

        return self.spaces_storage.download_json(
            snapshot.bucket_name,
            snapshot.payload_object_key,
        )

    def _compute_bytes_sha256(self, payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def _compute_json_sha256(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def _get_user_device(self, user_id: str, device_id: str) -> UserDevice:
        device = (
            self.db.query(UserDevice)
            .filter(
                UserDevice.id == device_id,
                UserDevice.user_id == user_id,
            )
            .first()
        )
        if device is None:
            raise CloudBackupNotFoundError("Device not found")
        return device

    def _get_snapshot(self, user_id: str, snapshot_id: str) -> CloudSnapshotManifest:
        snapshot = (
            self.db.query(CloudSnapshotManifest)
            .filter(
                CloudSnapshotManifest.id == snapshot_id,
                CloudSnapshotManifest.user_id == user_id,
            )
            .first()
        )
        if snapshot is None:
            raise CloudBackupNotFoundError("Snapshot not found")
        return snapshot

    def _get_latest_ready_snapshot(
        self,
        user_id: str,
        *,
        device_id: str | None = None,
        exclude_device_id: str | None = None,
    ) -> CloudSnapshotManifest | None:
        query = (
            self.db.query(CloudSnapshotManifest)
            .filter(
                CloudSnapshotManifest.user_id == user_id,
                CloudSnapshotManifest.status == "ready",
            )
            .order_by(CloudSnapshotManifest.created_at.desc())
        )
        if device_id:
            query = query.filter(CloudSnapshotManifest.device_id == device_id)
        if exclude_device_id:
            query = query.filter(CloudSnapshotManifest.device_id != exclude_device_id)
        return query.first()

    def _ensure_snapshot_capacity(self, user_id: str, max_snapshots: int) -> None:
        normalized_cap = max(0, int(max_snapshots or 0))
        if normalized_cap <= 0:
            raise CloudBackupQuotaExceededError("Your current PigTex plan does not include cloud snapshots")

        snapshot_count = (
            self.db.query(CloudSnapshotManifest)
            .filter(
                CloudSnapshotManifest.user_id == user_id,
                CloudSnapshotManifest.status != "deleted",
            )
            .count()
        )
        if snapshot_count >= normalized_cap:
            raise CloudBackupQuotaExceededError("Cloud snapshot limit reached for the current plan")
