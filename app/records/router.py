from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth.permissions import Permissions, Roles
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.core.storage import MAX_ATTACHMENT_BYTES, SAFE_SERVE_HEADERS, attachment_is_inline, get_file_path
from app.records.pdf import render_certificate_pdf, render_id_cards_pdf
from app.records.service import CERTIFICATES, DOCUMENTS, OWNER, RecordsService, reference_of, verify_url

router = APIRouter(prefix="/records", tags=["records"])
public_router = APIRouter(prefix="/records", tags=["records (public)"])
issue = require_permission(Permissions.RECORDS_ISSUE)
manage = require_permission(Permissions.RECORDS_DOCUMENTS)
OwnerKind = Literal["students", "employees"]


class ReasonIn(BaseModel):
    reason: str = Field(min_length=4, max_length=300)


class IdCardsIn(BaseModel):
    student_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    employee_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


def _pdf(content: bytes, name: str) -> Response:
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store", **SAFE_SERVE_HEADERS})


async def _certificate_pdf(result: dict) -> Response:
    d = result["doc"]
    pdf = await run_in_threadpool(
        render_certificate_pdf, institute=result["institute"], kind=d.kind, reference=reference_of(d), code=d.verification_code, verify_url=verify_url(d.verification_code),
        paragraphs=result["paragraphs"], issued_on=result["issued_on"])
    return _pdf(pdf, f"{d.kind.lower()}-certificate-{reference_of(d)}.pdf")


@public_router.get("/verify/{code}", summary="Check a certificate or ID card by its code (public)")
async def verify_json(code: str, db=Depends(get_db)) -> dict:
    result = await RecordsService(db).verify(code[:20])
    if result is None:
        raise ResourceNotFoundError("No certificate or card has this code.")
    return result


@router.get("/kinds", summary="Kinds of certificate and document")
async def kinds(current_user: CurrentUser = Depends(get_current_user)) -> dict:
    return {"certificates": CERTIFICATES, "documents": DOCUMENTS}


# ------------------------------------------------------------------ a student's own page
async def _me(user: CurrentUser, db):
    if Roles.STUDENT not in user.roles:
        raise AuthorizationError("This page is for students.")
    return await RecordsService(db).student_of_user(user.id)


