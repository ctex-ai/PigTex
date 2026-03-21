from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import case
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import (
    CloudStorageQuota,
    SyncBillingCustomer,
    SyncBillingEvent,
    SyncBillingSubscription,
    User,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}
GRACE_SUBSCRIPTION_STATUSES = {"past_due", "canceled", "unpaid", "incomplete_expired", "expired"}
PENDING_SUBSCRIPTION_STATUSES = {"pending", "created", "awaiting_payment"}
LEGACY_PLAN_MAP = {
    "basic": "sync",
    "pro": "sync",
    "unlimited": "sync_plus",
}


class SyncBillingError(RuntimeError):
    """Base class for PigTex Sync billing failures."""


class SyncBillingConfigError(SyncBillingError):
    """Raised when billing provider configuration is incomplete."""


class SyncBillingValidationError(SyncBillingError):
    """Raised when a request payload or provider response is invalid."""


class SyncBillingNotFoundError(SyncBillingError):
    """Raised when a customer or subscription cannot be found."""


@dataclass(frozen=True)
class SyncPlanDefinition:
    code: str
    name: str
    quota_bytes: int
    retention_days: int
    max_devices: int
    max_snapshots: int
    sync_enabled: bool
    device_transfer_enabled: bool
    priority_level: int
    monthly_price_vnd: int
    annual_price_vnd: int


@dataclass(frozen=True)
class SyncEntitlement:
    plan_code: str
    plan_name: str
    status: str
    subscription_status: str
    billing_cycle: str | None
    quota_bytes: int
    retention_days: int
    max_devices: int
    max_snapshots: int
    can_use_cloud_backup: bool
    can_use_device_transfer: bool
    can_use_sync: bool
    can_write_snapshots: bool
    can_restore_snapshots: bool
    priority_level: int
    quota_source: str
    cancel_at_period_end: bool
    current_period_start: datetime | None
    current_period_end: datetime | None
    grace_ends_at: datetime | None


@dataclass(frozen=True)
class CheckoutSessionResult:
    session_id: str
    checkout_url: str | None
    mode: str


@dataclass(frozen=True)
class PortalSessionResult:
    session_url: str | None
    mode: str


