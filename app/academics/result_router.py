from __future__ import annotations

from starlette.concurrency import run_in_threadpool

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from fastapi import UploadFile
from app.core.exceptions import BusinessRuleViolationError
from app.attendance.holds import ensure_no_attendance_hold
from app.auth.permissions import Roles
from app.academics.models import ResultSheetStatus
from app.academics.grade_scale import GradeScaleService
from app.academics.result_sheet_service import ResultSheetService
from app.academics.transcript_pdf import render_transcript_pdf
from app.academics.transcript_service import TranscriptService
from app.academics.router import _can_manage_marks_for_offering
from app.academics.service import CourseOfferingService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import require_any_permission, CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.students.repository import StudentRepository
from pydantic import BaseModel, Field

router = APIRouter(prefix="/results", tags=["results"])


class PublishIn(BaseModel):
    """Optional. Needed only to publish before attendance is locked (administrator only)."""

    override_reason: str | None = Field(default=None, max_length=500)


approver = require_permission(Permissions.RESULT_APPROVE)
transcript_reader = require_any_permission(Permissions.RESULT_APPROVE, Permissions.ACADEMIC_OVERSIGHT)      # exam office, principal, HOD


class ReturnIn(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


class ScaleEntryIn(BaseModel):
    letter: str = Field(min_length=1, max_length=4)
    min_percent: float = Field(ge=0, le=100)
    grade_points: float = Field(ge=0, le=10)
    is_passing: bool


class ScaleIn(BaseModel):
    entries: list[ScaleEntryIn] = Field(min_length=2, max_length=20)


def _scale_out(entries) -> list[dict]:
    return [{"letter": e.letter, "min_percent": float(e.min_percent), "grade_points": float(e.grade_points), "is_passing": e.is_passing} for e in entries]


async def _own_or_admin(db, offering_id: uuid.UUID, user: CurrentUser) -> None:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_manage_marks_for_offering(db, offering, user):
        raise AuthorizationError("You are not the assigned teacher for this subject.")


# ============================================================ the teacher (or administration) of the subject
@router.get("/offerings/{offering_id}/sheet", summary="Where this subject's marks are in the approval process, and whether they can be submitted")
async def sheet_status(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _own_or_admin(db, offering_id, current_user)
    return await ResultSheetService(db).detail(offering_id)


@router.post("/offerings/{offering_id}/submit", summary="Submit this subject's marks for approval (locks them until approved or sent back)")
async def submit(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _own_or_admin(db, offering_id, current_user)
    service = ResultSheetService(db)
    await service.submit(offering_id, actor_id=current_user.id)
    return await service.detail(offering_id)


# ============================================================ the approver
@router.get("/sheets", summary="Result sheets by status (approver)")
async def sheets(status: ResultSheetStatus | None = None, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> list[dict]:
    return await ResultSheetService(db).listing(status)


@router.get("/sheets-outstanding", summary="Courses whose marks have not been submitted yet (draft, sent back or not started) -- who to chase (approver)")
async def sheets_outstanding(current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> list[dict]:
    return await ResultSheetService(db).outstanding()


@router.get("/sheets/{offering_id}", summary="One subject's marks for the whole class, with each student's projected result (approver)")
async def review(offering_id: uuid.UUID, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    return await ResultSheetService(db).review(offering_id)


@router.post("/sheets/{offering_id}/approve", summary="Approve a submitted sheet (approver)")
async def approve(offering_id: uuid.UUID, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    service = ResultSheetService(db)
    await service.approve(offering_id, actor_id=current_user.id)
    return await service.detail(offering_id)


@router.post("/sheets/{offering_id}/return", summary="Send a sheet back to the teacher with a reason (approver)")
async def return_sheet(offering_id: uuid.UUID, payload: ReturnIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    service = ResultSheetService(db)
    await service.return_sheet(offering_id, actor_id=current_user.id, reason=payload.reason)
    return await service.detail(offering_id)


@router.post("/sheets/{offering_id}/publish", summary="Publish an approved sheet: students see their marks and pass/fail is decided (approver)")
async def publish(offering_id: uuid.UUID, payload: PublishIn | None = None, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    service = ResultSheetService(db)
    await service.publish(offering_id, actor_id=current_user.id, override_reason=payload.override_reason if payload else None,
                          actor_is_admin=Roles.ADMIN in current_user.roles)
    return await service.detail(offering_id)


@router.post("/publish-approved", summary="Publish every approved sheet at once -- for result day (approver)")
async def publish_all(current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    return await ResultSheetService(db).publish_all_approved(actor_id=current_user.id)


# ============================================================ the grading scale
@router.get("/scale", summary="The grading scale: which letter and grade points each percentage earns")
async def get_scale(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return _scale_out(await GradeScaleService(db).entries())


@router.put("/scale", summary="Replace the grading scale (approver). Results already published keep the grade they were given")
async def put_scale(payload: ScaleIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    entries, warning = await GradeScaleService(db).replace([e.model_dump() for e in payload.entries], actor_id=current_user.id)
    return {"entries": _scale_out(entries), "warning": warning}


# ============================================================ the result card (transcript)
async def _own_student_id(db, user: CurrentUser):
    student = await StudentRepository(db).get_by_user_id(user.id)
    if student is None:
        raise AuthorizationError("Only students have a result card.")
    return student.id


async def _pdf(data: dict) -> Response:
    name = (data["student"]["roll_number"] or "student").replace("/", "-")
    return Response(content=await run_in_threadpool(render_transcript_pdf, data), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="result-card-{name}.pdf"', "X-Content-Type-Options": "nosniff"})


@router.get("/me/transcript", summary="My result card: every published result, term by term, with GPA and CGPA")
async def my_transcript(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    sid = await _own_student_id(db, current_user)
    await ensure_no_attendance_hold(db, sid, action="Your result card")
    return await TranscriptService(db).build(sid)


@router.get("/me/transcript.pdf", summary="My result card as a PDF")
async def my_transcript_pdf(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    sid = await _own_student_id(db, current_user)
    await ensure_no_attendance_hold(db, sid, action="Your result card")
    return await _pdf(await TranscriptService(db).build(sid))


@router.get("/students/{student_id}/transcript", summary="Any student's result card (approver)")
async def student_transcript(student_id: uuid.UUID, current_user: CurrentUser = Depends(transcript_reader), db=Depends(get_db)) -> dict:
    return await TranscriptService(db).build(student_id)


@router.get("/students/{student_id}/transcript.pdf", summary="Any student's result card as a PDF (approver)")
async def student_transcript_pdf(student_id: uuid.UUID, current_user: CurrentUser = Depends(transcript_reader), db=Depends(get_db)) -> Response:
    return await _pdf(await TranscriptService(db).build(student_id))


# ============================================================ past results (import)
@router.get("/history/template.csv", summary="CSV template for importing past results")
async def history_template(current_user: CurrentUser = Depends(require_permission(Permissions.RESULT_HISTORY_IMPORT))) -> Response:
    from app.academics.history_import import TEMPLATE

    return Response(content=TEMPLATE, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="past-results-template.csv"'})


@router.post("/history/import", summary="Import past results from a CSV (all-or-nothing). dry_run=true only checks the file.")
async def history_import(file: UploadFile, dry_run: bool = True,
                         current_user: CurrentUser = Depends(require_permission(Permissions.RESULT_HISTORY_IMPORT)), db=Depends(get_db)) -> dict:
    from app.academics.history_import import HistoryImportService

    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise BusinessRuleViolationError("The file is larger than 10 MB. Split it.")
    return await HistoryImportService(db).run(raw, actor_id=current_user.id, dry_run=dry_run)