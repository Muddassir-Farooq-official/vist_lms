"""WhatsApp: tried first for every outgoing message; SMS is used only when WhatsApp could not take it (app/sms/service.py).

Two ways to send, chosen in Settings -> SMS & WhatsApp (table whatsapp_settings, secrets sealed):

  META  WhatsApp Cloud API (graph.facebook.com). A business may only start a conversation with an approved *template*, so two
        templates are used: a text one whose body is a single {{1}} (the message), and optionally a document one with a
        DOCUMENT header and the same body. "Accepted" by Meta counts as sent (replies and read receipts are not read: one-way).
  LINK  Any WhatsApp provider that takes a plain web request, set up like SMS: a URL (and for POST a body) with
        {to} {message} {api_key} {sender} {document_url} {document_name}. If the template has no {document_url}, the document
        link is added to the end of the message instead.

Documents (challan, receipt, result card, certificate) are sent as a link to /wa/d/<token>: the token is signed with the
server's secret and expires after DOC_DAYS, so the PDF opens without signing in but nobody can guess or change one. It is made
for the moment of sending and shows the record as it is when opened.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secretbox import open_

DOC_DAYS = 14
GRAPH = "https://graph.facebook.com/v20.0"
KINDS = ("CHALLAN", "RECEIPT", "RESULT", "CERTIFICATE")
DOC_NAMES = {"CHALLAN": "Fee challan", "RECEIPT": "Fee receipt", "RESULT": "Result card", "CERTIFICATE": "Certificate"}


@dataclass
class WhatsAppConfig:
    mode: str = "OFF"
    send_documents: bool = True
    meta_phone_number_id: str | None = None
    meta_token: str | None = None
    meta_template_text: str | None = None
    meta_template_document: str | None = None
    meta_template_language: str = "en"
    link_url: str | None = None
    link_method: str = "GET"
    link_body: str | None = None
    link_api_key: str | None = None
    link_sender: str | None = None
    link_success_marker: str | None = None
    public_base: str | None = None

    @property
    def ready(self) -> bool:
        if self.mode == "META":
            return bool(self.meta_phone_number_id and self.meta_token and self.meta_template_text)
        if self.mode == "LINK":
            return bool(self.link_url)
        return False

    @property
    def base(self) -> str:
        return ((self.public_base or settings.public_url or "").strip()).rstrip("/")


async def load_config(session: AsyncSession) -> WhatsAppConfig:
    from app.sms.models import WhatsAppSettings

    row = await session.get(WhatsAppSettings, 1)
    if row is None:
        return WhatsAppConfig()
    return WhatsAppConfig(mode=row.mode, send_documents=row.send_documents, meta_phone_number_id=row.meta_phone_number_id,
                          meta_token=open_(row.meta_token_enc), meta_template_text=row.meta_template_text,
                          meta_template_document=row.meta_template_document, meta_template_language=row.meta_template_language or "en",
                          link_url=row.link_url, link_method=row.link_method or "GET", link_body=row.link_body,
                          link_api_key=open_(row.link_api_key_enc), link_sender=row.link_sender, link_success_marker=row.link_success_marker,
                          public_base=row.public_base)


def wa_number(normalized: str) -> str:
    """WhatsApp wants the international number without '+': 923001234567."""
    digits = "".join(ch for ch in normalized if ch.isdigit())
    return "92" + digits[1:] if digits.startswith("0") else digits


# ------------------------------------------------------------------ document links
def _key() -> bytes:
    return hashlib.sha256(("wa-doc:" + (settings.jwt_secret or "")).encode()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(attachment: str, *, days: int = DOC_DAYS, now: float | None = None) -> str:
    payload = _b64(json.dumps({"a": attachment, "e": int((now or time.time()) + days * 86400)}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest()[:18])
    return f"{payload}.{sig}"


def read_token(token: str, *, now: float | None = None) -> tuple[str, uuid.UUID] | None:
    """(kind, id) when the token is genuine and not expired, else None."""
    try:
        payload, sig = token.split(".", 1)
        good = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest()[:18])
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(payload))
        if int(data["e"]) < (now or time.time()):
            return None
        kind, ident = str(data["a"]).split(":", 1)
        return (kind, uuid.UUID(ident)) if kind in KINDS else None
    except Exception:  # noqa: BLE001 - anything malformed is simply not a valid link
        return None


def document_url(cfg: WhatsAppConfig, attachment: str | None) -> tuple[str | None, str | None]:
    """(url, file name) for the message's document, or (None, None) when there is none or no public address to link to."""
    if not attachment or not cfg.send_documents or not cfg.base:
        return None, None
    kind = attachment.split(":", 1)[0]
    if kind not in KINDS:
        return None, None
    return f"{cfg.base}/wa/d/{make_token(attachment)}", f"{DOC_NAMES[kind].lower().replace(' ', '-')}.pdf"


