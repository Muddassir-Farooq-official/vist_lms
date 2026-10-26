from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.branding import service
from app.core import brand
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/institute", tags=["institute profile"])
public_router = APIRouter(tags=["branding (public)"])
manage = require_permission(Permissions.INSTITUTE_PROFILE_MANAGE)


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    short_name: str | None = Field(default=None, max_length=40)
    tagline: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=150)
    website: str | None = Field(default=None, max_length=150)
    registration_no: str | None = Field(default=None, max_length=120)
    primary_color: str | None = Field(default=None, max_length=7)
    document_footer: str | None = Field(default=None, max_length=300)
    bank_name: str | None = Field(default=None, max_length=120)
    bank_account_title: str | None = Field(default=None, max_length=150)
    bank_account_no: str | None = Field(default=None, max_length=60)
    bank_branch: str | None = Field(default=None, max_length=150)
    signatory_name: str | None = Field(default=None, max_length=120)
    signatory_title: str | None = Field(default=None, max_length=120)


@router.get("/profile", summary="The institute's name, contact details, logos, colour, bank details and signatory (administrator)")
async def get_profile(user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await service.get(db)


@router.put("/profile", summary="Change the institute profile; shows on every page, PDF and SMS within seconds (administrator)")
async def put_profile(body: ProfileIn, user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await service.update(db, body.model_dump(exclude_unset=True), actor_id=user.id)


@router.post("/images/{kind}", summary="Upload the logo, the logo for dark backgrounds, or the signature (PNG or JPG, at most 2 MB)")
async def upload_image(kind: str, file: UploadFile = File(...), user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    content = await file.read(service.MAX_IMAGE_BYTES + 1)
    return await service.save_image(db, kind, content, file.content_type, actor_id=user.id)


@router.delete("/images/{kind}", summary="Remove an uploaded logo or signature")
async def delete_image(kind: str, user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await service.remove_image(db, kind, actor_id=user.id)


@router.get("/signature.png", summary="The signature image (administrator only; it is printed on documents, never public)")
async def signature(user: CurrentUser = Depends(manage)) -> Response:
    p = brand.signature_path()
    if p is None:
        return Response(status_code=404)
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/preview/challan.pdf", summary="A sample challan with the current profile (to check the look before printing real ones)")
async def preview_challan(user: CurrentUser = Depends(manage)) -> Response:
    from datetime import date

    from starlette.concurrency import run_in_threadpool

    from app.fees.challan_pdf import render_challan_pdf

    data = {"consumer_number": "0000000000000", "payer": {"name": "Sample Student", "identifier": "26001", "group": "BS Computer Science"},
            "fee_name": "Tuition fee (sample)", "installment_text": "1 of 2", "charged": 25000.0, "concession": 2500.0, "payable": 22500.0,
            "due_date": date.today(), "late_fee_per_day": 100, "status": "PENDING", "generated_on": date.today()}
    pdf = await run_in_threadpool(render_challan_pdf, data)
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="sample-challan.pdf"'})


# ---------------------------------------------------------------- public files (used on the sign-in page, before sign-in)
def _image(path, fallback_svg: bool = True) -> Response:
    if path is not None:
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})
    if not fallback_svg:
        return Response(status_code=404)
    # No logo uploaded: a simple badge with the first letter, in the institute's colour.
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="{brand.color("#2454d6")}"/>'
           f'<text x="32" y="43" font-family="Arial,sans-serif" font-size="34" font-weight="700" fill="#fff" text-anchor="middle">{brand.initials()}</text></svg>')
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})


@public_router.get("/branding/logo.png", include_in_schema=False)
async def logo() -> Response:
    return _image(brand.logo_path())


@public_router.get("/branding/logo-dark.png", include_in_schema=False)
async def logo_dark() -> Response:
    return _image(brand.logo_path(dark=True))


@public_router.get("/branding/favicon.png", include_in_schema=False)
async def favicon() -> Response:
    from pathlib import Path

    p = Path(settings.institute_favicon_path or "")
    return _image(p if str(p) and p.is_file() else None)
