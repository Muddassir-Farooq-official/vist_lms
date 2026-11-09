"""SMS log and controls (administration)."""
from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.sms import models as _models  # noqa: F401  (registers the table)
from app.sms.models import SmsStatus
from app.sms.service import SmsService

router = APIRouter(prefix="/sms", tags=["sms"])
manage = require_permission(Permissions.SMS_MANAGE)


class TestIn(BaseModel):
    number: str = Field(min_length=10, max_length=20)
    message: str = Field(default="Test message from the institute portal.", min_length=1, max_length=300)


@router.get("", summary="SMS overview: provider set up?, last 7 days by status, latest messages")
async def overview(status: SmsStatus | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SmsService(db).overview(status=status)


class BulkIn(BaseModel):
    audience: str = Field(pattern="^(STUDENTS|STAFF)$")
    message: str = Field(min_length=5, max_length=300)
    program_id: uuid.UUID | None = None
    semester: int | None = Field(default=None, ge=1, le=12)
    section: str | None = Field(default=None, max_length=20)
    defaulters_only: bool = False
    job_role_id: uuid.UUID | None = None
    dry_run: bool = True


@router.post("/bulk", summary="One message to many students or staff (dry_run=true only counts who would get it)")
async def bulk(payload: BulkIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SmsService(db).bulk(**payload.model_dump(), actor_id=current_user.id)


@router.post("/send-now", summary="Send queued messages now (the sender process also does this every 30 seconds)")
async def send_now(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SmsService(db).send_pending(limit=50, time_budget=12)


@router.post("/retry", summary="Put failed (and not-sent LOGGED) messages back in the queue")
async def retry(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return {"requeued": await SmsService(db).retry_failed()}


@router.post("/test", status_code=201, summary="Queue a test SMS to one number")
async def test(payload: TestIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    m = await SmsService(db).queue_test(payload.number, payload.message)
    return {"id": m.id, "status": m.status.value}


@router.post("/fee-reminders", summary="Queue overdue-fee reminders now (normally run daily by the sender)")
async def fee_reminders(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return {"queued": await SmsService(db).queue_fee_overdue_reminders()}

# ------------------------------------------------------------------ WhatsApp (app/sms/whatsapp.py)
class WhatsAppIn(BaseModel):
    mode: str = Field(pattern="^(OFF|META|LINK)$")
    send_documents: bool = True
    meta_phone_number_id: str | None = Field(default=None, max_length=40)
    meta_token: str | None = Field(default=None, max_length=1000)          # None = keep the saved one; "" = remove it
    meta_template_text: str | None = Field(default=None, max_length=100)
    meta_template_document: str | None = Field(default=None, max_length=100)
    meta_template_language: str = Field(default="en", max_length=10)
    link_url: str | None = Field(default=None, max_length=1000)
    link_method: str = Field(default="GET", pattern="^(GET|POST)$")
    link_body: str | None = Field(default=None, max_length=2000)
    link_api_key: str | None = Field(default=None, max_length=500)       # None = keep; "" = remove
    link_sender: str | None = Field(default=None, max_length=60)
    link_success_marker: str | None = Field(default=None, max_length=100)


class WhatsAppTestIn(BaseModel):
    number: str = Field(min_length=10, max_length=20)
    message: str = Field(default="Test message from the institute portal.", min_length=1, max_length=300)


def _wa_out(row, cfg) -> dict:
    from app.core.secretbox import open_

    return {"mode": cfg.mode, "ready": cfg.ready, "send_documents": cfg.send_documents, "meta_phone_number_id": cfg.meta_phone_number_id,
            "has_meta_token": bool(row and row.meta_token_enc), "meta_token_unreadable": bool(row and row.meta_token_enc and open_(row.meta_token_enc) is None),
            "meta_template_text": cfg.meta_template_text, "meta_template_document": cfg.meta_template_document,
            "meta_template_language": cfg.meta_template_language, "link_url": cfg.link_url, "link_method": cfg.link_method, "link_body": cfg.link_body,
            "has_link_api_key": bool(row and row.link_api_key_enc), "link_sender": cfg.link_sender, "link_success_marker": cfg.link_success_marker,
            "documents_address": cfg.base or None, "last_test_at": row.last_test_at if row else None, "last_test_ok": row.last_test_ok if row else None,
            "last_test_error": row.last_test_error if row else None, "updated_at": row.updated_at if row else None,
            "sms_configured": bool(settings.sms_provider_url)}


@router.get("/whatsapp", summary="WhatsApp settings (secrets are never shown)")
async def whatsapp_settings(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.sms import whatsapp
    from app.sms.models import WhatsAppSettings

    return _wa_out(await db.get(WhatsAppSettings, 1), await whatsapp.load_config(db))


@router.put("/whatsapp", summary="Save the WhatsApp settings")
async def save_whatsapp(payload: WhatsAppIn, request: Request, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from datetime import datetime, timezone

    from app.audit.service import AuditService
    from app.core.exceptions import ValidationAppError
    from app.core.secretbox import seal
    from app.core.share import site_url
    from app.sms import whatsapp
    from app.sms.models import WhatsAppSettings

    row = await db.get(WhatsAppSettings, 1)
    if row is None:
        row = WhatsAppSettings(id=1)
        db.add(row)
    clean = lambda v: (v or "").strip() or None                                                    # noqa: E731
    url = clean(payload.link_url)
    if url and not url.lower().startswith(("https://", "http://")):
        raise ValidationAppError("The provider's web address must start with https://")
    before = {"mode": row.mode, "meta_phone_number_id": row.meta_phone_number_id, "meta_template_text": row.meta_template_text,
              "meta_template_document": row.meta_template_document, "link_url": row.link_url, "send_documents": row.send_documents}
    row.mode, row.send_documents = payload.mode, payload.send_documents
    row.meta_phone_number_id, row.meta_template_text = clean(payload.meta_phone_number_id), clean(payload.meta_template_text)
    row.meta_template_document, row.meta_template_language = clean(payload.meta_template_document), (clean(payload.meta_template_language) or "en")
    row.link_url, row.link_method, row.link_body = url, payload.link_method, clean(payload.link_body)
    row.link_sender, row.link_success_marker = clean(payload.link_sender), clean(payload.link_success_marker)
    if payload.meta_token is not None:
        row.meta_token_enc = seal(payload.meta_token.strip()) if payload.meta_token.strip() else None
    if payload.link_api_key is not None:
        row.link_api_key_enc = seal(payload.link_api_key.strip()) if payload.link_api_key.strip() else None
    base = site_url(request)
    if base.startswith("https://"):
        row.public_base = base                                       # documents are linked on the address the office uses
    row.updated_by, row.updated_at = current_user.id, datetime.now(timezone.utc)
    await db.flush()
    cfg = await whatsapp.load_config(db)
    if payload.mode != "OFF" and not cfg.ready:
        raise ValidationAppError("Fill in what this way of sending needs: for the WhatsApp Cloud API the phone number ID, the access token and "
                                 "the text template name; for a provider's web link, the web address.")
    await AuditService(db).log(actor_user_id=current_user.id, action="settings.whatsapp_saved", entity_type="whatsapp_settings", entity_id=None,
                               before=before, after={k: getattr(row, k) for k in before} | {"token_changed": payload.meta_token is not None,
                                                                                            "api_key_changed": payload.link_api_key is not None})
    return _wa_out(row, cfg)


@router.post("/whatsapp/test", summary="Send one WhatsApp message now and say whether the provider took it")
async def test_whatsapp(payload: WhatsAppTestIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from datetime import datetime, timezone

    import httpx

    from app.core.exceptions import ValidationAppError
    from app.sms import whatsapp
    from app.sms.models import SmsMessage, WhatsAppSettings
    from app.sms.service import normalize_pk_mobile

    norm = normalize_pk_mobile(payload.number)
    if norm is None:
        raise ValidationAppError("Enter a Pakistani mobile number, e.g. 0300-1234567.")
    cfg = await whatsapp.load_config(db)
    if not cfg.ready:
        raise ValidationAppError("Save the WhatsApp settings first.")
    async with httpx.AsyncClient(timeout=15) as client:
        ok, info = await whatsapp.send(client, cfg, norm, payload.message, None)
    row = await db.get(WhatsAppSettings, 1)
    row.last_test_at, row.last_test_ok, row.last_test_error = datetime.now(timezone.utc), ok, None if ok else info[:500]
    db.add(SmsMessage(to_number=payload.number[:20], recipient="TEST", body=payload.message[:300], event="TEST", channel="WHATSAPP",
                      status=SmsStatus.SENT if ok else SmsStatus.FAILED, attempts=1, sent_at=datetime.now(timezone.utc) if ok else None,
                      last_error=None if ok else f"WhatsApp: {info}"[:300], whatsapp_error=None if ok else info[:300],
                      provider_reference=info[:120] if ok else None))
    await db.flush()
    return {"ok": ok, "detail": info if not ok else "Accepted by WhatsApp. Check the phone."}


public_router = APIRouter(prefix="/wa", tags=["whatsapp (public)"])


@public_router.get("/d/{token}", include_in_schema=False)
async def whatsapp_document(token: str, db=Depends(get_db)):
    """The document sent with a WhatsApp message. The signed token is the permission; it expires (app/sms/whatsapp.py)."""
    from fastapi.responses import Response

    from app.core.exceptions import ResourceNotFoundError
    from app.core.storage import SAFE_SERVE_HEADERS
    from app.sms import whatsapp

    found = whatsapp.read_token(token[:400])
    doc = await whatsapp.render_document(db, *found) if found else None
    if doc is None:
        raise ResourceNotFoundError("This link has expired or the document is no longer available. Open the portal to download it.")
    pdf, name = doc
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "private, no-store", **SAFE_SERVE_HEADERS})
