import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from ..models import User
from ..services.texapi_partner_service import TexApiPartnerService
from .auth_utils import get_current_user

router = APIRouter(prefix="/texapi-partner", tags=["TexAPI Partner"])
logger = logging.getLogger(__name__)


@router.get("/usage")
async def get_texapi_partner_usage(
    limit: int = Query(50, ge=1, le=200),
    from_iso: Optional[str] = Query(None, alias="from"),
    to_iso: Optional[str] = Query(None, alias="to"),
    current_user: User = Depends(get_current_user),
):
    service = TexApiPartnerService()
    return await service.get_usage(
        current_user,
        limit=limit,
        from_iso=from_iso,
        to_iso=to_iso,
    )


@router.post("/webhook")
async def handle_texapi_partner_webhook(
    request: Request,
):
    raw_body = await request.body()
    service = TexApiPartnerService()
    service.verify_webhook_signature(
        raw_body,
        timestamp=request.headers.get("X-PigTex-Timestamp"),
        signature=request.headers.get("X-PigTex-Signature"),
    )

    payload = json.loads(raw_body.decode("utf-8"))
    if isinstance(payload, dict):
        service.handle_webhook_event(payload)
    else:
        logger.warning("Ignoring TexAPI webhook with non-object payload")
    return {"ok": True}