@router.get("/me/degree-audit", summary="My own degree progress")
async def my_audit(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    s = await _me(current_user, db)
    return await RecordsService(db).degree_audit(s.id)


# Kinds of document the office issues itself: a person cannot add these to their own file.
SELF_BLOCKED = {"STUDENT": {"CONTRACT", "EXPERIENCE"}, "EMPLOYEE": {"CONTRACT"}}


@router.get("/me/documents", summary="Documents I have given the office (students and staff)")
async def my_documents(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    owner_type, owner_id = await RecordsService(db).owner_of_user(current_user.id)
    return await RecordsService(db).list_documents(owner_type, owner_id, include_archived=False)


@router.post("/me/documents", status_code=201, summary="Upload a document for the office to check (students and staff)")
async def upload_my_document(kind: str = Form(...), title: str | None = Form(default=None), file: UploadFile = File(...),
                             current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    owner_type, owner_id = await RecordsService(db).owner_of_user(current_user.id)
    if kind.upper() in SELF_BLOCKED[owner_type]:
        raise AuthorizationError("The office adds this kind of document; you cannot upload it yourself.")
    return await RecordsService(db).add_document(owner_type, owner_id, kind=kind, title=title, content=await file.read(MAX_ATTACHMENT_BYTES + 1),
                                                 filename=file.filename or "file", content_type=file.content_type, actor_id=current_user.id)


@router.get("/me/completeness", summary="How complete my profile is, and what is still missing (students and staff)")
async def my_completeness(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.records.completeness import completeness

    owner_type, owner_id = await RecordsService(db).owner_of_user(current_user.id)
    return (await completeness(db, owner_type, [owner_id]))[owner_id]


class CompletenessIn(BaseModel):
    type: OwnerKind
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


@router.post("/completeness", summary="Profile completion for many students or employees at once (for the lists)")
async def completeness_many(payload: CompletenessIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.records.completeness import completeness

    return {str(k): v for k, v in (await completeness(db, OWNER[payload.type], payload.ids)).items()}


# ------------------------------------------------------------------ office
@router.post("/id-cards", summary="ID cards for students and / or staff (a PDF, eight to a page)")
async def id_cards(payload: IdCardsIn, current_user: CurrentUser = Depends(issue), db=Depends(get_db)) -> Response:
    result = await RecordsService(db).id_cards(student_ids=payload.student_ids, employee_ids=payload.employee_ids, actor_id=current_user.id)
    return _pdf(await run_in_threadpool(render_id_cards_pdf, institute=result["institute"], cards=result["cards"]), "id-cards.pdf")


@router.post("/students/{student_id}/certificates/{kind}", summary="Issue a bonafide, character or completion certificate (a PDF)")
async def student_certificate(student_id: uuid.UUID, kind: str, override_reason: str | None = Form(default=None), current_user: CurrentUser = Depends(issue), db=Depends(get_db)) -> Response:
    return await _certificate_pdf(await RecordsService(db).issue_student_certificate(student_id, kind, actor_id=current_user.id, override_reason=override_reason))


@router.post("/employees/{employee_id}/certificates/experience", summary="Issue an experience certificate (a PDF)")
async def experience_certificate(employee_id: uuid.UUID, override_reason: str | None = Form(default=None), current_user: CurrentUser = Depends(issue), db=Depends(get_db)) -> Response:
    return await _certificate_pdf(await RecordsService(db).issue_experience_certificate(employee_id, actor_id=current_user.id, override_reason=override_reason))


@router.post("/issued/{issued_id}/revoke", summary="Cancel a certificate or card")
async def revoke(issued_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(issue), db=Depends(get_db)) -> dict:
    return await RecordsService(db).revoke(issued_id, payload.reason, actor_id=current_user.id)


@router.get("/students/{student_id}/degree-audit", summary="A student's degree progress against their program")
async def student_audit(student_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await RecordsService(db).degree_audit(student_id)


@router.get("/{owner_kind}/{owner_id}/clearance", summary="Anything still outstanding before this person can be cleared")
async def clearance(owner_kind: OwnerKind, owner_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await RecordsService(db).clearance(OWNER[owner_kind], owner_id)


@router.get("/{owner_kind}/{owner_id}/issued", summary="Certificates and cards issued to this person")
async def issued(owner_kind: OwnerKind, owner_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await RecordsService(db).issued_for(OWNER[owner_kind], owner_id)


@router.get("/{owner_kind}/{owner_id}/documents", summary="Documents on file")
async def documents(owner_kind: OwnerKind, owner_id: uuid.UUID, include_archived: bool = False, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await RecordsService(db).list_documents(OWNER[owner_kind], owner_id, include_archived=include_archived)


@router.get("/{owner_kind}/{owner_id}/documents/all.pdf", summary="Everything on this person's file as one PDF (cover page + every document)")
async def documents_bundle(owner_kind: OwnerKind, owner_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    from app.core.clock import institute_now
    from app.records.bundle import build_bundle

    svc = RecordsService(db)
    person, parts = await svc.bundle_parts(OWNER[owner_kind], owner_id)
    pdf = await run_in_threadpool(build_bundle, person, parts, institute_now())
    await svc.audit.log(actor_user_id=current_user.id, action="records.documents_bundle", entity_type=OWNER[owner_kind].lower(), entity_id=owner_id,
                        metadata={"files": len(parts)})
    safe = "".join(ch if ch.isalnum() else "-" for ch in (person["code"] or person["name"]))[:40] or "person"
    return _pdf(pdf, f"documents-{safe}.pdf")


@router.get("/{owner_kind}/{owner_id}/profile-files", summary="The photo and combined certificate PDF on this person's profile")
async def profile_files(owner_kind: OwnerKind, owner_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await RecordsService(db).profile_files(OWNER[owner_kind], owner_id)


@router.post("/{owner_kind}/{owner_id}/documents", status_code=201, summary="Add a document to someone's file")
async def add_document(owner_kind: OwnerKind, owner_id: uuid.UUID, kind: str = Form(...), title: str | None = Form(default=None), file: UploadFile = File(...),
                       current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await RecordsService(db).add_document(OWNER[owner_kind], owner_id, kind=kind, title=title, content=await file.read(MAX_ATTACHMENT_BYTES + 1), filename=file.filename or "file",
                                                 content_type=file.content_type, actor_id=current_user.id)


@router.get("/documents/{document_id}/file", summary="Open a document (the office, or the student it belongs to)")
async def document_file(document_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    svc = RecordsService(db)
    d = await svc.document(document_id)
    if Permissions.RECORDS_DOCUMENTS not in current_user.permissions:
        try:
            own = await svc.owner_of_user(current_user.id)
        except ResourceNotFoundError:
            own = None
        if own != (d.owner_type, d.owner_id):
            raise AuthorizationError("This document is not yours.")
    path = get_file_path(d.storage_key)
    if path is None or not path.exists():
        raise ResourceNotFoundError("The file is missing from storage.")
    disp = "inline" if attachment_is_inline(d.content_type) else "attachment"
    return Response(content=path.read_bytes(), media_type=d.content_type, headers={"Content-Disposition": f'{disp}; filename="{d.original_name}"', "Cache-Control": "no-store", **SAFE_SERVE_HEADERS})


@router.post("/documents/{document_id}/verify", summary="Mark a document as checked against the original")
async def verify_document(document_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await RecordsService(db).verify_document(document_id, actor_id=current_user.id)


@router.post("/documents/{document_id}/archive", summary="Archive a document (kept on record)")
async def archive_document(document_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await RecordsService(db).archive_document(document_id, payload.reason, actor_id=current_user.id)