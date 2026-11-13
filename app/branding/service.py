"""Reading and changing the institute profile.

`apply_from_db` copies the profile into `settings` (institute_name, app_name, the logo paths, colour, bank details...),
which is what every page, PDF and SMS reads. It runs on each worker with the policy refresh (every few seconds, see
app.policy.service.refresh_if_stale) so a change shows everywhere without a restart.
"""
from __future__ import annotations

import io
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.branding.models import InstituteProfile
from app.core.config import settings
from app.core.exceptions import ValidationAppError

MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_KINDS = {"logo": "logo_key", "logo_dark": "logo_dark_key", "signature": "signature_key"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# field -> (label, max length, required)
FIELDS = {
    "name": ("Institute name", 200, True),
    "short_name": ("Short name", 40, True),
    "tagline": ("Motto / tagline", 200, False),
    "address": ("Address", 300, False),
    "city": ("City", 80, False),
    "phone": ("Phone", 60, False),
    "email": ("Email", 150, False),
    "website": ("Website", 150, False),
    "registration_no": ("Registration / affiliation no.", 120, False),
    "primary_color": ("Main colour", 7, False),
    "document_footer": ("Footer on documents", 300, False),
    "bank_name": ("Bank", 120, False),
    "bank_account_title": ("Account title", 150, False),
    "bank_account_no": ("Account no. / IBAN", 60, False),
    "bank_branch": ("Branch", 150, False),
    "signatory_name": ("Signatory name", 120, False),
    "signatory_title": ("Signatory title", 120, False),
    "card_instructions": ("ID card instructions", 900, False),
}
# profile field -> settings attribute
TO_SETTINGS = {
    "name": "institute_name", "short_name": "app_name", "tagline": "institute_tagline", "address": "institute_address",
    "city": "institute_city", "phone": "institute_phone", "email": "institute_email", "website": "institute_website",
    "registration_no": "institute_registration_no", "primary_color": "brand_color", "document_footer": "document_footer",
    "bank_name": "bank_name", "bank_account_title": "bank_account_title", "bank_account_no": "bank_account_no", "bank_branch": "bank_branch",
    "signatory_name": "institute_signatory_name", "signatory_title": "institute_signatory_title", "card_instructions": "card_instructions",
}
_DEFAULTS: dict = {}


def _defaults() -> dict:
    """The values from .env / config, remembered once (what the profile starts from, and what tests go back to)."""
    if not _DEFAULTS:
        for f, attr in TO_SETTINGS.items():
            _DEFAULTS[attr] = getattr(settings, attr)
        for attr in ("institute_logo_path", "institute_logo_dark_path", "institute_signature_path", "brand_version"):
            _DEFAULTS[attr] = getattr(settings, attr)
        _DEFAULTS["institute_favicon_path"] = ""
    return _DEFAULTS


def _storage_path(key: str | None) -> str:
    if not key:
        return ""
    p = Path(settings.storage_local_path) / key
    return str(p) if p.exists() else ""


def apply_row(row: InstituteProfile | None) -> None:
    d = _defaults()
    if row is None:
        for attr, v in d.items():
            setattr(settings, attr, v)
        return
    for f, attr in TO_SETTINGS.items():
        v = getattr(row, f)
        setattr(settings, attr, v if v not in (None, "") else ("" if f not in ("name", "short_name") else d[attr]))
    settings.institute_logo_path = _storage_path(row.logo_key) or d["institute_logo_path"]
    settings.institute_logo_dark_path = _storage_path(row.logo_dark_key)
    settings.institute_signature_path = _storage_path(row.signature_key)
    settings.institute_favicon_path = _storage_path(row.favicon_key)
    settings.brand_version = row.version


async def apply_from_db(session: AsyncSession) -> None:
    _defaults()
    apply_row(await session.get(InstituteProfile, "main"))


def reset_for_tests() -> None:
    if _DEFAULTS:
        apply_row(None)


def profile_dict(row: InstituteProfile | None) -> dict:
    d = _defaults()
    out = {f: (getattr(row, f) if row is not None else (d[attr] if f in ("name", "short_name") else (d[attr] or None)))
           for f, attr in TO_SETTINGS.items()}
    out.update({
        "has_logo": bool(row and row.logo_key), "has_logo_dark": bool(row and row.logo_dark_key), "has_signature": bool(row and row.signature_key),
        "version": row.version if row else 0, "updated_at": row.updated_at if row else None,
        "logo_url": f"/branding/logo.png?v={row.version if row else 0}",
        "logo_dark_url": f"/branding/logo-dark.png?v={row.version if row else 0}" if row and row.logo_dark_key else None,
    })
    return out


async def _row(session: AsyncSession, *, create: bool = False) -> InstituteProfile | None:
    from sqlalchemy import select

    row = (await session.execute(select(InstituteProfile).where(InstituteProfile.id == "main").with_for_update())).scalar_one_or_none()
    if row is None and create:
        d = _defaults()
        row = InstituteProfile(id="main", name=d["institute_name"], short_name=d["app_name"], signatory_title=d["institute_signatory_title"] or None,
                               version=1)
        session.add(row)
        await session.flush()
    return row


async def get(session: AsyncSession) -> dict:
    from sqlalchemy import select

    return profile_dict((await session.execute(select(InstituteProfile).where(InstituteProfile.id == "main"))).scalar_one_or_none())


def _clean(field: str, value) -> str | None:
    label, n, required = FIELDS[field]
    if field == "card_instructions":                     # several lines: one instruction each
        lines = [" ".join(x.split()).lstrip("-*\u2022 ").strip() for x in str(value or "").splitlines()]
        lines = [x for x in lines if x]
        if len(lines) > 8:
            raise ValidationAppError("ID card instructions: at most 8 lines (they must fit on the back of the card).")
        if any(len(x) > 140 for x in lines):
            raise ValidationAppError("ID card instructions: keep each line under 140 characters.")
        v = "\n".join(lines)
        if len(v) > n:
            raise ValidationAppError(f"{label}: at most {n} characters.")
        return v or None
    v = " ".join(str(value or "").split())
    if required and len(v) < 2:
        raise ValidationAppError(f"{label} is needed.")
    if len(v) > n:
        raise ValidationAppError(f"{label}: at most {n} characters.")
    if field == "primary_color" and v:
        if not HEX.match(v):
            raise ValidationAppError("Main colour must look like #0b6b5d.")
        v = v.lower()
    if field == "email" and v and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
        raise ValidationAppError("That email address does not look right.")
    if field == "website" and v and not re.match(r"^(https?://)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$", v):
        raise ValidationAppError("That website does not look right (e.g. www.example.edu.pk).")
    return v or None


async def update(session: AsyncSession, changes: dict, *, actor_id) -> dict:
    from app.audit.service import AuditService

    unknown = [k for k in changes if k not in FIELDS]
    if unknown:
        raise ValidationAppError("Unknown field(s): " + ", ".join(unknown))
    row = await _row(session, create=True)
    before, after = {}, {}
    for k, v in changes.items():
        new = _clean(k, v)
        if new != getattr(row, k):
            before[k], after[k] = getattr(row, k), new
            setattr(row, k, new)
    if after:
        row.version += 1
        row.updated_by, row.updated_at = actor_id, datetime.now(timezone.utc)
        await session.flush()
        await AuditService(session).log(actor_user_id=actor_id, action="institute.profile_updated", entity_type="institute_profile", entity_id=None,
                                        before=before, after=after)
    apply_row(row)
    return profile_dict(row)


def _read_image(content: bytes, content_type: str | None):
    from PIL import Image, UnidentifiedImageError

    if len(content) > MAX_IMAGE_BYTES:
        raise ValidationAppError("The image is too large: at most 2 MB.")
    if not (content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff")):
        raise ValidationAppError("Upload a PNG or JPG image (SVG and other formats are not accepted).")
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
        img = Image.open(io.BytesIO(content))
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValidationAppError("That file is not a readable image.") from exc
    if img.width < 32 or img.height < 32:
        raise ValidationAppError("The image is too small (at least 32 x 32 pixels).")
    if img.width * img.height > 40_000_000:
        raise ValidationAppError("The image is too large in pixels.")
    return img


def _png(img, max_w: int, max_h: int) -> bytes:
    from PIL import Image

    img = img.convert("RGBA")
    img.thumbnail((max_w, max_h), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _favicon(img) -> bytes:
    from PIL import Image

    img = img.convert("RGBA")
    side = max(img.width, img.height)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    square = square.resize((64, 64), Image.LANCZOS)
    out = io.BytesIO()
    square.save(out, format="PNG")
    return out.getvalue()


def _store(data: bytes) -> str:
    key = f"branding/{uuid.uuid4().hex}.png"
    full = Path(settings.storage_local_path) / key
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    return key


async def save_image(session: AsyncSession, kind: str, content: bytes, content_type: str | None, *, actor_id) -> dict:
    """Logo, logo for dark backgrounds, or signature. Checked, resized and stored as PNG; old files are kept (history)."""
    from app.audit.service import AuditService

    if kind not in IMAGE_KINDS:
        raise ValidationAppError("Unknown image.")
    img = _read_image(content, content_type)
    row = await _row(session, create=True)
    if kind == "signature":
        row.signature_key = _store(_png(img, 900, 300))
    else:
        setattr(row, IMAGE_KINDS[kind], _store(_png(img, 800, 800)))
        if kind == "logo":
            row.favicon_key = _store(_favicon(img))
    row.version += 1
    row.updated_by, row.updated_at = actor_id, datetime.now(timezone.utc)
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action=f"institute.{kind}_uploaded", entity_type="institute_profile", entity_id=None,
                                    metadata={"bytes": len(content), "size": f"{img.width}x{img.height}"})
    apply_row(row)
    return profile_dict(row)


async def remove_image(session: AsyncSession, kind: str, *, actor_id) -> dict:
    from app.audit.service import AuditService

    if kind not in IMAGE_KINDS:
        raise ValidationAppError("Unknown image.")
    row = await _row(session, create=True)
    setattr(row, IMAGE_KINDS[kind], None)
    if kind == "logo":
        row.favicon_key = None
    row.version += 1
    row.updated_by, row.updated_at = actor_id, datetime.now(timezone.utc)
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action=f"institute.{kind}_removed", entity_type="institute_profile", entity_id=None)
    apply_row(row)
    return profile_dict(row)