class SyncBillingService:
    """Resolve PigTex Sync entitlements and broker subscription lifecycle."""

    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    def get_plan_catalog(self) -> dict[str, SyncPlanDefinition]:
        return {
            "free": SyncPlanDefinition(
                code="free",
                name="Free",
                quota_bytes=0,
                retention_days=0,
                max_devices=0,
                max_snapshots=0,
                sync_enabled=False,
                device_transfer_enabled=False,
                priority_level=0,
                monthly_price_vnd=0,
                annual_price_vnd=0,
            ),
            "sync": SyncPlanDefinition(
                code="sync",
                name="PigTex Sync",
                quota_bytes=max(0, int(self.settings.sync_plan_quota_bytes or 0)),
                retention_days=max(1, int(self.settings.sync_plan_retention_days or 30)),
                max_devices=max(1, int(self.settings.sync_plan_max_devices or 5)),
                max_snapshots=max(1, int(self.settings.sync_plan_max_snapshots or 64)),
                sync_enabled=True,
                device_transfer_enabled=True,
                priority_level=1,
                monthly_price_vnd=max(0, int(self.settings.sync_monthly_price_vnd or 0)),
                annual_price_vnd=max(0, int(self.settings.sync_annual_price_vnd or 0)),
            ),
            "sync_plus": SyncPlanDefinition(
                code="sync_plus",
                name="PigTex Sync Plus",
                quota_bytes=max(0, int(self.settings.sync_plus_quota_bytes or 0)),
                retention_days=max(1, int(self.settings.sync_plus_retention_days or 180)),
                max_devices=max(1, int(self.settings.sync_plus_max_devices or 10)),
                max_snapshots=max(1, int(self.settings.sync_plus_max_snapshots or 256)),
                sync_enabled=True,
                device_transfer_enabled=True,
                priority_level=2,
                monthly_price_vnd=max(0, int(self.settings.sync_plus_monthly_price_vnd or 0)),
                annual_price_vnd=max(0, int(self.settings.sync_plus_annual_price_vnd or 0)),
            ),
        }

    def list_plan_offers(self) -> list[dict[str, Any]]:
        catalog = self.get_plan_catalog()
        offers: list[dict[str, Any]] = []
        for code in ("sync", "sync_plus"):
            plan = catalog[code]
            offers.append(
                {
                    "plan_code": plan.code,
                    "name": plan.name,
                    "quota_bytes": plan.quota_bytes,
                    "retention_days": plan.retention_days,
                    "max_devices": plan.max_devices,
                    "max_snapshots": plan.max_snapshots,
                    "monthly_price_vnd": plan.monthly_price_vnd,
                    "annual_price_vnd": plan.annual_price_vnd,
                    "sync_enabled": plan.sync_enabled,
                    "device_transfer_enabled": plan.device_transfer_enabled,
                    "priority_level": plan.priority_level,
                }
            )
        return offers

    def normalize_plan_code(self, value: str | None) -> str:
        normalized = (value or "free").strip().lower()
        normalized = LEGACY_PLAN_MAP.get(normalized, normalized)
        if normalized in self.get_plan_catalog():
            return normalized
        return "free"

    def _normalize_billing_cycle(self, value: str | None) -> str:
        normalized = (value or "monthly").strip().lower()
        if normalized not in {"monthly", "annual"}:
            raise SyncBillingValidationError("billing_cycle must be monthly or annual")
        return normalized

    def _get_plan_definition(self, plan_code: str | None) -> SyncPlanDefinition:
        normalized = self.normalize_plan_code(plan_code)
        return self.get_plan_catalog()[normalized]

    def _serialize_dt(self, value: datetime | None) -> str | None:
        normalized = self._ensure_utc(value)
        return normalized.isoformat() if normalized else None

    def _ensure_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _subscription_ordering(self) -> tuple[Any, ...]:
        return (
            case(
                (SyncBillingSubscription.current_period_end.is_(None), 1),
                else_=0,
            ).asc(),
            SyncBillingSubscription.current_period_end.desc(),
            SyncBillingSubscription.updated_at.desc(),
            SyncBillingSubscription.created_at.desc(),
        )

    def get_current_subscription(self, user: User) -> SyncBillingSubscription | None:
        subscriptions = (
            self.db.query(SyncBillingSubscription)
            .filter(SyncBillingSubscription.user_id == user.id)
            .order_by(*self._subscription_ordering())
            .all()
        )
        if not subscriptions:
            return None

        def rank(subscription: SyncBillingSubscription) -> tuple[int, float]:
            status = (subscription.status or "").strip().lower()
            if status in ACTIVE_SUBSCRIPTION_STATUSES:
                priority = 0
            elif self._is_subscription_in_grace(subscription):
                priority = 1
            else:
                priority = 2
            normalized_end = self._ensure_utc(subscription.current_period_end)
            end_value = normalized_end.timestamp() if normalized_end else 0.0
            return (priority, -end_value)

        return sorted(subscriptions, key=rank)[0]

    def _is_subscription_active(self, subscription: SyncBillingSubscription | None) -> bool:
        if subscription is None:
            return False
        status = (subscription.status or "").strip().lower()
        if status not in ACTIVE_SUBSCRIPTION_STATUSES:
            return False
        current_period_end = self._ensure_utc(subscription.current_period_end)
        if current_period_end and current_period_end < utcnow():
            return False
        return True

    def _is_subscription_in_grace(self, subscription: SyncBillingSubscription | None) -> bool:
        if subscription is None:
            return False
        grace_ends_at = self._ensure_utc(subscription.grace_ends_at)
        if not grace_ends_at:
            return False
        return grace_ends_at >= utcnow()

    def resolve_entitlement(self, user: User) -> SyncEntitlement:
        subscription = self.get_current_subscription(user)
        if (
            subscription is not None
            and subscription.provider == "payos"
            and (subscription.status or "").strip().lower() in PENDING_SUBSCRIPTION_STATUSES
            and self.settings.sync_billing_provider == "payos"
            and self.settings.sync_billing_enabled
        ):
            subscription = self._sync_payos_subscription_state(subscription)
        quota_source = "free"

        if self._is_subscription_active(subscription):
            assert subscription is not None
            plan = self._get_plan_definition(subscription.plan_code)
            quota_source = "subscription"
            return SyncEntitlement(
                plan_code=plan.code,
                plan_name=plan.name,
                status="active",
                subscription_status=(subscription.status or "active").strip().lower() or "active",
                billing_cycle=(subscription.billing_cycle or "monthly").strip().lower() or "monthly",
                quota_bytes=plan.quota_bytes,
                retention_days=plan.retention_days,
                max_devices=plan.max_devices,
                max_snapshots=plan.max_snapshots,
                can_use_cloud_backup=True,
                can_use_device_transfer=plan.device_transfer_enabled,
                can_use_sync=plan.sync_enabled,
                can_write_snapshots=True,
                can_restore_snapshots=True,
                priority_level=plan.priority_level,
                quota_source=quota_source,
                cancel_at_period_end=bool(subscription.cancel_at_period_end),
                current_period_start=subscription.current_period_start,
                current_period_end=subscription.current_period_end,
                grace_ends_at=subscription.grace_ends_at,
            )

        if self._is_subscription_in_grace(subscription):
            assert subscription is not None
            plan = self._get_plan_definition(subscription.plan_code)
            quota_source = "subscription_grace"
            return SyncEntitlement(
                plan_code=plan.code,
                plan_name=plan.name,
                status="grace_period",
                subscription_status=(subscription.status or "canceled").strip().lower() or "canceled",
                billing_cycle=(subscription.billing_cycle or "monthly").strip().lower() or "monthly",
                quota_bytes=plan.quota_bytes,
                retention_days=plan.retention_days,
                max_devices=plan.max_devices,
                max_snapshots=plan.max_snapshots,
                can_use_cloud_backup=True,
                can_use_device_transfer=plan.device_transfer_enabled,
                can_use_sync=plan.sync_enabled,
                can_write_snapshots=False,
                can_restore_snapshots=True,
                priority_level=plan.priority_level,
                quota_source=quota_source,
                cancel_at_period_end=bool(subscription.cancel_at_period_end),
                current_period_start=subscription.current_period_start,
                current_period_end=subscription.current_period_end,
                grace_ends_at=subscription.grace_ends_at,
            )

        normalized_plan = self.normalize_plan_code(user.plan)
        if normalized_plan != "free":
            plan = self._get_plan_definition(normalized_plan)
            quota_source = "legacy_user_plan"
            return SyncEntitlement(
                plan_code=plan.code,
                plan_name=plan.name,
                status="active",
                subscription_status="legacy",
                billing_cycle=None,
                quota_bytes=plan.quota_bytes,
                retention_days=plan.retention_days,
                max_devices=plan.max_devices,
                max_snapshots=plan.max_snapshots,
                can_use_cloud_backup=True,
                can_use_device_transfer=plan.device_transfer_enabled,
                can_use_sync=plan.sync_enabled,
                can_write_snapshots=True,
                can_restore_snapshots=True,
                priority_level=plan.priority_level,
                quota_source=quota_source,
                cancel_at_period_end=False,
                current_period_start=None,
                current_period_end=None,
                grace_ends_at=None,
            )

        free_plan = self._get_plan_definition("free")
        return SyncEntitlement(
            plan_code=free_plan.code,
            plan_name=free_plan.name,
            status="free",
            subscription_status="free",
            billing_cycle=None,
            quota_bytes=free_plan.quota_bytes,
            retention_days=free_plan.retention_days,
            max_devices=free_plan.max_devices,
            max_snapshots=free_plan.max_snapshots,
            can_use_cloud_backup=False,
            can_use_device_transfer=False,
            can_use_sync=False,
            can_write_snapshots=False,
            can_restore_snapshots=False,
            priority_level=free_plan.priority_level,
            quota_source=quota_source,
            cancel_at_period_end=False,
            current_period_start=None,
            current_period_end=None,
            grace_ends_at=None,
        )

    def materialize_quota(self, user: User) -> tuple[CloudStorageQuota, SyncEntitlement]:
        entitlement = self.resolve_entitlement(user)
        quota = (
            self.db.query(CloudStorageQuota)
            .filter(CloudStorageQuota.user_id == user.id)
            .first()
        )
        if quota is None:
            quota = CloudStorageQuota(
                id=str(uuid.uuid4()),
                user_id=user.id,
                usage_bytes_cached=0,
            )
            self.db.add(quota)

        quota.plan_code = entitlement.plan_code
        quota.quota_bytes = int(entitlement.quota_bytes)
        quota.retention_days = int(entitlement.retention_days)
        quota.max_devices = int(entitlement.max_devices)
        quota.max_snapshots = int(entitlement.max_snapshots)
        quota.sync_enabled = bool(entitlement.can_use_sync)
        quota.device_transfer_enabled = bool(entitlement.can_use_device_transfer)
        quota.priority_level = int(entitlement.priority_level)
        quota.quota_source = entitlement.quota_source
        quota.frozen_at = utcnow() if entitlement.status == "grace_period" else None

        raw_user_plan = (user.plan or "free").strip().lower() or "free"
        if raw_user_plan != entitlement.plan_code:
            user.plan = entitlement.plan_code

        self.db.commit()
        self.db.refresh(quota)
        return quota, entitlement

    def get_or_create_customer(self, user: User) -> SyncBillingCustomer:
        customer = (
            self.db.query(SyncBillingCustomer)
            .filter(SyncBillingCustomer.user_id == user.id)
            .first()
        )
        if customer is not None:
            if customer.email != user.email:
                customer.email = user.email
                self.db.commit()
                self.db.refresh(customer)
            return customer

        customer = SyncBillingCustomer(
            id=str(uuid.uuid4()),
            user_id=user.id,
            provider=self.settings.sync_billing_provider,
            email=user.email,
        )
        self.db.add(customer)
        self.db.commit()
        self.db.refresh(customer)
        return customer

    def create_checkout_session(
        self,
        user: User,
        *,
        plan_code: str,
        billing_cycle: str,
        success_url: str | None = None,
        cancel_url: str | None = None,
    ) -> CheckoutSessionResult:
        normalized_plan = self.normalize_plan_code(plan_code)
        normalized_cycle = self._normalize_billing_cycle(billing_cycle)
        if normalized_plan == "free":
            raise SyncBillingValidationError("Free plan does not require checkout")

        customer = self.get_or_create_customer(user)
        if self.settings.sync_billing_provider == "mock" or not self.settings.sync_billing_enabled:
            subscription = self._activate_mock_subscription(
                user,
                customer,
                plan_code=normalized_plan,
                billing_cycle=normalized_cycle,
            )
            redirect_url = self._append_query_params(
                success_url or self.settings.sync_checkout_success_url,
                {
                    "mock": "1",
                    "subscription_id": subscription.id,
                    "plan_code": normalized_plan,
                },
            )
            return CheckoutSessionResult(
                session_id=subscription.id,
                checkout_url=redirect_url,
                mode="mock_activated",
            )

        if self.settings.sync_billing_provider == "payos":
            return self._create_payos_checkout_session(
                user,
                customer,
                plan_code=normalized_plan,
                billing_cycle=normalized_cycle,
                success_url=success_url,
                cancel_url=cancel_url,
            )

        customer = self._ensure_stripe_customer(user, customer)
        price_id = self._get_stripe_price_id(normalized_plan, normalized_cycle)
        payload = {
            "mode": "subscription",
            "success_url": success_url or self.settings.sync_checkout_success_url,
            "cancel_url": cancel_url or self.settings.sync_checkout_cancel_url,
            "customer": customer.provider_customer_id or "",
            "client_reference_id": user.id,
            "allow_promotion_codes": "true",
            "metadata[user_id]": user.id,
            "metadata[plan_code]": normalized_plan,
            "metadata[billing_cycle]": normalized_cycle,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
        }
        response = self._stripe_request("POST", "/checkout/sessions", data=payload)
        session_id = str(response.get("id") or "").strip()
        checkout_url = str(response.get("url") or "").strip() or None
        if not session_id or not checkout_url:
            raise SyncBillingValidationError("Stripe checkout session did not return a usable url")
        return CheckoutSessionResult(
            session_id=session_id,
            checkout_url=checkout_url,
            mode="redirect",
        )

    def create_portal_session(
        self,
        user: User,
        *,
        return_url: str | None = None,
    ) -> PortalSessionResult:
        subscription = self.get_current_subscription(user)
        if subscription is None:
            raise SyncBillingNotFoundError("No active PigTex Sync subscription found")

        if self.settings.sync_billing_provider == "payos":
            return PortalSessionResult(session_url=None, mode="unsupported")

        if self.settings.sync_billing_provider != "stripe" or not self.settings.sync_billing_enabled:
            return PortalSessionResult(session_url=None, mode="unsupported")

        customer = self.get_or_create_customer(user)
        customer = self._ensure_stripe_customer(user, customer)
        response = self._stripe_request(
            "POST",
            "/billing_portal/sessions",
            data={
                "customer": customer.provider_customer_id or "",
                "return_url": return_url or self.settings.sync_portal_return_url,
            },
        )
        session_url = str(response.get("url") or "").strip() or None
        if not session_url:
            raise SyncBillingValidationError("Stripe billing portal did not return a url")
        return PortalSessionResult(session_url=session_url, mode="redirect")

    def cancel_subscription(self, user: User, *, immediately: bool = False) -> SyncBillingSubscription:
        subscription = self.get_current_subscription(user)
        if subscription is None:
            raise SyncBillingNotFoundError("No PigTex Sync subscription found")

        if subscription.provider == "mock" or not self.settings.sync_billing_enabled:
            now = utcnow()
            subscription.cancel_at_period_end = not immediately
            if immediately:
                subscription.status = "canceled"
                subscription.canceled_at = now
                subscription.ended_at = now
                subscription.current_period_end = now
                subscription.grace_ends_at = now + timedelta(days=max(1, int(self.settings.sync_freeze_grace_days or 14)))
            self.db.commit()
            self.materialize_quota(user)
            self.db.refresh(subscription)
            return subscription

        if subscription.provider == "payos":
            now = utcnow()
            if (
                (subscription.status or "").strip().lower() in PENDING_SUBSCRIPTION_STATUSES
                and subscription.provider_subscription_id
                and self.settings.sync_billing_enabled
                and self.settings.sync_billing_provider == "payos"
            ):
                self._cancel_payos_payment_link(subscription.provider_subscription_id)
                subscription.status = "canceled"
                subscription.canceled_at = now
                subscription.ended_at = now
            subscription.cancel_at_period_end = True
            if immediately and self._is_subscription_active(subscription):
                subscription.status = "canceled"
                subscription.canceled_at = now
                subscription.ended_at = now
                subscription.current_period_end = now
                subscription.grace_ends_at = now + timedelta(days=max(1, int(self.settings.sync_freeze_grace_days or 14)))
            self.db.commit()
            self.materialize_quota(user)
            self.db.refresh(subscription)
            return subscription

        if not subscription.provider_subscription_id:
            raise SyncBillingValidationError("Subscription is missing provider_subscription_id")

        endpoint = f"/subscriptions/{subscription.provider_subscription_id}"
        if immediately:
            provider_payload = self._stripe_request("DELETE", endpoint)
        else:
            provider_payload = self._stripe_request(
                "POST",
                endpoint,
                data={"cancel_at_period_end": "true"},
            )
        updated = self._upsert_subscription_from_stripe_object(provider_payload)
        return updated

    def handle_webhook(
        self,
        *,
        payload_text: str,
        signature_header: str | None,
    ) -> SyncBillingEvent:
        if self.settings.sync_billing_provider == "payos":
            return self._handle_payos_webhook(payload_text)

        if self.settings.sync_billing_provider != "stripe":
            raise SyncBillingConfigError("Webhook processing is only implemented for Stripe")

        signature_valid = self._verify_stripe_signature(payload_text, signature_header)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise SyncBillingValidationError("Webhook payload is not valid JSON") from exc

        event_type = str(payload.get("type") or "").strip()
        provider_event_id = str(payload.get("id") or "").strip() or None
        event = SyncBillingEvent(
            id=str(uuid.uuid4()),
            provider="stripe",
            event_type=event_type or "unknown",
            provider_event_id=provider_event_id,
            signature_valid=signature_valid,
            payload_json=payload_text,
            processed=False,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)

        if not signature_valid:
            event.error_message = "Invalid Stripe signature"
            self.db.commit()
            raise SyncBillingValidationError("Invalid Stripe signature")

        data_object = payload.get("data", {}).get("object", {})
        try:
            if event_type.startswith("customer.subscription."):
                subscription = self._upsert_subscription_from_stripe_object(data_object)
                event.user_id = subscription.user_id
                event.subscription_id = subscription.id
            elif event_type == "checkout.session.completed":
                subscription_id = str(data_object.get("subscription") or "").strip()
                if subscription_id:
                    response = self._stripe_request("GET", f"/subscriptions/{subscription_id}")
                    subscription = self._upsert_subscription_from_stripe_object(response)
                    event.user_id = subscription.user_id
                    event.subscription_id = subscription.id
            elif event_type in {"invoice.payment_failed", "invoice.payment_action_required"}:
                subscription_id = str(data_object.get("subscription") or "").strip()
                if subscription_id:
                    subscription = self._mark_provider_subscription_status(subscription_id, "past_due")
                    if subscription is not None:
                        event.user_id = subscription.user_id
                        event.subscription_id = subscription.id
            elif event_type == "invoice.paid":
                subscription_id = str(data_object.get("subscription") or "").strip()
                if subscription_id:
                    response = self._stripe_request("GET", f"/subscriptions/{subscription_id}")
                    subscription = self._upsert_subscription_from_stripe_object(response)
                    event.user_id = subscription.user_id
                    event.subscription_id = subscription.id
        except Exception as exc:
            event.error_message = str(exc)
            event.processed = False
            self.db.commit()
            raise

        event.processed = True
        event.processed_at = utcnow()
        event.error_message = None
        self.db.commit()
        self.db.refresh(event)
        return event

    def serialize_entitlement(self, entitlement: SyncEntitlement) -> dict[str, Any]:
        return {
            "plan_code": entitlement.plan_code,
            "plan_name": entitlement.plan_name,
            "status": entitlement.status,
            "subscription_status": entitlement.subscription_status,
            "billing_cycle": entitlement.billing_cycle,
            "quota_bytes": int(entitlement.quota_bytes),
            "retention_days": int(entitlement.retention_days),
            "max_devices": int(entitlement.max_devices),
            "max_snapshots": int(entitlement.max_snapshots),
            "can_use_cloud_backup": bool(entitlement.can_use_cloud_backup),
            "can_use_device_transfer": bool(entitlement.can_use_device_transfer),
            "can_use_sync": bool(entitlement.can_use_sync),
            "can_write_snapshots": bool(entitlement.can_write_snapshots),
            "can_restore_snapshots": bool(entitlement.can_restore_snapshots),
            "priority_level": int(entitlement.priority_level),
            "quota_source": entitlement.quota_source,
            "cancel_at_period_end": bool(entitlement.cancel_at_period_end),
            "current_period_start": self._serialize_dt(entitlement.current_period_start),
            "current_period_end": self._serialize_dt(entitlement.current_period_end),
            "grace_ends_at": self._serialize_dt(entitlement.grace_ends_at),
            "plans": self.list_plan_offers(),
        }

    def _append_query_params(self, url: str, params: dict[str, str]) -> str:
        if not url:
            return ""
        base = httpx.URL(url)
        query = dict(base.params.multi_items())
        for key, value in params.items():
            if value:
                query[key] = value
        return str(base.copy_merge_params(query))

    def _metadata_to_dict(self, subscription: SyncBillingSubscription | None) -> dict[str, Any]:
        if subscription is None or not subscription.metadata_json:
            return {}
        try:
            payload = json.loads(subscription.metadata_json)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _cycle_duration(self, billing_cycle: str) -> timedelta:
        return timedelta(days=365 if billing_cycle == "annual" else 30)

    def _create_payos_checkout_session(
        self,
        user: User,
        customer: SyncBillingCustomer,
        *,
        plan_code: str,
        billing_cycle: str,
        success_url: str | None,
        cancel_url: str | None,
    ) -> CheckoutSessionResult:
        plan = self._get_plan_definition(plan_code)
        amount_vnd = plan.annual_price_vnd if billing_cycle == "annual" else plan.monthly_price_vnd
        if amount_vnd <= 0:
            raise SyncBillingConfigError(f"PayOS amount is not configured for {plan_code} {billing_cycle}")

        order_code = self._generate_payos_order_code()
        return_url = success_url or self.settings.sync_checkout_success_url
        resolved_cancel_url = cancel_url or self.settings.sync_checkout_cancel_url
        description = self._build_payos_description(order_code)
        signature_payload = {
            "amount": amount_vnd,
            "cancelUrl": resolved_cancel_url,
            "description": description,
            "orderCode": order_code,
            "returnUrl": return_url,
        }
        payload = {
            "orderCode": order_code,
            "amount": amount_vnd,
            "description": description,
            "buyerName": (user.name or user.username or user.email or "PigTex User")[:255],
            "buyerEmail": user.email or "",
            "items": [
                {
                    "name": plan.name,
                    "quantity": 1,
                    "price": amount_vnd,
                }
            ],
            "cancelUrl": resolved_cancel_url,
            "returnUrl": return_url,
            "expiredAt": int(time.time()) + max(300, int(self.settings.sync_payos_payment_link_expiry_minutes or 30) * 60),
            "signature": self._sign_payos_payload(signature_payload),
        }
        response = self._payos_request("POST", "/v2/payment-requests", json_payload=payload)
        data = self._extract_payos_response_data(response, "create payment link")
        checkout_url = str(data.get("checkoutUrl") or "").strip() or None
        payment_link_id = str(data.get("paymentLinkId") or data.get("id") or "").strip() or None
        if not checkout_url:
            raise SyncBillingValidationError("PayOS create payment link did not return checkoutUrl")

        if customer.provider != "payos":
            customer.provider = "payos"
            customer.metadata_json = json.dumps(
                {
                    "last_order_code": order_code,
                    "last_payment_link_id": payment_link_id,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            self.db.commit()
            self.db.refresh(customer)

        subscription = SyncBillingSubscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            customer_id=customer.id,
            provider="payos",
            provider_subscription_id=str(order_code),
            provider_price_id=payment_link_id,
            plan_code=plan_code,
            billing_cycle=billing_cycle,
            status=self._normalize_payos_provider_status(data.get("status")),
            cancel_at_period_end=False,
            metadata_json=json.dumps(
                {
                    "source": "payos_checkout",
                    "order_code": order_code,
                    "payment_link_id": payment_link_id,
                    "checkout_url": checkout_url,
                    "provider_payload": data,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        )
        self.db.add(subscription)
        self.db.commit()
        self.db.refresh(subscription)

        return CheckoutSessionResult(
            session_id=str(order_code),
            checkout_url=checkout_url,
            mode="redirect_pending_payment",
        )

    def _generate_payos_order_code(self) -> int:
        for _ in range(8):
            candidate = int(f"{int(time.time() * 1000)}{(int(uuid.uuid4().hex[:2], 16) % 90) + 10}")
            existing = (
                self.db.query(SyncBillingSubscription)
                .filter(SyncBillingSubscription.provider_subscription_id == str(candidate))
                .first()
            )
            if existing is None:
                return candidate
        raise SyncBillingValidationError("Could not allocate a unique PayOS order code")

    def _build_payos_description(self, order_code: int) -> str:
        return f"PGX{int(order_code) % 1000000:06d}"

    def _normalize_payos_provider_status(self, status: Any, *, success: bool | None = None) -> str:
        normalized = str(status or "").strip().upper()
        if success is True or normalized in {"PAID", "SUCCESS"}:
            return "active"
        if normalized in {"PENDING", "PROCESSING", ""}:
            return "pending"
        if normalized in {"CANCELLED", "CANCELED"}:
            return "canceled"
        if normalized == "EXPIRED":
            return "expired"
        if success is False:
            return "failed"
        return "pending"

    def _resolve_payos_subscription(
        self,
        *,
        order_code: str | None = None,
        payment_link_id: str | None = None,
    ) -> SyncBillingSubscription | None:
        if order_code:
            subscription = (
                self.db.query(SyncBillingSubscription)
                .filter(SyncBillingSubscription.provider == "payos")
                .filter(SyncBillingSubscription.provider_subscription_id == order_code)
                .first()
            )
            if subscription is not None:
                return subscription
        if payment_link_id:
            return (
                self.db.query(SyncBillingSubscription)
                .filter(SyncBillingSubscription.provider == "payos")
                .filter(SyncBillingSubscription.provider_price_id == payment_link_id)
                .first()
            )
        return None

    def _sync_payos_subscription_state(
        self,
        subscription: SyncBillingSubscription,
    ) -> SyncBillingSubscription:
        order_code = str(subscription.provider_subscription_id or "").strip()
        if not order_code:
            return subscription

        try:
            response = self._payos_request("GET", f"/v2/payment-requests/{order_code}")
            data = self._extract_payos_response_data(response, "get payment link")
        except SyncBillingError:
            return subscription

        status = self._normalize_payos_provider_status(data.get("status"))
        if status == "active":
            return self._activate_payos_subscription(subscription, data)

        metadata = self._metadata_to_dict(subscription)
        metadata["provider_payload"] = data
        payment_link_id = str(data.get("paymentLinkId") or data.get("id") or "").strip()
        if payment_link_id:
            subscription.provider_price_id = payment_link_id
        subscription.status = status
        subscription.cancel_at_period_end = subscription.cancel_at_period_end or status in {"canceled", "expired"}
        if status in {"canceled", "expired"}:
            now = utcnow()
            subscription.canceled_at = subscription.canceled_at or now
            subscription.ended_at = subscription.ended_at or now
        subscription.metadata_json = json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def _activate_payos_subscription(
        self,
        subscription: SyncBillingSubscription,
        provider_payload: dict[str, Any],
    ) -> SyncBillingSubscription:
        user = self.db.query(User).filter(User.id == subscription.user_id).first()
        if user is None:
            raise SyncBillingValidationError("Could not resolve user for PayOS payment")

        now = utcnow()
        period_end = now + self._cycle_duration(subscription.billing_cycle)
        metadata = self._metadata_to_dict(subscription)
        metadata["provider_payload"] = provider_payload
        metadata["paid_at"] = self._serialize_dt(now)

        payment_link_id = str(provider_payload.get("paymentLinkId") or provider_payload.get("id") or "").strip()
        if payment_link_id:
            subscription.provider_price_id = payment_link_id
        subscription.provider = "payos"
        subscription.status = "active"
        subscription.cancel_at_period_end = True
        subscription.current_period_start = now
        subscription.current_period_end = period_end
        subscription.grace_ends_at = period_end + timedelta(days=max(1, int(self.settings.sync_freeze_grace_days or 14)))
        subscription.canceled_at = None
        subscription.ended_at = None
        subscription.metadata_json = json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
        self.db.commit()
        self.db.refresh(subscription)
        self.materialize_quota(user)
        self.db.refresh(subscription)
        return subscription

    def _cancel_payos_payment_link(self, order_code: str) -> dict[str, Any]:
        response = self._payos_request(
            "POST",
            f"/v2/payment-requests/{order_code}/cancel",
            json_payload={"cancellationReason": "Canceled in PigTex"},
        )
        return self._extract_payos_response_data(response, "cancel payment link")

    def _handle_payos_webhook(self, payload_text: str) -> SyncBillingEvent:
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise SyncBillingValidationError("Webhook payload is not valid JSON") from exc

        if not isinstance(payload, dict):
            raise SyncBillingValidationError("Webhook payload must be a JSON object")

        data_object = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        order_code = str(data_object.get("orderCode") or "").strip() or None
        payment_link_id = str(data_object.get("paymentLinkId") or data_object.get("id") or "").strip() or None
        success = payload.get("success")
        event_type = "payment.paid" if success is True else "payment.updated"
        provider_event_id = (
            str(data_object.get("reference") or "").strip()
            or payment_link_id
            or order_code
        )

        if provider_event_id:
            existing = (
                self.db.query(SyncBillingEvent)
                .filter(SyncBillingEvent.provider == "payos")
                .filter(SyncBillingEvent.provider_event_id == provider_event_id)
                .filter(SyncBillingEvent.event_type == event_type)
                .first()
            )
            if existing is not None and existing.processed:
                return existing

        signature_valid = self._verify_payos_signature(payload)
        event = SyncBillingEvent(
            id=str(uuid.uuid4()),
            provider="payos",
            event_type=event_type,
            provider_event_id=provider_event_id,
            signature_valid=signature_valid,
            payload_json=payload_text,
            processed=False,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)

        if not signature_valid:
            event.error_message = "Invalid PayOS signature"
            self.db.commit()
            raise SyncBillingValidationError("Invalid PayOS signature")

        subscription = self._resolve_payos_subscription(order_code=order_code, payment_link_id=payment_link_id)
        if subscription is None:
            # PayOS webhook URL validation and other probe events can be fully signed
            # but not tied to any local subscription yet. Acknowledge these events to
            # keep webhook health checks green while preserving observability.
            event.error_message = "Could not resolve PayOS subscription"
            event.processed = True
            event.processed_at = utcnow()
            self.db.commit()
            self.db.refresh(event)
            return event

        normalized_status = self._normalize_payos_provider_status(data_object.get("status"), success=success is True)
        if normalized_status == "active":
            subscription = self._activate_payos_subscription(subscription, data_object)
        else:
            metadata = self._metadata_to_dict(subscription)
            metadata["provider_payload"] = data_object
            subscription.status = normalized_status
            subscription.metadata_json = json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
            if normalized_status in {"canceled", "expired", "failed"}:
                now = utcnow()
                subscription.cancel_at_period_end = True
                subscription.canceled_at = subscription.canceled_at or now
                subscription.ended_at = subscription.ended_at or now
            self.db.commit()
            self.db.refresh(subscription)

        event.user_id = subscription.user_id
        event.subscription_id = subscription.id
        event.processed = True
        event.processed_at = utcnow()
        event.error_message = None
        self.db.commit()
        self.db.refresh(event)
        return event

    def _verify_payos_signature(self, payload: dict[str, Any]) -> bool:
        signature = str(payload.get("signature") or "").strip()
        if not signature:
            return False
        data_object = payload.get("data")
        if not isinstance(data_object, dict):
            return False
        expected = self._sign_payos_payload(data_object)
        return hmac.compare_digest(expected, signature)

    def _sign_payos_payload(self, payload: dict[str, Any]) -> str:
        checksum_key = (self.settings.sync_payos_checksum_key or "").strip()
        if not checksum_key:
            raise SyncBillingConfigError("SYNC_PAYOS_CHECKSUM_KEY is not configured")
        canonical = self._build_payos_signature_payload(payload)
        return hmac.new(
            checksum_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _build_payos_signature_payload(self, payload: dict[str, Any]) -> str:
        segments: list[str] = []
        for key in sorted(payload.keys()):
            if key == "signature":
                continue
            value = payload.get(key)
            if value is None:
                continue
            segments.append(f"{key}={self._serialize_payos_value(value)}")
        return "&".join(segments)

    def _serialize_payos_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if isinstance(value, dict):
            return json.dumps(
                {key: value[key] for key in sorted(value.keys()) if value[key] is not None},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return str(value)

    def _extract_payos_response_data(
        self,
        payload: dict[str, Any],
        action: str,
    ) -> dict[str, Any]:
        code = str(payload.get("code") or "").strip()
        desc = str(payload.get("desc") or "").strip()
        data = payload.get("data")
        if code and code != "00":
            raise SyncBillingValidationError(desc or f"PayOS failed to {action}")
        if not isinstance(data, dict):
            raise SyncBillingValidationError(f"PayOS did not return a valid payload for {action}")
        return data

    def _payos_request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client_id = (self.settings.sync_payos_client_id or "").strip()
        api_key = (self.settings.sync_payos_api_key or "").strip()
        if not client_id:
            raise SyncBillingConfigError("SYNC_PAYOS_CLIENT_ID is not configured")
        if not api_key:
            raise SyncBillingConfigError("SYNC_PAYOS_API_KEY is not configured")

        url = f"https://api-merchant.payos.vn{path}"
        headers = {
            "x-client-id": client_id,
            "x-api-key": api_key,
        }
        partner_code = (self.settings.sync_payos_partner_code or "").strip()
        if partner_code:
            headers["x-partner-code"] = partner_code

        response = httpx.request(
            method,
            url,
            headers=headers,
            json=json_payload,
            timeout=httpx.Timeout(20.0, connect=10.0),
        )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except Exception:
                payload = None
            message = None
            if isinstance(payload, dict):
                message = str(payload.get("desc") or payload.get("message") or "").strip() or None
            raise SyncBillingValidationError(message or f"PayOS request failed with HTTP {response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise SyncBillingValidationError("PayOS response is not a JSON object")
        return payload

    def _activate_mock_subscription(
        self,
        user: User,
        customer: SyncBillingCustomer,
        *,
        plan_code: str,
        billing_cycle: str,
    ) -> SyncBillingSubscription:
        now = utcnow()
        period_end = now + timedelta(days=365 if billing_cycle == "annual" else 30)
        subscription = SyncBillingSubscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            customer_id=customer.id,
            provider="mock",
            provider_subscription_id=f"mock_sub_{uuid.uuid4().hex[:24]}",
            plan_code=plan_code,
            billing_cycle=billing_cycle,
            status="active",
            cancel_at_period_end=False,
            current_period_start=now,
            current_period_end=period_end,
            grace_ends_at=period_end + timedelta(days=max(1, int(self.settings.sync_freeze_grace_days or 14))),
            metadata_json=json.dumps({"source": "mock_checkout"}, ensure_ascii=True, separators=(",", ":")),
        )
        self.db.add(subscription)
        self.db.commit()
        self.db.refresh(subscription)
        self.materialize_quota(user)
        return subscription

    def _mark_provider_subscription_status(
        self,
        provider_subscription_id: str,
        status: str,
    ) -> SyncBillingSubscription | None:
        subscription = (
            self.db.query(SyncBillingSubscription)
            .filter(SyncBillingSubscription.provider_subscription_id == provider_subscription_id)
            .first()
        )
        if subscription is None:
            return None

        normalized_status = (status or "").strip().lower() or "past_due"
        subscription.status = normalized_status
        if normalized_status in GRACE_SUBSCRIPTION_STATUSES:
            subscription.grace_ends_at = utcnow() + timedelta(days=max(1, int(self.settings.sync_freeze_grace_days or 14)))
        self.db.commit()

        user = self.db.query(User).filter(User.id == subscription.user_id).first()
        if user is not None:
            self.materialize_quota(user)
        self.db.refresh(subscription)
        return subscription

    def _upsert_subscription_from_stripe_object(self, payload: dict[str, Any]) -> SyncBillingSubscription:
        provider_subscription_id = str(payload.get("id") or "").strip()
        if not provider_subscription_id:
            raise SyncBillingValidationError("Stripe subscription payload is missing id")

        customer_ref = str(payload.get("customer") or "").strip()
        customer = None
        if customer_ref:
            customer = (
                self.db.query(SyncBillingCustomer)
                .filter(SyncBillingCustomer.provider_customer_id == customer_ref)
                .first()
            )

        metadata = payload.get("metadata") or {}
        user_id = str(metadata.get("user_id") or "").strip() or None
        if customer is None and user_id:
            user = self.db.query(User).filter(User.id == user_id).first()
            if user is not None:
                customer = self.get_or_create_customer(user)
                customer.provider = "stripe"
                customer.provider_customer_id = customer_ref or customer.provider_customer_id
                self.db.commit()
                self.db.refresh(customer)
        elif customer is not None:
            user = self.db.query(User).filter(User.id == customer.user_id).first()
        else:
            user = None

        if customer is None or user is None:
            raise SyncBillingValidationError("Could not resolve user for Stripe subscription event")

        price_id = self._extract_stripe_price_id(payload)
        interval = self._extract_stripe_interval(payload)
        plan_code = self.normalize_plan_code(str(metadata.get("plan_code") or "") or self._plan_code_from_price_id(price_id))
        status = str(payload.get("status") or "pending").strip().lower()
        subscription = (
            self.db.query(SyncBillingSubscription)
            .filter(SyncBillingSubscription.provider_subscription_id == provider_subscription_id)
            .first()
        )
        if subscription is None:
            subscription = SyncBillingSubscription(
                id=str(uuid.uuid4()),
                user_id=user.id,
                customer_id=customer.id,
                provider="stripe",
                provider_subscription_id=provider_subscription_id,
            )
            self.db.add(subscription)

        subscription.customer_id = customer.id
        subscription.provider = "stripe"
        subscription.provider_price_id = price_id
        subscription.plan_code = plan_code
        subscription.billing_cycle = interval
        subscription.status = status
        subscription.cancel_at_period_end = bool(payload.get("cancel_at_period_end"))
        subscription.current_period_start = self._timestamp_to_datetime(payload.get("current_period_start"))
        subscription.current_period_end = self._timestamp_to_datetime(payload.get("current_period_end"))
        subscription.canceled_at = self._timestamp_to_datetime(payload.get("canceled_at"))
        subscription.ended_at = self._timestamp_to_datetime(payload.get("ended_at"))
        if status in GRACE_SUBSCRIPTION_STATUSES:
            base_time = subscription.current_period_end or utcnow()
            subscription.grace_ends_at = base_time + timedelta(days=max(1, int(self.settings.sync_freeze_grace_days or 14)))
        else:
            subscription.grace_ends_at = subscription.current_period_end
        subscription.metadata_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

        self.db.commit()
        self.db.refresh(subscription)
        self.materialize_quota(user)
        self.db.refresh(subscription)
        return subscription

    def _extract_stripe_price_id(self, payload: dict[str, Any]) -> str | None:
        items = payload.get("items", {}).get("data", [])
        if not items:
            return None
        first_item = items[0] if isinstance(items, list) else {}
        price = first_item.get("price") or {}
        price_id = str(price.get("id") or "").strip()
        return price_id or None

    def _extract_stripe_interval(self, payload: dict[str, Any]) -> str:
        items = payload.get("items", {}).get("data", [])
        if not items:
            return "monthly"
        first_item = items[0] if isinstance(items, list) else {}
        price = first_item.get("price") or {}
        recurring = price.get("recurring") or {}
        interval = str(recurring.get("interval") or "month").strip().lower()
        return "annual" if interval == "year" else "monthly"

    def _timestamp_to_datetime(self, value: Any) -> datetime | None:
        if value in (None, "", 0):
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None

    def _plan_code_from_price_id(self, price_id: str | None) -> str:
        normalized = (price_id or "").strip()
        if not normalized:
            return "free"
        if normalized in {
            self.settings.sync_stripe_price_sync_plus_monthly.strip(),
            self.settings.sync_stripe_price_sync_plus_annual.strip(),
        }:
            return "sync_plus"
        if normalized in {
            self.settings.sync_stripe_price_sync_monthly.strip(),
            self.settings.sync_stripe_price_sync_annual.strip(),
        }:
            return "sync"
        return "free"

    def _get_stripe_price_id(self, plan_code: str, billing_cycle: str) -> str:
        if plan_code == "sync_plus":
            price_id = (
                self.settings.sync_stripe_price_sync_plus_annual
                if billing_cycle == "annual"
                else self.settings.sync_stripe_price_sync_plus_monthly
            )
        else:
            price_id = (
                self.settings.sync_stripe_price_sync_annual
                if billing_cycle == "annual"
                else self.settings.sync_stripe_price_sync_monthly
            )
        normalized = (price_id or "").strip()
        if not normalized:
            raise SyncBillingConfigError(
                f"Stripe price id is not configured for {plan_code} {billing_cycle}"
            )
        return normalized

    def _ensure_stripe_customer(
        self,
        user: User,
        customer: SyncBillingCustomer,
    ) -> SyncBillingCustomer:
        if customer.provider_customer_id:
            customer.provider = "stripe"
            self.db.commit()
            self.db.refresh(customer)
            return customer

        response = self._stripe_request(
            "POST",
            "/customers",
            data={
                "email": user.email,
                "name": user.name or user.username,
                "metadata[user_id]": user.id,
            },
        )
        provider_customer_id = str(response.get("id") or "").strip()
        if not provider_customer_id:
            raise SyncBillingValidationError("Stripe customer creation failed")
        customer.provider = "stripe"
        customer.provider_customer_id = provider_customer_id
        customer.metadata_json = json.dumps(response, ensure_ascii=True, separators=(",", ":"))
        self.db.commit()
        self.db.refresh(customer)
        return customer

    def _verify_stripe_signature(self, payload_text: str, signature_header: str | None) -> bool:
        secret = (self.settings.sync_stripe_webhook_secret or "").strip()
        if not secret:
            raise SyncBillingConfigError("SYNC_STRIPE_WEBHOOK_SECRET is not configured")
        if not signature_header:
            return False

        timestamp = ""
        signatures: list[str] = []
        for segment in signature_header.split(","):
            key, _, value = segment.partition("=")
            if key == "t":
                timestamp = value
            elif key == "v1":
                signatures.append(value)
        if not timestamp or not signatures:
            return False

        try:
            signed_at = int(timestamp)
        except ValueError:
            return False

        tolerance_seconds = 300
        if abs(int(time.time()) - signed_at) > tolerance_seconds:
            return False

        signed_payload = f"{timestamp}.{payload_text}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, signature) for signature in signatures)

    def _stripe_request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        secret_key = (self.settings.sync_stripe_secret_key or "").strip()
        if not secret_key:
            raise SyncBillingConfigError("SYNC_STRIPE_SECRET_KEY is not configured")

        url = f"https://api.stripe.com/v1{path}"
        headers = {
            "Authorization": f"Bearer {secret_key}",
        }
        response = httpx.request(
            method,
            url,
            headers=headers,
            data=data,
            timeout=httpx.Timeout(20.0, connect=10.0),
        )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except Exception:
                payload = None
            message = None
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or "").strip() or None
            raise SyncBillingValidationError(message or f"Stripe request failed with HTTP {response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise SyncBillingValidationError("Stripe response is not a JSON object")
        return payload