async def render_document(session: AsyncSession, kind: str, ident: uuid.UUID) -> tuple[bytes, str] | None:
    """The PDF behind a document link, as it is now. None when the record is gone (or held back, e.g. a result card on hold)."""
    from starlette.concurrency import run_in_threadpool

    if kind == "CHALLAN":
        from app.fees.account_router import challan_pdf_data
        from app.fees.challan_pdf import render_challan_pdf
        from app.fees.models import FeeAssignment
        from app.fees.payment_models import PaymentChallan

        ch = await session.get(PaymentChallan, ident)
        if ch is None:
            return None
        data = await challan_pdf_data(session, ch, await session.get(FeeAssignment, ch.fee_assignment_id))
        return await run_in_threadpool(render_challan_pdf, data), f"challan-{ch.consumer_number}.pdf"
    if kind == "RECEIPT":
        from app.fees.account_router import receipt_pdf_data
        from app.fees.models import FeeAssignment, FeeLedgerEntry
        from app.fees.receipt_pdf import render_receipt_pdf

        e = await session.get(FeeLedgerEntry, ident)
        if e is None or not e.receipt_number:
            return None
        data = await receipt_pdf_data(session, e, await session.get(FeeAssignment, e.fee_assignment_id))
        return await run_in_threadpool(render_receipt_pdf, data), f"receipt-{e.receipt_number}.pdf"
    if kind == "RESULT":
        from app.academics.transcript_pdf import render_transcript_pdf
        from app.academics.transcript_service import TranscriptService
        from app.attendance.holds import ensure_no_attendance_hold

        try:
            await ensure_no_attendance_hold(session, ident, action="The result card")
        except Exception:  # noqa: BLE001 - on hold: not shared
            return None
        data = await TranscriptService(session).build(ident)
        return await run_in_threadpool(render_transcript_pdf, data), "result-card.pdf"
    if kind == "CERTIFICATE":
        from app.records.pdf import render_certificate_pdf
        from app.records.requests import CertificateRequestService
        from app.records.service import reference_of, verify_url

        try:
            parts = await CertificateRequestService(session).pdf_parts(ident)
        except Exception:  # noqa: BLE001 - cancelled or not ready
            return None
        d = parts["doc"]
        pdf = await run_in_threadpool(render_certificate_pdf, institute=parts["institute"], kind=d.kind, reference=reference_of(d), code=d.verification_code,
                                      verify_url=verify_url(d.verification_code), paragraphs=parts["paragraphs"], issued_on=parts["issued_on"])
        return pdf, f"certificate-{reference_of(d)}.pdf"
    return None


# ------------------------------------------------------------------ sending
async def send(client: httpx.AsyncClient, cfg: WhatsAppConfig, number: str, body: str, attachment: str | None) -> tuple[bool, str]:
    """(taken, reference-or-error). Never raises."""
    to = wa_number(number)
    url, name = document_url(cfg, attachment)
    try:
        if cfg.mode == "META":
            return await _meta(client, cfg, to, body, url, name)
        if cfg.mode == "LINK":
            return await _link(client, cfg, to, body, url, name)
        return False, "WhatsApp is off"
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:250]}"


async def _meta(client, cfg: WhatsAppConfig, to: str, body: str, url: str | None, name: str | None) -> tuple[bool, str]:
    params = [{"type": "text", "text": body}]
    if url and cfg.meta_template_document:
        template = {"name": cfg.meta_template_document, "language": {"code": cfg.meta_template_language},
                    "components": [{"type": "header", "parameters": [{"type": "document", "document": {"link": url, "filename": name}}]},
                                   {"type": "body", "parameters": params}]}
    else:
        if url:
            params = [{"type": "text", "text": f"{body} {url}"}]
        template = {"name": cfg.meta_template_text, "language": {"code": cfg.meta_template_language},
                    "components": [{"type": "body", "parameters": params}]}
    resp = await client.post(f"{GRAPH}/{cfg.meta_phone_number_id}/messages", headers={"Authorization": f"Bearer {cfg.meta_token}"},
                             json={"messaging_product": "whatsapp", "to": to, "type": "template", "template": template})
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if 200 <= resp.status_code < 300 and data.get("messages"):
        return True, str(data["messages"][0].get("id", ""))[:120]
    err = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
    return False, f"HTTP {resp.status_code}: {(err or resp.text or '').strip()[:250]}"


async def _link(client, cfg: WhatsAppConfig, to: str, body: str, url: str | None, name: str | None) -> tuple[bool, str]:
    from urllib.parse import quote, quote_plus

    template = (cfg.link_url or "") + (cfg.link_body or "")
    text = body if (not url or "{document_url}" in template) else f"{body} {url}"
    values = {"to": to, "message": text, "api_key": cfg.link_api_key or "", "sender": cfg.link_sender or "",
              "document_url": url or "", "document_name": name or ""}
    target = cfg.link_url or ""
    for k, v in values.items():
        target = target.replace("{" + k + "}", quote(v))
    method = (cfg.link_method or "GET").upper()
    data = None
    if method == "POST" and cfg.link_body:
        data = cfg.link_body
        for k, v in values.items():
            data = data.replace("{" + k + "}", json.dumps(v)[1:-1] if data.lstrip().startswith("{") else quote_plus(v))
    is_json = bool(data and data.lstrip().startswith("{"))
    resp = await client.request(method, target, content=data,
                                headers={"Content-Type": "application/json" if is_json else "application/x-www-form-urlencoded"} if data else None)
    text_out = resp.text[:2000]
    if 200 <= resp.status_code < 300 and (not cfg.link_success_marker or cfg.link_success_marker in text_out):
        return True, text_out.strip()[:120]
    return False, f"HTTP {resp.status_code}: {text_out.strip()[:250]}"
