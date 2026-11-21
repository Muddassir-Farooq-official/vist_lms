"""Fee defaulters page (app/fees/defaulters.py): the list by batch term or session with CSV, SMS reminders (previewed
first), promises to pay, and admit-card exemptions for fees (administrators only)."""
from __future__ import annotations

import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.permissions import Permissions
from app.core.csvsafe import safe_writer
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/fee-defaulters", tags=["fee defaulters"])
view = require_permission(Permissions.ACCOUNT_VIEW)          # the administration and the accounts office
exempt_access = require_permission(Permissions.FEE_MANAGE)   # the administration only


class Filters(BaseModel):
    term_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    program_id: uuid.UUID | None = None
    semester: int | None = Field(default=None, ge=1, le=12)
    min_amount: float | None = Field(default=None, ge=0)
    min_days: int | None = Field(default=None, ge=0, le=3650)


def _filters(term_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None, program_id: uuid.UUID | None = None,
             semester: int | None = None, min_amount: float | None = None, min_days: int | None = None) -> Filters:
    return Filters(term_id=term_id, session_id=session_id, program_id=program_id, semester=semester, min_amount=min_amount, min_days=min_days)


@router.get("", summary="Students with overdue fees: by batch term or session, program, semester, days overdue and amount")
async def list_defaulters(f: Filters = Depends(_filters), current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    from app.fees.defaulters import defaulters

    return await defaulters(db, **f.model_dump())


@router.get("/export.csv", summary="The defaulter list as a spreadsheet (same filters)")
async def defaulters_csv(f: Filters = Depends(_filters), current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> Response:
    from app.fees.defaulters import csv_rows, defaulters

    data = await defaulters(db, **f.model_dump())
    header, rows = csv_rows(data)
    buf = io.StringIO()
    w = safe_writer(buf, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="fee-defaulters-{data["as_of"]}.csv"', "Cache-Control": "no-store"})


@router.get("/choices", summary="Sessions, batch terms and programs to filter by")
async def choices(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    from app.academics.terms import AcademicSession, AcademicTerm, TermStatus
    from app.fees.defaulters import DEFAULT_MESSAGE, PLACEHOLDERS
    from app.programs.models import Program

    terms = (await db.execute(select(AcademicTerm).order_by(AcademicTerm.status, AcademicTerm.label))).scalars().all()
    return {"sessions": [{"id": s.id, "name": s.name} for s in (await db.execute(select(AcademicSession).order_by(AcademicSession.name))).scalars()],
            "terms": [{"id": t.id, "label": t.label, "open": t.status == TermStatus.OPEN, "session_id": t.session_id} for t in terms],
            "programs": [{"id": p.id, "code": p.code, "name": p.name} for p in (await db.execute(select(Program).order_by(Program.code))).scalars()],
            "default_message": DEFAULT_MESSAGE, "placeholders": list(PLACEHOLDERS)}


class RemindIn(BaseModel):
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)
    to: str = Field(pattern="^(STUDENT|PARENT|BOTH)$")
    message: str | None = Field(default=None, max_length=400)
    dry_run: bool = True


@router.post("/remind", summary="SMS the chosen defaulters now (dry_run=true previews; at most one fee SMS per student per day)")
async def remind_now(payload: RemindIn, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    from app.fees.defaulters import remind

    return await remind(db, student_ids=payload.student_ids, to=payload.to, message=payload.message, dry_run=payload.dry_run,
                        actor_id=current_user.id)


# ---------------------------------------------------------------- promises to pay
class PromiseIn(BaseModel):
    student_id: uuid.UUID
    promised_date: date
    amount: float | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=300)


@router.get("/promises", summary="Promises to pay (running ones; include_old adds cancelled and past ones)")
async def promises(include_old: bool = False, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    from app.fees.defaulters import list_promises

    return await list_promises(db, include_old=include_old)


@router.post("/promises", status_code=201, summary="Record a promise to pay by a date: no fee reminder and no admit-card fee hold until then")
async def add_promise(payload: PromiseIn, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    from app.fees.defaulters import add_promise as add, promise_dict
    from app.students.models import Student

    row = await add(db, student_id=payload.student_id, promised_date=payload.promised_date, amount=payload.amount, note=payload.note,
                    actor_id=current_user.id)
    return {**promise_dict(row, await db.get(Student, row.student_id)), "active": True}


@router.post("/promises/{promise_id}/cancel", summary="Cancel a promise to pay (kept on record)")
async def cancel_promise(promise_id: uuid.UUID, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    from app.fees.defaulters import cancel_promise as cancel, promise_dict

    return {**promise_dict(await cancel(db, promise_id, actor_id=current_user.id)), "active": False}


# ---------------------------------------------------------------- admit card allowed despite overdue fees (administrators)
class ExemptionIn(BaseModel):
    student_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)
    until: date | None = None


@router.get("/exemptions", summary="Students whose admit card is printed despite overdue fees")
async def exemptions(include_old: bool = False, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    from app.students.regdeadline import list_exemptions

    return await list_exemptions(db, "FEES", include_old=include_old)


@router.post("/exemptions", status_code=201, summary="Allow one student's admit card despite overdue fees, with a reason (administrators)")
async def add_exemption(payload: ExemptionIn, current_user: CurrentUser = Depends(exempt_access), db=Depends(get_db)) -> dict:
    from app.students.models import Student
    from app.students.regdeadline import add_exemption as add, exemption_dict

    row = await add(db, student_id=payload.student_id, kind="FEES", reason=payload.reason, until=payload.until, actor_id=current_user.id)
    return {**exemption_dict(row, await db.get(Student, row.student_id)), "active": True}


@router.post("/exemptions/{exemption_id}/revoke", summary="Revoke a fee exemption: the hold applies again (kept on record)")
async def revoke_exemption(exemption_id: uuid.UUID, current_user: CurrentUser = Depends(exempt_access), db=Depends(get_db)) -> dict:
    from app.students.regdeadline import exemption_dict, revoke_exemption as revoke

    return {**exemption_dict(await revoke(db, exemption_id, actor_id=current_user.id, kind="FEES")), "active": False}
