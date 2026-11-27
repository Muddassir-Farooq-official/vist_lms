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
    from app.academics.affiliation import university_parts_for_offering
    from app.academics.grading_schemes import COMPONENT_LABELS

    out = await ResultSheetService(db).review(offering_id)
    parts, program = await university_parts_for_offering(db, offering_id)
    # an affiliated program's sheet is internal marks only: the projected grades are not the result
    out["affiliated"] = ({"program": program.code, "university_parts": [COMPONENT_LABELS[p] for p in sorted(parts)]}
                         if program is not None and program.exam_body == "AFFILIATED" else None)
    return out


@router.post("/sheets/{offering_id}/approve", summary="Approve a submitted sheet (approver)")
async def approve(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.approvals.inbox import check

    await check(db, "RESULT_APPROVE", current_user, offering_id)   # result.approve as always, or whom a rule names
    service = ResultSheetService(db)
    await service.approve(offering_id, actor_id=current_user.id)
    return await service.detail(offering_id)


@router.post("/sheets/{offering_id}/return", summary="Send a sheet back to the teacher with a reason (approver)")
async def return_sheet(offering_id: uuid.UUID, payload: ReturnIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.approvals.inbox import check

    await check(db, "RESULT_APPROVE", current_user, offering_id)
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


# ============================================================ grading tables (per affiliating body) and mark splits
class BandIn(BaseModel):
    letter: str = Field(min_length=1, max_length=4)
    min_percent: float = Field(ge=0, le=100)
    grade_points: float = Field(ge=0, le=10)
    grade_points_low: float | None = Field(default=None, ge=0, le=10)
    is_passing: bool
    division: str | None = Field(default=None, max_length=40)
    remark: str | None = Field(default=None, max_length=60)


class GradingTableIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    body: str | None = Field(default=None, max_length=150)
    system: str = Field(default="SEMESTER", pattern="^(SEMESTER|ANNUAL)$")
    pass_percent: float = Field(ge=0, le=100)
    theory_pass_percent: float | None = Field(default=None, ge=0, le=100)
    practical_pass_percent: float | None = Field(default=None, ge=0, le=100)
    resit_cap_percent: float | None = Field(default=None, ge=0, le=100)
    min_gpa_promotion: float | None = Field(default=None, ge=0, le=10)
    probation_gpa: float | None = Field(default=None, ge=0, le=10)
    min_cgpa_degree: float | None = Field(default=None, ge=0, le=10)
    sliding_points: bool = False
    is_draft: bool = False
    notes: str | None = Field(default=None, max_length=2000)
    bands: list[BandIn] = Field(min_length=2, max_length=25)


class ProgramTableIn(BaseModel):
    scheme_id: uuid.UUID | None = None


class SplitIn(BaseModel):
    program_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    values: dict[str, float] | None = None       # {"SESSIONAL": 20, "MID": 30, "FINAL": 50, "PRACTICAL": 0}; null = remove


@router.get("/grading-tables", summary="Grading tables (one per affiliating body) with the programs using each")
async def grading_tables(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.academics.grading_schemes import GradingSchemeService

    return await GradingSchemeService(db).listing()


@router.post("/grading-tables", status_code=201, summary="Add a grading table (approver)")
async def add_grading_table(payload: GradingTableIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.grading_schemes import GradingSchemeService

    return await GradingSchemeService(db).save(payload.model_dump(), actor_id=current_user.id)


@router.put("/grading-tables/{scheme_id}", summary="Change a grading table (approver). Published results keep their grades")
async def edit_grading_table(scheme_id: uuid.UUID, payload: GradingTableIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.grading_schemes import GradingSchemeService

    return await GradingSchemeService(db).save(payload.model_dump(), actor_id=current_user.id, scheme_id=scheme_id)


@router.delete("/grading-tables/{scheme_id}", status_code=204, summary="Delete a grading table no program uses (approver)")
async def delete_grading_table(scheme_id: uuid.UUID, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> None:
    from app.academics.grading_schemes import GradingSchemeService

    await GradingSchemeService(db).delete(scheme_id, actor_id=current_user.id)


@router.post("/grading-tables/drafts", summary="Add ready-made DRAFT tables (AUST, KMU, Sarhad, Board) to check and edit (approver)")
async def add_draft_tables(current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.grading_schemes import GradingSchemeService

    return {"added": await GradingSchemeService(db).add_ready_made_drafts(actor_id=current_user.id)}


# ============================================================ affiliated programs: who conducts the exams
class ExamBodyIn(BaseModel):
    exam_body: str = Field(pattern="^(INSTITUTE|AFFILIATED)$")
    university_parts: list[str] = Field(default_factory=list, max_length=3)


@router.get("/programs-exam-mode", summary="Every program with its grading table and who conducts its exams (approver)")
async def programs_exam_mode(current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> list[dict]:
    from sqlalchemy import select as _select

    from app.programs.models import Program

    rows = (await db.execute(_select(Program).where(Program.is_active.is_(True)).order_by(Program.code))).scalars()
    return [{"id": p.id, "code": p.code, "name": p.name, "grading_scheme_id": p.grading_scheme_id, "exam_body": p.exam_body or "INSTITUTE",
             "university_parts": [x for x in (p.university_parts or "").split(",") if x]} for p in rows]


@router.put("/programs/{program_id}/exam-body", summary="Set who conducts a program's exams: the institute, or the affiliating university for some parts (approver)")
async def set_exam_body(program_id: uuid.UUID, payload: ExamBodyIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.affiliation import UNIVERSITY_CAN_TAKE
    from app.audit.service import AuditService
    from app.programs.models import Program

    program = await db.get(Program, program_id)
    if program is None:
        raise ResourceNotFoundError("Program not found.")
    parts = [p for p in UNIVERSITY_CAN_TAKE if p in {x.upper() for x in payload.university_parts}]
    if payload.exam_body == "AFFILIATED" and not parts:
        raise BusinessRuleViolationError("Tick at least one part the university conducts (mid, final or practical).")
    before = {"exam_body": program.exam_body, "university_parts": program.university_parts}
    program.exam_body = payload.exam_body
    program.university_parts = ",".join(parts) if payload.exam_body == "AFFILIATED" else None
    await db.flush()
    await AuditService(db).log(actor_user_id=current_user.id, action="program.exam_body_set", entity_type="program", entity_id=program.id,
                               before=before, after={"exam_body": program.exam_body, "university_parts": program.university_parts})
    return {"id": program.id, "exam_body": program.exam_body, "university_parts": parts if payload.exam_body == "AFFILIATED" else []}


@router.get("/sheets/{offering_id}/internal-award-list.{fmt}", summary="Internal award list for the university (PDF or CSV): internal marks with Reg No")
async def internal_award_list(offering_id: uuid.UUID, fmt: str, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    from app.academics.affiliation import internal_award_list as build, internal_award_list_csv
    from app.academics.models import CourseOffering

    if fmt not in ("pdf", "csv"):
        raise ResourceNotFoundError("Choose pdf or csv.")
    offering = await db.get(CourseOffering, offering_id)
    if offering is None:
        raise ResourceNotFoundError("Course offering not found.")
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("Only the subject's teacher or the exam office can print its award list.")
    data = await build(db, offering_id)
    name = f"internal-award-list-{data['course_code']}-{(offering.section_label or '').replace('/', '-')}"
    if fmt == "csv":
        return Response(content=internal_award_list_csv(data), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    from app.academics.result_documents_pdf import render_internal_award_list

    pdf = await run_in_threadpool(render_internal_award_list, data)
    return _pdf_response(pdf, f"{name}.pdf")


@router.get("/sheets/{offering_id}/reg-check", summary="Students of this subject without a university Reg No (for the internal award list)")
async def internal_award_reg_check(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.academics.models import CourseOffering
    from app.students.regdeadline import reg_check

    offering = await db.get(CourseOffering, offering_id)
    if offering is None:
        raise ResourceNotFoundError("Course offering not found.")
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("Only the subject's teacher or the exam office can see this.")
    return await reg_check(db, offering_id)


@router.get("/university/template.csv", summary="CSV template for the university's result (Reg No, course code, marks and/or grade)")
async def university_template(current_user: CurrentUser = Depends(approver)) -> Response:
    from app.academics.affiliation import template_csv

    return Response(content=template_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="university-result-template.csv"'})


@router.post("/university/import", summary="Import the university's result for a program and term (by Reg No). All-or-nothing; dry_run=true only checks.")
async def university_import(file: UploadFile, program_id: uuid.UUID, term_label: str, dry_run: bool = True, kind: str = "REGULAR",
                            current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    """kind=REGULAR: the first exam (re-import revises it). kind=SUPPLEMENTARY: a re-sit of a failed subject; it becomes
    the result that counts and the first attempt is kept."""
    from app.academics.affiliation import UniversityResultImport

    return await UniversityResultImport(db).run(await file.read(), program_id=program_id, term_label=term_label, dry_run=dry_run,
                                                actor_id=current_user.id, kind=kind)


@router.put("/programs/{program_id}/grading-table", summary="Choose the grading table a program's results use (approver); empty = institute default")
async def set_program_table(program_id: uuid.UUID, payload: ProgramTableIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.grading_schemes import GradingSchemeService

    await GradingSchemeService(db).assign(program_id, payload.scheme_id, actor_id=current_user.id)
    return {"program_id": program_id, "scheme_id": payload.scheme_id}


@router.get("/mark-splits", summary="Mark splits per program and per subject")
async def mark_splits(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.academics.grading_schemes import GradingSchemeService

    return await GradingSchemeService(db).splits()


@router.put("/mark-splits", summary="Set (or remove) a program's or a subject's mark split (approver)")
async def set_mark_split(payload: SplitIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.grading_schemes import GradingSchemeService

    values = {k.upper(): v for k, v in payload.values.items()} if payload.values is not None else None
    return {"split": await GradingSchemeService(db).set_split(program_id=payload.program_id, course_id=payload.course_id, values=values, actor_id=current_user.id)}


# ============================================================ award list, class result cards, gazette, promotion
def _pdf_response(content: bytes, filename: str) -> Response:
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"})


@router.get("/sheets/{offering_id}/award-list.pdf", summary="Award list of one subject (the subject's teacher or the exam office)")
async def award_list_pdf(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    from app.academics.grading_schemes import rules_for_program
    from app.academics.models import CourseOffering
    from app.academics.result_documents_pdf import render_award_list
    from app.programs.models import Program

    offering = await db.get(CourseOffering, offering_id)
    if offering is None:
        raise ResourceNotFoundError("Course offering not found.")
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("Only the subject's teacher or the exam office can print its award list.")
    sheet = await ResultSheetService(db).review(offering_id)
    from collections import Counter

    from sqlalchemy import select as _select

    from app.academics.models import Enrollment
    from app.students.models import Student

    ids = (await db.execute(_select(Student.program_id).join(Enrollment, Enrollment.student_id == Student.id)
                            .where(Enrollment.course_offering_id == offering_id, Student.program_id.is_not(None)))).scalars().all()
    common = Counter(ids).most_common(1)          # the class's program (by its students)
    program = await db.get(Program, common[0][0]) if common else None
    rules = await rules_for_program(db, program.id if program else None)
    grading = {"name": rules.name, "body": rules.body}
    pdf = await run_in_threadpool(render_award_list, sheet, grading=grading, program_label=f"{program.name} ({program.code})" if program else None)
    return _pdf_response(pdf, f"award-list-{sheet['course_code']}.pdf")


class PromoteIn(BaseModel):
    program_id: uuid.UUID
    semester: int = Field(ge=1, le=20)
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)


class UndoIn(BaseModel):
    batch: str = Field(min_length=10, max_length=60)


@router.get("/class", summary="A class's results for a term: GPA (or total/%/division), status and position (approver)")
async def class_results(program_id: uuid.UUID, semester: int, term_label: str, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.promotion import ClassResultService

    return await ClassResultService(db).build(program_id=program_id, semester=semester, term_label=term_label)


@router.get("/class/result-cards.pdf", summary="Semester (year) result cards, one page per student (approver)")
async def class_result_cards(program_id: uuid.UUID, semester: int, term_label: str, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> Response:
    from app.academics.promotion import ClassResultService
    from app.academics.result_documents_pdf import render_result_cards

    data = await ClassResultService(db).build(program_id=program_id, semester=semester, term_label=term_label)
    return _pdf_response(await run_in_threadpool(render_result_cards, data), f"result-cards-{data['program']['code']}-{semester}.pdf")


@router.get("/class/gazette.pdf", summary="Result gazette / notification for a program, semester and term (approver)")
async def class_gazette(program_id: uuid.UUID, semester: int, term_label: str, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> Response:
    from app.academics.promotion import ClassResultService
    from app.academics.result_documents_pdf import render_gazette

    data = await ClassResultService(db).build(program_id=program_id, semester=semester, term_label=term_label)
    return _pdf_response(await run_in_threadpool(render_gazette, data), f"gazette-{data['program']['code']}-{semester}.pdf")


@router.post("/class/promote", summary="Move the chosen students to the next semester (year) (approver; can be undone)")
async def promote(payload: PromoteIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.promotion import ClassResultService

    return await ClassResultService(db).promote(program_id=payload.program_id, semester=payload.semester, student_ids=payload.student_ids, actor_id=current_user.id)


@router.post("/class/promote/undo", summary="Undo a promotion (students who have not moved again go back) (approver)")
async def undo_promotion(payload: UndoIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    from app.academics.promotion import ClassResultService

    return await ClassResultService(db).undo(batch=payload.batch, actor_id=current_user.id)


# ============================================================ the result card (transcript)
async def _own_student_id(db, user: CurrentUser):
    student = await StudentRepository(db).get_by_user_id(user.id)
    if student is None:
        raise AuthorizationError("Only students have a result card.")
    return student.id


async def _not_held(db, student_id) -> None:
    """A suspended student's result card is held (not issued) until the suspension is lifted."""
    from app.students.models import StudentStatus
    from app.students.standing import ensure_not_locked

    await ensure_not_locked(db, student_id, "the result card", allow=(StudentStatus.DROPPED,))


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
    await _not_held(db, sid)
    return await _pdf(await TranscriptService(db).build(sid))


@router.get("/students/{student_id}/transcript", summary="Any student's result card (approver)")
async def student_transcript(student_id: uuid.UUID, current_user: CurrentUser = Depends(transcript_reader), db=Depends(get_db)) -> dict:
    return await TranscriptService(db).build(student_id)


@router.get("/students/{student_id}/transcript.pdf", summary="Any student's result card as a PDF (approver)")
async def student_transcript_pdf(student_id: uuid.UUID, current_user: CurrentUser = Depends(transcript_reader), db=Depends(get_db)) -> Response:
    await _not_held(db, student_id)
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