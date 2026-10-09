"""Import past (pre-system) results so existing students start with a correct transcript and CGPA.

One CSV row = one student's final result in one course in one past term:

    roll_number, term_label, term_start_date, course_code, course_title, credit_hours, percentage, letter_grade

- percentage OR letter_grade is required (both is fine; they must agree with the
  grading scale). With only a letter, the percentage recorded is that grade's minimum.
- An unknown course_code is created (title + credit hours from the row).
- Each (course, term) becomes a closed, already-PUBLISHED offering in section
  "IMPORTED", so it never mixes with live classes; the result is frozen exactly
  as given and counts in GPA/CGPA, retakes included, like any published result.
- All-or-nothing: if any row has an error nothing is imported. dry_run=True only checks.
- Re-importing the same row is reported as already imported, never doubled.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections import Counter
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.grade_scale import GradeScaleService, grade_for
from app.academics.models import (
    Course,
    CourseOffering,
    Enrollment,
    EnrollmentOutcome,
    EnrollmentResult,
    EnrollmentStatus,
    ResultSheet,
    ResultSheetStatus,
)
from app.audit.service import AuditService
from app.core.clock import institute_today
from app.students.models import Student

COLUMNS = ["roll_number", "term_label", "term_start_date", "course_code", "course_title", "credit_hours", "percentage", "letter_grade"]
REQUIRED = ["roll_number", "term_label", "term_start_date", "course_code", "credit_hours"]
SECTION = "IMPORTED"
MAX_ROWS = 20000

TEMPLATE = ",".join(COLUMNS) + "\r\n" + "25001,Fall 2025,2025-09-01,CS101,Programming Fundamentals,3,78,B+\r\n"


class HistoryImportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run(self, raw: bytes, *, actor_id: uuid.UUID, dry_run: bool) -> dict:
        errors: list[dict] = []

        def err(row: int, field: str, message: str) -> None:
            if len(errors) < 500:
                errors.append({"row": row, "field": field, "message": message})

        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return self._report(0, [{"row": 0, "field": "file", "message": "The file is not UTF-8 text. Save it from Excel as 'CSV UTF-8'."}], dry_run)
        reader = csv.DictReader(io.StringIO(text))
        header = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED if c not in header]
        if missing or not ({"percentage", "letter_grade"} & set(header)):
            need = missing + ([] if {"percentage", "letter_grade"} & set(header) else ["percentage or letter_grade"])
            return self._report(0, [{"row": 1, "field": "header", "message": f"Missing column(s): {', '.join(need)}. Use the template."}], dry_run)
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in reader]
        if not rows:
            return self._report(0, [{"row": 1, "field": "file", "message": "The file has no data rows."}], dry_run)
        if len(rows) > MAX_ROWS:
            return self._report(len(rows), [{"row": 0, "field": "file", "message": f"At most {MAX_ROWS} rows per file. Split the file."}], dry_run)

        scale = await GradeScaleService(self.session).entries()
        by_letter = {e.letter.upper(): e for e in scale}
        students = {s.roll_number: s for s in (await self.session.execute(select(Student))).scalars() if s.roll_number}
        courses = {c.code.upper(): c for c in (await self.session.execute(select(Course))).scalars()}
        today = institute_today()

        parsed = []
        term_dates: dict[str, date] = {}
        new_courses: dict[str, tuple[str, int]] = {}
        seen: Counter = Counter()
        for i, r in enumerate(rows, start=2):           # row 1 is the header
            bad = False
            for f in REQUIRED:
                if not r.get(f):
                    err(i, f, "Required."); bad = True
            if bad:
                continue
            student = students.get(r["roll_number"])
            if student is None:
                err(i, "roll_number", f"No student with roll number {r['roll_number']}. Import the student first."); bad = True
            try:
                start = date.fromisoformat(r["term_start_date"])
                if start >= today:
                    err(i, "term_start_date", "Must be a past date (this import is for past terms only)."); bad = True
            except ValueError:
                err(i, "term_start_date", "Use YYYY-MM-DD."); bad = True
                start = None
            term = r["term_label"]
            if start and term in term_dates and term_dates[term] != start:
                err(i, "term_start_date", f"Term {term} was given start date {term_dates[term]} on an earlier row."); bad = True
            elif start:
                term_dates.setdefault(term, start)
            try:
                ch = int(r["credit_hours"])
                if not 0 <= ch <= 12:
                    raise ValueError
            except ValueError:
                err(i, "credit_hours", "A whole number from 0 to 12."); bad = True
                ch = None
            code = r["course_code"].upper()
            course = courses.get(code)
            if course is not None and ch is not None and int(course.credit_hours) != ch:
                err(i, "credit_hours", f"{code} is {course.credit_hours} credit hours in the system, not {ch}."); bad = True
            if course is None:
                if not r.get("course_title"):
                    err(i, "course_title", f"{code} is a new course: give its title."); bad = True
                elif code in new_courses and ch is not None and new_courses[code][1] != ch:
                    err(i, "credit_hours", f"{code} was given {new_courses[code][1]} credit hours on an earlier row."); bad = True
                elif ch is not None:
                    new_courses.setdefault(code, (r["course_title"][:200], ch))

            pct = None
            if r.get("percentage"):
                try:
                    pct = float(r["percentage"])
                    if not 0 <= pct <= 100:
                        raise ValueError
                except ValueError:
                    err(i, "percentage", "A number from 0 to 100."); bad = True
            letter = (r.get("letter_grade") or "").upper() or None
            if letter and letter not in by_letter:
                err(i, "letter_grade", f"{letter} is not on the grading scale ({', '.join(sorted(by_letter))})."); bad = True
            if pct is None and letter is None and not bad:
                err(i, "percentage", "Give the percentage or the letter grade."); bad = True
            if bad:
                continue
            if letter is None:
                passed = any(e.is_passing and float(e.min_percent) <= pct for e in scale)
                letter, gp = grade_for(scale, pct, passed)
            else:
                entry = by_letter[letter]
                gp = float(entry.grade_points)
                if pct is None:
                    pct = float(entry.min_percent)
                else:
                    expect, _ = grade_for(scale, pct, entry.is_passing)
                    if expect != letter:
                        err(i, "letter_grade", f"{pct:g}% is {expect} on the grading scale, not {letter}."); continue
            key = (r["roll_number"], code, term)
            seen[key] += 1
            if seen[key] > 1:
                err(i, "course_code", f"Same student, course and term appears more than once in the file."); continue
            passed = by_letter[letter].is_passing
            parsed.append({"row": i, "student": student, "code": code, "term": term, "start": start, "ch": ch,
                           "pct": round(pct, 2), "letter": letter, "gp": gp, "passed": passed})

        if errors:
            return self._report(len(rows), errors, dry_run)

        # Already imported? (same student + course + term in the IMPORTED section)
        existing = set()
        for (sid, code, term) in (await self.session.execute(
                select(Enrollment.student_id, Course.code, CourseOffering.term_label)
                .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
                .where(CourseOffering.section_label == SECTION))).all():
            existing.add((sid, code.upper(), term))
        todo = [p for p in parsed if (p["student"].id, p["code"], p["term"]) not in existing]
        summary = {"rows": len(rows), "to_import": len(todo), "already_imported": len(parsed) - len(todo),
                   "students": len({p["student"].id for p in todo}), "terms": sorted({p["term"] for p in todo}),
                   "new_courses": sorted(new_courses)}
        if dry_run or not todo:
            return {"dry_run": dry_run, "ok": True, "errors": [], **summary}

        now = datetime.now(timezone.utc)
        # Past terms join the term list as CLOSED (history only), so labels stay consistent.
        from app.academics.terms import AcademicTermService, TermStatus

        terms = AcademicTermService(self.session)
        for label in {p["term"] for p in todo}:
            if await terms.by_label(label) is None:
                await terms.create(label=label, start_date=term_dates.get(label), end_date=None, actor_id=actor_id, status=TermStatus.CLOSED)
        for code, (title, ch) in new_courses.items():
            course = Course(code=code, title=title, credit_hours=ch)
            self.session.add(course)
            courses[code] = course
        await self.session.flush()

        offerings: dict[tuple[str, str], CourseOffering] = {
            (o.course.code.upper(), o.term_label): o for o in (await self.session.execute(
                select(CourseOffering).where(CourseOffering.section_label == SECTION))).scalars()}
        for p in todo:
            okey = (p["code"], p["term"])
            if okey not in offerings:
                off = CourseOffering(course_id=courses[p["code"]].id, term_label=p["term"], section_label=SECTION,
                                     is_open_for_enrollment=False, term_start_date=p["start"], attendance_locked_at=now, content_locked_at=now)
                self.session.add(off)
                await self.session.flush()
                self.session.add(ResultSheet(course_offering_id=off.id, status=ResultSheetStatus.PUBLISHED, approved_by=actor_id,
                                             approved_at=now, published_by=actor_id, published_at=now))
                offerings[okey] = off
            off = offerings[okey]
            enr = Enrollment(student_id=p["student"].id, course_offering_id=off.id, status=EnrollmentStatus.COMPLETED,
                             outcome=EnrollmentOutcome.PASSED if p["passed"] else EnrollmentOutcome.FAILED, is_retake=False,
                             enrolled_at=datetime.combine(p["start"], time(9), tzinfo=timezone.utc))
            self.session.add(enr)
            await self.session.flush()
            self.session.add(EnrollmentResult(enrollment_id=enr.id, percentage=p["pct"], total_obtained=p["pct"], total_max=100,
                                              letter_grade=p["letter"], grade_points=p["gp"], credit_hours=p["ch"],
                                              outcome="PASSED" if p["passed"] else "FAILED", attendance_percentage=None,
                                              failed_for_attendance=False, computed_at=now))
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="results.history_imported", entity_type="enrollment_result",
                                             entity_id=None, metadata={k: v for k, v in summary.items() if k != "terms"} | {"terms": summary["terms"][:50]})
        return {"dry_run": False, "ok": True, "errors": [], **summary}

    @staticmethod
    def _report(n: int, errors: list[dict], dry_run: bool) -> dict:
        return {"dry_run": dry_run, "ok": False, "rows": n, "to_import": 0, "already_imported": 0, "students": 0, "terms": [],
                "new_courses": [], "errors": errors}