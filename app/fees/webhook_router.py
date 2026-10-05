from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AuthenticationError
from app.fees.assignment_service import ChallanService
from app.fees.schemas import ChallanOut

router = APIRouter(prefix="/fees/payments", tags=["fees"])


class WebhookPaymentNotification(BaseModel):
    """Shape a real 1LINK 1Bill payment notification would arrive in —
    exact field names will need adjusting once VIST's bank issues real
    integration documentation, but the response behavior (confirm the
    challan, write the ledger CREDIT, done) is already correct and
    won't need to change.

    A real integration would also carry its own signature/HMAC field
    for verifying the request genuinely came from 1LINK. Until those
    real credentials exist, this endpoint is protected by a single
    shared secret instead — see _verify_webhook_secret below. A
    security review found this endpoint had NO authentication at all
    (reachable by anyone with a valid consumer_number, no login
    required) — that has been fixed; see the secret check below."""

    consumer_number: str
    gateway_reference: str


def _verify_webhook_secret(x_webhook_secret: str | None) -> None:
    configured = settings.payment_webhook_shared_secret
    if not configured:
        # Deliberately refuses EVERY request, not just unsigned ones —
        # there is no real 1LINK secret to check against yet, so "no
        # secret configured" must mean "this endpoint is not live,"
        # not "skip the check." Set payment_webhook_shared_secret to
        # actually enable this endpoint once real credentials exist.
        raise AuthenticationError("Payment webhook is not configured.")
    if not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, configured):
        raise AuthenticationError("Invalid webhook credentials.")


@router.post(
    "/webhook",
    response_model=ChallanOut,
    summary="1LINK payment callback endpoint (shared-secret protected placeholder — see payment_gateway.py)",
)
async def payment_webhook(
    payload: WebhookPaymentNotification,
    x_webhook_secret: str | None = Header(default=None),
    db=Depends(get_db),
) -> ChallanOut:
    _verify_webhook_secret(x_webhook_secret)
    challan = await ChallanService(db).confirm_from_webhook(
        consumer_number=payload.consumer_number, gateway_reference=payload.gateway_reference
    )
    return ChallanOut.model_validate(challan)