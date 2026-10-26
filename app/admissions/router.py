"""Admissions office: rounds, applications, verification, merit lists, admitting."""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from app.core.csvsafe import safe_writer
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.admissions import models as _models  # noqa: F401  (registers the tables)
from app.admissions.models import ApplicantStatus
from app.admissions.service import AdmissionService
from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_permission

router = APIRouter(prefix="/admissions", tags=["admissions"])
manage = require_permission(Permissions.ADMISSION_MANAGE)


class RoundIn(BaseModel):
    term_label: str = Field(min_length=1, max_length=50)
    program_id: uuid.UUID
    seats: int = Field(ge=1, le=10000)
    weight_matric: float = Field(ge=0, le=100)
    weight_inter: float = Field(ge=0, le=100)
    weight_test: float = Field(ge=0, le=100)
    min_inter_percent: float = Field(default=45, ge=0, le=100)
    closes_on: date | None = None


class ApplicationIn(BaseModel):
    """The student-record details (same fields and rules as creating a student) plus marks."""

    model_config = ConfigDict(extra="allow")
    matric_obtained: float = Field(ge=0)
    matric_total: float = Field(gt=0)
    inter_obtained: float = Field(ge=0)
    inter_total: float = Field(gt=0)
    test_obtained: float | None = Field(default=None, ge=0)
    test_total: float | None = Field(default=None, gt=0)


class StatusIn(BaseModel):
    status: ApplicantStatus
    remarks: str | None = Field(default=None, max_length=300)


class MeritIn(BaseModel):
    offer_days: int = Field(default=7, ge=1, le=60)


@router.get("/rounds", summary="Admission rounds with application counts and free seats")
async def rounds(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await AdmissionService(db).rounds()


@router.post("/rounds", status_code=201, summary="Open an admission round for a program and term")
async def create_round(payload: RoundIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    r = await AdmissionService(db).create_round(**payload.model_dump(), actor_id=current_user.id)
    return {"id": r.id}


@router.post("/rounds/{round_id}/close", summary="Close a round (no more applications or merit lists)")
async def close_round(round_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    r = await AdmissionService(db).close_round(round_id)
    return {"id": r.id, "status": r.status.value}


@router.get("/rounds/{round_id}/applicants", summary="Applications in a round, highest aggregate first")
async def applicants(round_id: uuid.UUID, status: ApplicantStatus | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await AdmissionService(db).applicants(round_id, status)


@router.post("/rounds/{round_id}/applicants", status_code=201, summary="Enter an application (checked with the student-record rules)")
async def apply(round_id: uuid.UUID, payload: ApplicationIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    a = await AdmissionService(db).apply(round_id, payload.model_dump(), actor_id=current_user.id)
    return AdmissionService.applicant_dict(a)


@router.post("/applicants/{applicant_id}/status", summary="Verify, reject (with reason) or withdraw an application")
async def set_status(applicant_id: uuid.UUID, payload: StatusIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    a = await AdmissionService(db).set_status(applicant_id, status=payload.status, remarks=payload.remarks)
    return AdmissionService.applicant_dict(a)


@router.post("/rounds/{round_id}/merit-lists", summary="Publish the next merit list: offers the free seats to the top verified applicants")
async def publish_merit(round_id: uuid.UUID, payload: MeritIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await AdmissionService(db).publish_merit_list(round_id, offer_days=payload.offer_days, actor_id=current_user.id)


@router.post("/rounds/{round_id}/expire-offers", summary="Expire offers whose deadline has passed (frees their seats)")
async def expire(round_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return {"expired": await AdmissionService(db).expire_offers(round_id)}


@router.get("/rounds/{round_id}/merit-lists/{list_no}.pdf", summary="Merit list for the notice board (PDF, no CNICs)")
async def merit_pdf(round_id: uuid.UUID, list_no: int, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    from app.admissions.merit_pdf import render_merit_list_pdf

    m = await AdmissionService(db).merit_list(round_id, list_no)
    return Response(content=await run_in_threadpool(render_merit_list_pdf, m), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="merit-list-{list_no}-{m["program"]}.pdf"'})


@router.post("/applicants/{applicant_id}/admit", summary="Admit an offered applicant: creates the student record (roll number, login)")
async def admit(applicant_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await AdmissionService(db).admit(applicant_id, actor_id=current_user.id)


@router.get("/applications-template.csv", summary="CSV template for importing applications")
async def template(current_user: CurrentUser = Depends(manage)) -> Response:
    from app.admissions.service import CSV_COLUMNS

    sample = ["Ali", "Khan", "2007-03-14", "Male", "35202-1234567-1", "0300-1234567", "Single", "Islam", "Pakistani", "Imran Khan",
              "35202-7654321-3", "Father", "0321-1234567", "House 1, Street 2", "Punjab", "Lahore", "Lahore", "950", "1100", "880", "1100",
              "0321-1234567", "", "72", "100"]
    import csv
    import io

    buf = io.StringIO()
    w = safe_writer(buf, lineterminator="\r\n")       # quotes values that contain commas, e.g. addresses
    w.writerow(CSV_COLUMNS)
    w.writerow(sample)
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="applications-template.csv"'})


@router.post("/rounds/{round_id}/applicants/import", summary="Import applications from CSV; dry_run=true only checks. Bad rows are listed and skipped.")
async def import_csv(round_id: uuid.UUID, file: UploadFile, dry_run: bool = True, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        from app.core.exceptions import ValidationAppError

        raise ValidationAppError("The file is larger than 5 MB. Split it.")
    return await AdmissionService(db).import_csv(round_id, raw, actor_id=current_user.id, dry_run=dry_run)