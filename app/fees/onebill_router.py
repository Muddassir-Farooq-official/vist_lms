"""1Bill connection points called by the bank network (no sign-in: the bank's username/password in the headers), and
the daily match report for accounts. See app/fees/onebill.py."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth.permissions import Permissions
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.core.logging import get_logger
from app.core.net import client_ip
from app.fees import onebill as ob

router = APIRouter(prefix="/onebill", tags=["1bill"])
report_router = APIRouter(prefix="/fees/onebill", tags=["fees"])
logger = get_logger(__name__)


class InquiryIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    consumer_number: str = ""
    bank_mnemonic: str | None = None
    reserved: str | None = None


class PaymentIn(InquiryIn):
    tran_auth_id: str = ""
    transaction_amount: str = ""
    tran_date: str | None = None
    tran_time: str | None = None


def _gate(request: Request, username: str | None, password: str | None, basic: tuple[str, str] | None) -> JSONResponse | None:
    if not ob.is_enabled():
        return JSONResponse(status_code=503, content={"response_Code": ob.ERROR, "message": "1Bill is not switched on."})
    u, p = (username, password) if username or password else (basic or (None, None))
    if not ob.credentials_ok(u, p) or not ob.address_ok(client_ip(request)):
        logger.warning("one_bill_refused", ip=client_ip(request))
        return JSONResponse(status_code=401, content={"response_Code": ob.ERROR, "message": "Not allowed."})
    return None


def _basic(request: Request) -> tuple[str, str] | None:
    import base64

    h = request.headers.get("authorization", "")
    if not h.lower().startswith("basic "):
        return None
    try:
        u, _, p = base64.b64decode(h[6:]).decode("utf-8").partition(":")
        return u, p
    except Exception:  # noqa: BLE001
        return None


@router.post("/inquiry", summary="1Bill bill inquiry (called by the bank network)")
async def bill_inquiry(payload: InquiryIn, request: Request, username: str | None = Header(None), password: str | None = Header(None),
                       db=Depends(get_db)):
    refused = _gate(request, username, password, _basic(request))
    if refused:
        return refused
    try:
        return await ob.inquiry(db, payload.consumer_number)
    except Exception as exc:  # noqa: BLE001 - always answer the bank in its own format
        logger.error("one_bill_inquiry_failed", error=str(exc)[:300])
        return ob.inquiry_reply(ob.ERROR)


@router.post("/payment", summary="1Bill bill payment (called by the bank network)")
async def bill_payment(payload: PaymentIn, request: Request, username: str | None = Header(None), password: str | None = Header(None),
                       db=Depends(get_db)):
    refused = _gate(request, username, password, _basic(request))
    if refused:
        return refused
    try:
        reply = await ob.payment(db, consumer_number=payload.consumer_number, tran_auth_id=payload.tran_auth_id,
                                 amount_text=payload.transaction_amount, tran_date=payload.tran_date, tran_time=payload.tran_time,
                                 bank_mnemonic=payload.bank_mnemonic)
        if reply["response_Code"] == ob.OK:
            await db.commit()
        else:
            await db.rollback()
        return reply
    except Exception as exc:  # noqa: BLE001 - nothing is kept, and the bank is told so it does not take the money
        await db.rollback()
        logger.error("one_bill_payment_failed", error=str(exc)[:300])
        return ob.payment_reply(ob.ERROR)


@report_router.get("/report", summary="1Bill payments received on a day, for matching with the bank (accounts)")
async def report(day: date | None = None, current_user: CurrentUser = Depends(require_permission(Permissions.ACCOUNT_VIEW)), db=Depends(get_db)) -> dict:
    return await ob.day_report(db, day or institute_today())
