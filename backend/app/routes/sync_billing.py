from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import User
from ..services.cloud_backup_service import CloudBackupService
from ..services.sync_billing_service import (
    SyncBillingConfigError,
    SyncBillingNotFoundError,
    SyncBillingService,
    SyncBillingValidationError,
)
from .auth_utils import get_current_user

router = APIRouter(prefix="/billing/sync", tags=["PigTex Sync Billing"])


class SyncPlanOfferResponse(BaseModel):
    plan_code: str
    name: str
    quota_bytes: int
    retention_days: int
    max_devices: int
    max_snapshots: int
    monthly_price_vnd: int
    annual_price_vnd: int
    sync_enabled: bool
    device_transfer_enabled: bool
    priority_level: int


class SyncEntitlementResponse(BaseModel):
    plan_code: str
    plan_name: str
    status: str
    subscription_status: str
    billing_cycle: Optional[str] = None
    quota_bytes: int
    usage_bytes: int
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
    current_period_start: Optional[str] = None
    current_period_end: Optional[str] = None
    grace_ends_at: Optional[str] = None
    plans: list[SyncPlanOfferResponse]


class CheckoutSessionRequest(BaseModel):
    plan_code: str = Field(..., min_length=1, max_length=32)
    billing_cycle: str = Field(default="monthly", min_length=1, max_length=16)
    success_url: Optional[str] = Field(default=None, max_length=500)
    cancel_url: Optional[str] = Field(default=None, max_length=500)


class CheckoutSessionResponse(BaseModel):
    session_id: str
    checkout_url: Optional[str] = None
    mode: str


class PortalSessionRequest(BaseModel):
    return_url: Optional[str] = Field(default=None, max_length=500)


class PortalSessionResponse(BaseModel):
    session_url: Optional[str] = None
    mode: str


class CancelSubscriptionRequest(BaseModel):
    immediately: bool = False


class CancelSubscriptionResponse(BaseModel):
    ok: bool
    status: str
    cancel_at_period_end: bool
    current_period_end: Optional[str] = None
    grace_ends_at: Optional[str] = None


class WebhookResponse(BaseModel):
    ok: bool
    event_id: str


def _build_billing_service(db: Session) -> SyncBillingService:
    return SyncBillingService(db, settings=get_settings())


def _build_cloud_service(db: Session) -> CloudBackupService:
    return CloudBackupService(db, settings=get_settings())


@router.get("/entitlement", response_model=SyncEntitlementResponse)
async def get_sync_entitlement(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    billing_service = _build_billing_service(db)
    cloud_service = _build_cloud_service(db)
    quota, entitlement = billing_service.materialize_quota(current_user)
    usage_bytes = cloud_service.calculate_usage_bytes(current_user.id)
    quota.usage_bytes_cached = usage_bytes
    db.commit()

    payload = billing_service.serialize_entitlement(entitlement)
    payload["usage_bytes"] = usage_bytes
    return SyncEntitlementResponse(**payload)


@router.post("/checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    payload: CheckoutSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = _build_billing_service(db)
    try:
        session = service.create_checkout_session(
            current_user,
            plan_code=payload.plan_code,
            billing_cycle=payload.billing_cycle,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
        )
    except SyncBillingValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SyncBillingConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return CheckoutSessionResponse(
        session_id=session.session_id,
        checkout_url=session.checkout_url,
        mode=session.mode,
    )


@router.post("/portal-session", response_model=PortalSessionResponse)
async def create_portal_session(
    payload: PortalSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = _build_billing_service(db)
    try:
        session = service.create_portal_session(
            current_user,
            return_url=payload.return_url,
        )
    except SyncBillingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SyncBillingValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SyncBillingConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return PortalSessionResponse(session_url=session.session_url, mode=session.mode)


@router.post("/cancel", response_model=CancelSubscriptionResponse)
async def cancel_subscription(
    payload: CancelSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = _build_billing_service(db)
    try:
        subscription = service.cancel_subscription(
            current_user,
            immediately=payload.immediately,
        )
    except SyncBillingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SyncBillingValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SyncBillingConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return CancelSubscriptionResponse(
        ok=True,
        status=subscription.status,
        cancel_at_period_end=bool(subscription.cancel_at_period_end),
        current_period_end=subscription.current_period_end.isoformat() if subscription.current_period_end else None,
        grace_ends_at=subscription.grace_ends_at.isoformat() if subscription.grace_ends_at else None,
    )


@router.post("/webhook", response_model=WebhookResponse)
async def handle_sync_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(default=None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
):
    service = _build_billing_service(db)
    payload_text = (await request.body()).decode("utf-8")
    try:
        event = service.handle_webhook(
            payload_text=payload_text,
            signature_header=stripe_signature,
        )
    except SyncBillingValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SyncBillingConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return WebhookResponse(ok=True, event_id=event.id)
