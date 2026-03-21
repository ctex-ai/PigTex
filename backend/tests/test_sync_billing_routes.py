import hashlib
import hmac
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
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
)
from app.routes.auth_utils import get_current_user
from app.routes.sync_billing import router
from app.services.sync_billing_service import SyncBillingService


class FakeHttpResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class SyncBillingRouteTests(unittest.TestCase):
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
            plan="free",
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

    def test_entitlement_defaults_to_free(self) -> None:
        settings = Settings(
            sync_billing_enabled=False,
            sync_billing_provider="mock",
        )

        with patch("app.routes.sync_billing.get_settings", return_value=settings):
            response = self.client.get("/api/billing/sync/entitlement")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan_code"], "free")
        self.assertFalse(response.json()["can_use_cloud_backup"])
        self.assertEqual(len(response.json()["plans"]), 2)

    def test_mock_checkout_activates_sync_subscription(self) -> None:
        settings = Settings(
            sync_billing_enabled=False,
            sync_billing_provider="mock",
            sync_plan_quota_bytes=2048,
            sync_plan_retention_days=30,
            sync_plan_max_devices=5,
            sync_plan_max_snapshots=64,
        )

        with patch("app.routes.sync_billing.get_settings", return_value=settings):
            checkout_response = self.client.post(
                "/api/billing/sync/checkout-session",
                json={
                    "plan_code": "sync",
                    "billing_cycle": "monthly",
                },
            )
            entitlement_response = self.client.get("/api/billing/sync/entitlement")

        self.assertEqual(checkout_response.status_code, 200)
        self.assertEqual(checkout_response.json()["mode"], "mock_activated")
        self.assertEqual(entitlement_response.status_code, 200)
        self.assertEqual(entitlement_response.json()["plan_code"], "sync")
        self.assertTrue(entitlement_response.json()["can_use_cloud_backup"])
        self.assertEqual(entitlement_response.json()["quota_bytes"], 2048)

    def test_payos_checkout_and_webhook_activate_subscription(self) -> None:
        settings = Settings(
            sync_billing_enabled=True,
            sync_billing_provider="payos",
            sync_payos_client_id="client-id",
            sync_payos_api_key="api-key",
            sync_payos_checksum_key="checksum-key",
            sync_plan_quota_bytes=4096,
            sync_plan_retention_days=30,
            sync_plan_max_devices=5,
            sync_plan_max_snapshots=64,
            sync_monthly_price_vnd=79000,
        )

        def fake_payos_request(method: str, url: str, **kwargs):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://api-merchant.payos.vn/v2/payment-requests")
            payload = kwargs.get("json") or {}
            self.assertEqual(payload.get("amount"), 79000)
            self.assertEqual(payload.get("buyerEmail"), "owner@example.com")
            return FakeHttpResponse(
                {
                    "code": "00",
                    "desc": "success",
                    "data": {
                        "paymentLinkId": "plink_1",
                        "checkoutUrl": "https://pay.payos.vn/web/checkout-1",
                        "status": "PENDING",
                    },
                }
            )

        with (
            patch("app.routes.sync_billing.get_settings", return_value=settings),
            patch("app.services.sync_billing_service.httpx.request", side_effect=fake_payos_request),
        ):
            checkout_response = self.client.post(
                "/api/billing/sync/checkout-session",
                json={
                    "plan_code": "sync",
                    "billing_cycle": "monthly",
                },
            )

        self.assertEqual(checkout_response.status_code, 200)
        self.assertEqual(checkout_response.json()["mode"], "redirect_pending_payment")
        self.assertEqual(checkout_response.json()["checkout_url"], "https://pay.payos.vn/web/checkout-1")

        subscription = (
            self.db.query(SyncBillingSubscription)
            .filter(SyncBillingSubscription.user_id == self.user.id)
            .first()
        )
        self.assertIsNotNone(subscription)
        assert subscription is not None
        self.assertEqual(subscription.provider, "payos")
        self.assertEqual(subscription.status, "pending")

        data = {
            "orderCode": int(subscription.provider_subscription_id),
            "paymentLinkId": "plink_1",
            "status": "PAID",
            "amount": 79000,
            "description": "PGX000001",
            "reference": "txn_1",
        }
        canonical = "&".join(f"{key}={data[key]}" for key in sorted(data.keys()))
        signature = hmac.new(
            settings.sync_payos_checksum_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        webhook_payload = {
            "code": "00",
            "desc": "success",
            "success": True,
            "data": data,
            "signature": signature,
        }

        with patch("app.routes.sync_billing.get_settings", return_value=settings):
            webhook_response = self.client.post("/api/billing/sync/webhook", json=webhook_payload)
            entitlement_response = self.client.get("/api/billing/sync/entitlement")

        self.assertEqual(webhook_response.status_code, 200)
        self.assertEqual(entitlement_response.status_code, 200)
        self.assertEqual(entitlement_response.json()["plan_code"], "sync")
        self.assertEqual(entitlement_response.json()["status"], "active")
        self.assertTrue(entitlement_response.json()["can_use_cloud_backup"])

    def test_payos_webhook_unknown_subscription_is_acknowledged(self) -> None:
        settings = Settings(
            sync_billing_enabled=True,
            sync_billing_provider="payos",
            sync_payos_client_id="client-id",
            sync_payos_api_key="api-key",
            sync_payos_checksum_key="checksum-key",
        )

        data = {
            "orderCode": 99999999,
            "paymentLinkId": "plink_probe",
            "status": "PENDING",
            "amount": 1000,
            "description": "Webhook URL probe",
            "reference": "probe_ref_1",
        }
        canonical = "&".join(f"{key}={data[key]}" for key in sorted(data.keys()))
        signature = hmac.new(
            settings.sync_payos_checksum_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        webhook_payload = {
            "code": "00",
            "desc": "success",
            "success": True,
            "data": data,
            "signature": signature,
        }

        with patch("app.routes.sync_billing.get_settings", return_value=settings):
            webhook_response = self.client.post("/api/billing/sync/webhook", json=webhook_payload)

        self.assertEqual(webhook_response.status_code, 200)
        event = (
            self.db.query(SyncBillingEvent)
            .filter(SyncBillingEvent.provider == "payos")
            .filter(SyncBillingEvent.provider_event_id == "probe_ref_1")
            .first()
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertTrue(event.signature_valid)
        self.assertTrue(event.processed)
        self.assertIn("Could not resolve PayOS subscription", event.error_message or "")

    def test_subscription_ordering_avoids_nulls_last_sql_for_mysql(self) -> None:
        service = SyncBillingService(self.db)
        statement = select(SyncBillingSubscription).order_by(*service._subscription_ordering())
        compiled = str(
            statement.compile(
                dialect=mysql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).upper()

        self.assertNotIn("NULLS LAST", compiled)
        self.assertIn("IS NULL", compiled)


if __name__ == "__main__":
    unittest.main()
