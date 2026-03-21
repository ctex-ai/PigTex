import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base, get_db
from app.models import (
    CloudSnapshotManifest,
    CloudStorageQuota,
    SyncBillingCustomer,
    SyncBillingEvent,
    SyncBillingSubscription,
    User,
    UserDevice,
)
from app.routes.auth_utils import get_current_user
from app.routes.cloud_backup import router
from app.services.cloud_backup_service import CloudBackupService


class CloudBackupRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        Base.metadata.create_all(
            engine,
            tables=[
                User.__table__,
                UserDevice.__table__,
                CloudStorageQuota.__table__,
                CloudSnapshotManifest.__table__,
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

        app = FastAPI()
        app.include_router(router, prefix="/api")

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: self.user

        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.db.close()

    def test_usage_route_returns_quota_summary(self) -> None:
        settings = Settings(
            cloud_backup_enabled=True,
            cloud_backup_default_quota_bytes=2048,
            cloud_backup_default_retention_days=30,
            cloud_backup_default_max_devices=5,
            cloud_backup_default_max_snapshots=30,
            sync_plan_quota_bytes=2048,
            sync_plan_retention_days=30,
            sync_plan_max_devices=5,
            sync_plan_max_snapshots=30,
        )
        CloudBackupService(self.db, settings=settings).get_or_create_quota(self.user)

        with patch("app.routes.cloud_backup.get_settings", return_value=settings):
            response = self.client.get("/api/cloud/usage")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["quota_bytes"], 2048)
        self.assertEqual(response.json()["usage_bytes"], 0)
        self.assertTrue(response.json()["sync_enabled"])

    def test_usage_route_returns_503_when_disabled(self) -> None:
        disabled_settings = Settings(cloud_backup_enabled=False)

        with patch("app.routes.cloud_backup.get_settings", return_value=disabled_settings):
            response = self.client.get("/api/cloud/usage")

        self.assertEqual(response.status_code, 503)
        self.assertIn("disabled", response.json()["detail"].lower())

    def test_upload_request_rejects_unencrypted_payloads(self) -> None:
        settings = Settings(
            cloud_backup_enabled=True,
            spaces_region="sgp1",
            spaces_endpoint_url="https://sgp1.digitaloceanspaces.com",
            spaces_bucket_backups="test-backups",
            spaces_access_key_id="spaces-key",
            spaces_secret_access_key="spaces-secret",
        )
        service = CloudBackupService(self.db, settings=settings)
        device, _ = service.register_device(
            self.user,
            device_key="desktop-1",
            device_name="Main Desktop",
            platform="windows",
            app_version="1.0.0",
        )

        with patch("app.routes.cloud_backup.get_settings", return_value=settings):
            response = self.client.post(
                "/api/cloud/backups/upload-request",
                json={
                    "device_id": device.id,
                    "scope_type": "account",
                    "snapshot_kind": "full",
                    "payload_size_bytes": 1024,
                    "payload_sha256": "abc123",
                    "encrypted": False,
                    "encryption_scheme": None,
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("encrypted cloud backups are required", response.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
