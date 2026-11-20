"""University registration numbers (Reg No) and the student field rules.

  * /students/field-rules            which details are required when adding / importing students (admin)
  * /students/reg-numbers            the class list with each student's Reg No, to fill in or correct (admin + exam office)
  * /students/reg-numbers/import     a CSV of roll_number, reg_number (all-or-nothing; dry_run only checks)
  * /students/{id}/reg-number        set or clear one student's Reg No
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_any_permission, require_permission
from app.core.exceptions import ValidationAppError
from app.students.models import Student, StudentStatus

router = APIRouter(prefix="/students", tags=["students"])
regno_access = require_any_permission(Permissions.STUDENT_UPDATE, Permissions.RESULT_APPROVE)
MAX_ROWS = 5000


# ---------------------------------------------------------------- field rules
class FieldRulesUpdate(BaseModel):
    rules: dict[str, bool] = Field(description="{column: required?}; first and last name are always required")


@router.get("/field-rules", summary="Which details are required when a student is added or imported")
async def get_field_rules(
    current_user: CurrentUser = Depends(require_any_permission(Permissions.STUDENT_CREATE, Permissions.STUDENT_IMPORT)), db=Depends(get_db),
) -> list[dict]:
    from app.students.field_rules import describe, get_rules

    return describe(await get_rules(db))


@router.put("/field-rules", summary="Change which details are required when a student is added or imported (admin)")
async def put_field_rules(payload: FieldRulesUpdate, current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_IMPORT)),
                          db=Depends(get_db)) -> list[dict]:
    from app.students.field_rules import describe, save_rules

    return describe(await save_rules(db, payload.rules, actor_id=current_user.id))


# ---------------------------------------------------------------- Reg No
class RegNoUpdate(BaseModel):
    reg_number: str | None = Field(default=None, max_length=60)


def _row(s: Student, programs: dict) -> dict:
    p = programs.get(s.program_id)
    return {"id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}", "program_code": p.code if p else None,
            "program_name": p.name if p else None, "current_semester": s.current_semester, "section_label": s.section_label,
            "reg_number": s.reg_number, "status": s.status.value}


async def _programs(db) -> dict:
    from app.programs.models import Program

    return {p.id: p for p in (await db.execute(select(Program))).scalars()}


@router.get("/reg-numbers", summary="Students with their university Reg No, by program / semester / section (admin, exam office)")
async def list_reg_numbers(
    program_id: uuid.UUID | None = None, semester: int | None = None, section: str | None = None, missing_only: bool = False,
    q: str | None = None, current_user: CurrentUser = Depends(regno_access), db=Depends(get_db),
) -> dict:
    stmt = select(Student).where(Student.status == StudentStatus.ACTIVE)
    if program_id:
        stmt = stmt.where(Student.program_id == program_id)
    if semester:
        stmt = stmt.where(Student.current_semester == semester)
    if section:
        stmt = stmt.where(func.lower(Student.section_label) == section.strip().lower())
    if missing_only:
        stmt = stmt.where(Student.reg_number.is_(None))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(Student.first_name + " " + Student.last_name).like(like)
                          | func.lower(Student.roll_number).like(like) | func.lower(func.coalesce(Student.reg_number, "")).like(like))
    rows = list((await db.execute(stmt.order_by(Student.roll_number).limit(1000))).scalars())
    programs = await _programs(db)
    return {"students": [_row(s, programs) for s in rows], "missing": sum(1 for s in rows if not s.reg_number), "truncated": len(rows) == 1000,
            "programs": [{"id": p.id, "code": p.code, "name": p.name} for p in sorted(programs.values(), key=lambda p: p.code)]}


@router.put("/{student_id}/reg-number", summary="Set or clear one student's university Reg No (admin, exam office)")
async def put_reg_number(student_id: uuid.UUID, payload: RegNoUpdate, current_user: CurrentUser = Depends(regno_access),
                         db=Depends(get_db)) -> dict:
    from app.students.service import StudentService

    s = await StudentService(db).set_reg_number(student_id, payload.reg_number, actor_id=current_user.id)
    return _row(s, await _programs(db))


@router.get("/reg-numbers/template.csv", summary="CSV to fill in Reg Nos: roll_number, name, reg_number (admin, exam office)")
async def reg_numbers_template(program_id: uuid.UUID | None = None, semester: int | None = None, section: str | None = None,
                               current_user: CurrentUser = Depends(regno_access), db=Depends(get_db)) -> Response:
    data = await list_reg_numbers(program_id=program_id, semester=semester, section=section, missing_only=False, q=None,
                                  current_user=current_user, db=db)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["roll_number", "name", "reg_number"])
    for r in data["students"]:
        w.writerow([r["roll_number"], _safe(r["name"]), r["reg_number"] or ""])
    return Response(content="﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="reg-numbers.csv"'})


def _safe(v: str) -> str:
    return "'" + v if v and v[0] in "=+-@\t\r" else v


@router.post("/reg-numbers/import", summary="Fill Reg Nos from a CSV (roll_number, reg_number). All-or-nothing; dry_run=true only checks.")
async def import_reg_numbers(file: UploadFile, dry_run: bool = True, current_user: CurrentUser = Depends(regno_access),
                             db=Depends(get_db)) -> dict:
    from app.audit.service import AuditService
    from app.imports.validators import _REG_NO_RE, normalize_reg_number

    raw = await file.read()
    if len(raw) > 2_000_000:
        raise ValidationAppError("The file is too big (at most 2 MB).")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationAppError("Save the file as CSV UTF-8.")
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    if "roll_number" not in headers or "reg_number" not in headers:
        raise ValidationAppError("The file needs the columns roll_number and reg_number.")
    rows = []
    for i, r in enumerate(reader, start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if not r.get("roll_number") and not r.get("reg_number"):
            continue
        rows.append((i, r.get("roll_number", ""), normalize_reg_number(r.get("reg_number"))))
        if len(rows) > MAX_ROWS:
            raise ValidationAppError(f"At most {MAX_ROWS} rows at a time.")
    rolls = {roll for _, roll, _ in rows if roll}
    students = {s.roll_number: s for s in (await db.execute(select(Student).where(Student.roll_number.in_(rolls)))).scalars()} if rolls else {}
    regs = {reg.upper() for _, _, reg in rows if reg}
    owners = {s.reg_number.upper(): s for s in (await db.execute(select(Student).where(func.upper(Student.reg_number).in_(regs)))).scalars()} if regs else {}
    errors, changes, seen, seen_roll = [], [], {}, {}
    for line, roll, reg in rows:
        s = students.get(roll)
        if s is None:
            errors.append({"line": line, "roll_number": roll, "error": "No student has this roll number."})
            continue
        if roll in seen_roll:
            errors.append({"line": line, "roll_number": roll, "error": f"Roll number repeated (line {seen_roll[roll]})."})
            continue
        seen_roll[roll] = line
        if reg and not _REG_NO_RE.match(reg):
            errors.append({"line": line, "roll_number": roll, "error": "Reg No may have letters, digits, spaces, '-', '/' and '.' (at most 40)."})
            continue
        if reg:
            if reg.upper() in seen:
                errors.append({"line": line, "roll_number": roll, "error": f"Reg No {reg} repeated (line {seen[reg.upper()]})."})
                continue
            seen[reg.upper()] = line
        if (s.reg_number or None) != reg:
            changes.append({"student": s, "roll_number": roll, "name": f"{s.first_name} {s.last_name}", "old": s.reg_number, "new": reg})
    # after the file, a Reg No may still belong to someone the file does not change (or changes to keep it)
    final = {c["student"].id: c["new"] for c in changes}
    for c in changes:
        owner = owners.get((c["new"] or "").upper())
        if c["new"] and owner is not None and owner.id != c["student"].id and (final.get(owner.id, owner.reg_number) or "").upper() == c["new"].upper():
            errors.append({"line": seen.get(c["new"].upper()), "roll_number": c["roll_number"],
                           "error": f"Reg No {c['new']} already belongs to {owner.roll_number} ({owner.first_name} {owner.last_name})."})
    out = {"dry_run": dry_run or bool(errors), "rows": len(rows), "errors": errors[:200], "error_count": len(errors),
           "changes": [{k: v for k, v in c.items() if k != "student"} for c in changes[:500]], "change_count": len(changes)}
    if errors or dry_run:
        return out
    # free every Reg No that moves first, so two students can swap numbers in one file
    for c in changes:
        c["student"].reg_number = None
    await db.flush()
    for c in changes:
        c["student"].reg_number = c["new"]
    await db.flush()
    await AuditService(db).log(actor_user_id=current_user.id, action="student.reg_numbers_imported", entity_type="student", entity_id=None,
                               metadata={"changed": len(changes), "file": (file.filename or "")[:100]})
    out["applied"] = len(changes)
    return out

