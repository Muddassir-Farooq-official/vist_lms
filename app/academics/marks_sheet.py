"""Marks as a spreadsheet: download the roster with one column per assessment component (marks already
entered are filled in), fill it in Excel, upload it. Every cell goes through GradingService.enter_mark,
so the rules are exactly those of entering a mark by hand (0..max, locked once submitted, ...).

- A blank cell is left as it is.  - "Check" (dry_run) saves nothing.
- Rows with a problem are listed and skipped; good rows are saved.
- Only while the result sheet is a draft (or sent back). Published marks are corrected one by one, with a reason.
"""
from __future__ import annotations

from app.core.csvsafe import safe_writer
import csv
import io
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import AssessmentType, Enrollment, EnrollmentStatus, OfferingAssessment, ResultSheet, ResultSheetStatus, StudentMark
from app.core.exceptions import AppError, BusinessRuleViolationError, ValidationAppError

FIXED = ["roll_number", "student_name"]


def _col(t: AssessmentType) -> str:
    return f"{t.name} (/{float(t.max_marks):g})"


class MarksSheetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _components(self, offering_id: uuid.UUID) -> list[tuple[OfferingAssessment, AssessmentType]]:
        rows = (await self.session.execute(select(OfferingAssessment, AssessmentType).join(AssessmentType, AssessmentType.id == OfferingAssessment.assessment_type_id)
                                           .where(OfferingAssessment.course_offering_id == offering_id))).all()
        if not rows:
            raise BusinessRuleViolationError("Add the assessment components (e.g. Sessional, Final) to this course first.")
        return sorted(rows, key=lambda r: r[0].created_at)        # the order they were added to the course

    async def _roster(self, offering_id: uuid.UUID) -> list[tuple[Enrollment, object]]:
        from app.students.models import Student

        rows = (await self.session.execute(select(Enrollment, Student).join(Student, Student.id == Enrollment.student_id)
                                           .where(Enrollment.course_offering_id == offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN))).all()
        return sorted(rows, key=lambda r: r[1].roll_number or "")

    async def template(self, offering_id: uuid.UUID) -> str:
        comps = await self._components(offering_id)
        roster = await self._roster(offering_id)
        marks = {(m.enrollment_id, m.offering_assessment_id): float(m.obtained_marks) for m in (await self.session.execute(
            select(StudentMark).where(StudentMark.enrollment_id.in_([e.id for e, _ in roster])))).scalars()} if roster else {}
        out = io.StringIO()
        w = safe_writer(out, lineterminator="\r\n")
        w.writerow(FIXED + [_col(t) for _, t in comps])
        for e, st in roster:
            w.writerow([st.roll_number, f"{st.first_name} {st.last_name}"] + [("" if (e.id, oa.id) not in marks else f"{marks[(e.id, oa.id)]:g}") for oa, _ in comps])
        return out.getvalue()

    async def upload(self, offering_id: uuid.UUID, raw: bytes, *, actor_id: uuid.UUID, is_admin: bool, dry_run: bool) -> dict:
        from app.academics.grading_service import GradingService

        sheet = (await self.session.execute(select(ResultSheet).where(ResultSheet.course_offering_id == offering_id))).scalar_one_or_none()
        if sheet is not None and sheet.status not in (ResultSheetStatus.DRAFT, ResultSheetStatus.RETURNED):
            raise BusinessRuleViolationError("These marks are submitted or published, so they cannot be uploaded. "
                                             "Published marks are corrected one at a time, with a reason.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationAppError("Save the file as CSV UTF-8.") from None
        reader = csv.reader(io.StringIO(text))
        header = [h.strip() for h in next(reader, [])]
        if not header or header[0].lower() != "roll_number":
            raise ValidationAppError("The first column must be roll_number. Download the sheet from this page and fill it in.")
        comps = await self._components(offering_id)
        by_name = {t.name.strip().lower(): (oa, t) for oa, t in comps}
        cols = {}
        for i, h in enumerate(header):
            if i < 2:
                continue
            name = re.sub(r"\s*\(/[\d.]+\)\s*$", "", h).strip().lower()
            if name not in by_name:
                raise ValidationAppError(f'Column "{h}" is not a component of this course. Download a fresh sheet.')
            cols[i] = by_name[name]
        enrol = {st.roll_number: e for e, st in await self._roster(offering_id)}
        grading = GradingService(self.session)
        saved, errors, rows_ok = 0, [], 0
        for line, row in enumerate(reader, start=2):
            if not row or not any(c.strip() for c in row):
                continue
            roll = row[0].strip()
            e = enrol.get(roll)
            if e is None:
                errors.append({"row": line, "message": f"{roll or '(blank)'} is not on this course's roster."})
                continue
            row_errors = []
            cells = []
            for i, (oa, t) in cols.items():
                v = row[i].strip() if i < len(row) else ""
                if v == "":
                    continue
                try:
                    cells.append((oa, t, float(v)))
                except ValueError:
                    row_errors.append(f"{t.name}: '{v}' is not a number")
            if row_errors:
                errors.append({"row": line, "message": f"{roll}: " + "; ".join(row_errors)})
                continue
            try:
                async with self.session.begin_nested() as sp:
                    for oa, t, v in cells:
                        try:
                            await grading.enter_mark(e.id, oa.id, obtained_marks=v, actor_id=actor_id, is_admin=is_admin)
                        except AppError as exc:
                            raise AppError(f"{t.name}: {exc}") from None
                    if dry_run:
                        await sp.rollback()
                saved += len(cells)
                rows_ok += 1
            except AppError as exc:
                errors.append({"row": line, "message": f"{roll}: {exc}"})
        return {"dry_run": dry_run, "rows_ok": rows_ok, "marks": saved, "errors": errors}