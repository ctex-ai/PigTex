import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models import (
    CloudRestoreJob,
    CloudSnapshotManifest,
    CloudStorageQuota,
    SyncBillingCustomer,
    SyncBillingEvent,
    SyncBillingSubscription,
    User,
    UserDevice,
)
from app.services.cloud_backup_service import CloudBackupService
from app.services.cloud_backup_service import CloudBackupValidationError
from app.services.cloud_backup_service import MANAGED_BACKUP_ENCRYPTION_SCHEME


class FakeSpacesStorageService:
    def __init__(self) -> None:
        self.uploaded_json: list[tuple[str, str, dict]] = []
        self.uploaded_bytes: list[tuple[str, str, bytes, str]] = []
        self.json_objects: dict[tuple[str, str], dict] = {}
        self.byte_objects: dict[tuple[str, str], bytes] = {}

    def is_configured(self) -> bool:
        return True

    def create_presigned_upload_url(self, bucket_name: str, object_key: str, **_: object) -> str:
        return f"https://upload.example/{bucket_name}/{object_key}"

    def upload_json(self, bucket_name: str, object_key: str, payload: dict) -> None:
        self.uploaded_json.append((bucket_name, object_key, payload))
        self.json_objects[(bucket_name, object_key)] = payload

    def upload_bytes(
        self,
        bucket_name: str,
        object_key: str,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.uploaded_bytes.append((bucket_name, object_key, payload, content_type))
        self.byte_objects[(bucket_name, object_key)] = payload

    def download_json(self, bucket_name: str, object_key: str) -> dict:
        return self.json_objects[(bucket_name, object_key)]

    def download_bytes(self, bucket_name: str, object_key: str) -> bytes:
        return self.byte_objects[(bucket_name, object_key)]

    def generate_download_url(self, bucket_name: str, object_key: str) -> str:
        return f"https://download.example/{bucket_name}/{object_key}"


class CloudBackupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        SessionLocal = sessionmaker(bind=engine)
        Base.metadata.create_all(
            engine,
            tables=[
                User.__table__,
                UserDevice.__table__,
                CloudStorageQuota.__table__,
                CloudSnapshotManifest.__table__,
                CloudRestoreJob.__table__,
                SyncBillingCustomer.__table__,
                SyncBillingSubscription.__table__,
                SyncBillingEvent.__table__,
            ],
        )

        self.db = SessionLocal()
        self.user = User(
            id="user-1",
            email="owner@example.com",
            username="owner",
            name="Owner",
            plan="pro",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self.settings = Settings(
            cloud_backup_enabled=True,
            jwt_secret_key="test-secret-key-that-is-long-enough-123456",
            spaces_region="sgp1",
            spaces_endpoint_url="https://sgp1.digitaloceanspaces.com",
            spaces_bucket_backups="test-backups",
            spaces_access_key_id="spaces-key",
            spaces_secret_access_key="spaces-secret",
            spaces_signed_url_ttl_seconds=300,
            cloud_backup_default_quota_bytes=1024 * 1024,
            cloud_backup_default_retention_days=30,
            cloud_backup_default_max_devices=5,
            cloud_backup_default_max_snapshots=30,
            sync_plan_quota_bytes=1024 * 1024,
            sync_plan_retention_days=30,
            sync_plan_max_devices=5,
            sync_plan_max_snapshots=30,
        )
        self.fake_spaces = FakeSpacesStorageService()
        self.service = CloudBackupService(
            self.db,
            spaces_storage=self.fake_spaces,
            settings=self.settings,
        )

    def tearDown(self) -> None:
        self.db.close()

    def test_register_device_creates_quota(self) -> None:
        device, quota = self.service.register_device(
            self.user,
            device_key="desktop-1",
            device_name="Main Desktop",
            platform="windows",
            app_version="1.0.0",
        )

        self.assertEqual(device.user_id, self.user.id)
        self.assertEqual(device.device_name, "Main Desktop")
        self.assertEqual(quota.quota_bytes, 1024 * 1024)
        self.assertEqual(quota.plan_code, "sync")

    def test_request_upload_then_complete_creates_ready_snapshot(self) -> None:
        device, _ = self.service.register_device(
            self.user,
            device_key="desktop-1",
            device_name="Main Desktop",
            platform="windows",
            app_version="1.0.0",
        )

        snapshot, session_uri = self.service.request_upload(
            self.user,
            device_id=device.id,
            scope_type="account",
            scope_id=None,
            snapshot_kind="full",
            payload_size_bytes=4096,
            payload_sha256="abc123",
            encrypted=True,
            encryption_scheme="client-managed-v1",
            request_id="req-1",
        )

        self.assertEqual(snapshot.status, "uploading")
        self.assertIn(snapshot.payload_object_key, session_uri)

        completed = self.service.complete_upload(
            self.user,
            snapshot_id=snapshot.id,
            payload_size_bytes=4096,
            payload_sha256="abc123",
            counts={"workspaces": 2, "conversations": 3},
        )

        self.assertEqual(completed.status, "ready")
        self.assertTrue(self.fake_spaces.uploaded_json)
        _, _, manifest_payload = self.fake_spaces.uploaded_json[0]
        self.assertEqual(manifest_payload["snapshot_id"], snapshot.id)
        self.assertTrue(manifest_payload["encrypted"])
        self.assertEqual(json.loads(completed.counts_json)["workspaces"], 2)

    def test_request_restore_returns_download_url(self) -> None:
        device, _ = self.service.register_device(
            self.user,
            device_key="desktop-1",
            device_name="Main Desktop",
            platform="windows",
            app_version="1.0.0",
        )
        snapshot, _ = self.service.request_upload(
            self.user,
            device_id=device.id,
            scope_type="account",
            scope_id=None,
            snapshot_kind="full",
            payload_size_bytes=2048,
            payload_sha256="abc123",
            encrypted=True,
            encryption_scheme="client-managed-v1",
        )
        self.service.complete_upload(
            self.user,
            snapshot_id=snapshot.id,
            payload_size_bytes=2048,
            payload_sha256="abc123",
            counts={"messages": 10},
        )

        job, download_url = self.service.request_restore(
            self.user,
            snapshot_id=snapshot.id,
            target_device_id=device.id,
        )

        self.assertEqual(job.status, "requested")
        self.assertIn(snapshot.payload_object_key, download_url)

        job = self.service.complete_restore(
            self.user,
            restore_job_id=job.id,
            ok=True,
        )
        self.assertEqual(job.status, "completed")

    def test_request_upload_rejects_unencrypted_payloads(self) -> None:
        device, _ = self.service.register_device(
            self.user,
            device_key="desktop-1",
            device_name="Main Desktop",
            platform="windows",
            app_version="1.0.0",
        )

        with self.assertRaisesRegex(CloudBackupValidationError, "Encrypted cloud backups are required"):
            self.service.request_upload(
                self.user,
                device_id=device.id,
                scope_type="account",
                scope_id=None,
                snapshot_kind="full",
                payload_size_bytes=1024,
                payload_sha256="abc123",
                encrypted=False,
                encryption_scheme=None,
            )

    @patch("app.services.cloud_backup_service.LocalDatabase")
    def test_create_local_snapshot_encrypts_payload_and_restores_it(self, local_db_cls) -> None:
        exported_payload = {
            "workspaces": [{"id": "ws-1"}],
            "knowledge_items": [{"id": "know-1"}],
            "conversations": [{"id": "conv-1"}],
            "messages": [{"id": "msg-1"}],
            "facts": [],
            "preferences": [],
            "memory_assertions": [],
            "memory_evidence": [],
            "memory_pending_changes": [],
        }
        local_db_instance = local_db_cls.return_value
        local_db_instance.export_all_data.return_value = exported_payload
        local_db_instance.import_from_data.return_value = {"conversations": 1}

        device, _ = self.service.register_device(
            self.user,
            device_key="desktop-1",
            device_name="Main Desktop",
            platform="windows",
            app_version="1.0.0",
        )

        snapshot, counts = self.service.create_local_snapshot(
            self.user,
            device_id=device.id,
            scope_type="account",
            snapshot_kind="full",
        )

        self.assertEqual(snapshot.status, "ready")
        self.assertTrue(snapshot.encrypted)
        self.assertEqual(snapshot.encryption_scheme, MANAGED_BACKUP_ENCRYPTION_SCHEME)
        self.assertEqual(counts["conversations"], 1)
        self.assertTrue(self.fake_spaces.uploaded_bytes)

        _, _, encrypted_payload, content_type = self.fake_spaces.uploaded_bytes[0]
        self.assertEqual(content_type, "application/octet-stream")
        self.assertNotIn(b'"conversations"', encrypted_payload)

        restored = self.service.apply_snapshot_to_local(
            self.user,
            snapshot_id=snapshot.id,
            merge=False,
        )

        self.assertEqual(restored["conversations"], 1)
        local_db_instance.import_from_data.assert_called_once_with(exported_payload, merge=False)


if __name__ == "__main__":
    unittest.main()
