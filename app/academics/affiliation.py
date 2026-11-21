"""Affiliated programs: the university (KMU, AUST ...) conducts some exams; the institute keeps the internal marks.

Per program (Results -> Grading tables & mark split -> Programs):
  * exam_body INSTITUTE (degree-awarding, default): everything works as before;
  * exam_body AFFILIATED with university_parts, e.g. MID + FINAL: those parts are the university's. The institute cannot
    add those assessments to a subject, enter their marks, or put them on its own datesheet. Teachers enter internal marks
    (quiz 1 ... n, assignments, sessional, and any part the institute still runs); the exam office prints the internal
    award list (with Reg No) for the university. Publishing a sheet shows students their internal marks but decides
    nothing: the result is the university's, imported by Reg No (UniversityResultImport), and that result is what
    transcripts, GPA/CGPA and promotion use.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.grading_schemes import COMPONENT_LABELS, COMPONENTS, rules_for_program, split_for
from app.academics.models import (
    AssessmentType,
    Course,
    CourseOffering,
    CourseProgram,
    Enrollment,
    EnrollmentOutcome,
    EnrollmentResult,
    EnrollmentStatus,
    OfferingAssessment,
    StudentMark,
)
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

UNIVERSITY_CAN_TAKE = ("MID", "FINAL", "PRACTICAL")
MAX_ROWS = 20000


def parts_of(program) -> set[str]:
    if program is None or (program.exam_body or "INSTITUTE") != "AFFILIATED":
        return set()
    return {p for p in (program.university_parts or "").split(",") if p in UNIVERSITY_CAN_TAKE}


def is_affiliated(program) -> bool:
    return program is not None and (program.exam_body or "INSTITUTE") == "AFFILIATED"


async def program_of_offering(session: AsyncSession, offering: CourseOffering):
    """The program a subject's class belongs to: its batch term's program, else most of its students' program, else the
    course's only program."""
    from app.academics.terms import AcademicTerm
    from app.programs.models import Program
    from app.students.models import Student

    term = (await session.execute(select(AcademicTerm).where(func.lower(AcademicTerm.label) == (offering.term_label or "").lower()))).scalars().first()
    if term is not None and term.program_id:
        return await session.get(Program, term.program_id)
    ids = (await session.execute(select(Student.program_id).join(Enrollment, Enrollment.student_id == Student.id).where(
        Enrollment.course_offering_id == offering.id, Enrollment.status != EnrollmentStatus.WITHDRAWN, Student.program_id.is_not(None)))).scalars().all()
    if ids:
        return await session.get(Program, Counter(ids).most_common(1)[0][0])
    links = (await session.execute(select(CourseProgram.program_id).where(CourseProgram.course_id == offering.course_id))).scalars().all()
    if len(set(links)) == 1:
        return await session.get(Program, links[0])
    return None


async def university_parts_for_offering(session: AsyncSession, offering_id: uuid.UUID) -> tuple[set[str], object]:
    offering = await session.get(CourseOffering, offering_id)
    if offering is None:
        return set(), None
    program = await program_of_offering(session, offering)
    return parts_of(program), program


async def check_part_allowed(session: AsyncSession, offering_id: uuid.UUID, component: str | None, *, doing: str) -> None:
    """Refuse when the university conducts this part of the subject's program."""
    if not component:
        return
    parts, program = await university_parts_for_offering(session, offering_id)
    if component in parts:
        body = await _body_name(session, program)
        raise BusinessRuleViolationError(
            f"The {COMPONENT_LABELS[component].lower()} of {program.code} is conducted by {body}, so the institute cannot {doing}. "
            "Enter internal marks only; the university's result is imported later (Results -> University result).")


async def _body_name(session: AsyncSession, program) -> str:
    rules = await rules_for_program(session, program.id if program else None)
    return rules.body or (rules.name if rules.scheme_id else "the affiliating university")


# ---------------------------------------------------------------------- the internal award list
async def internal_award_list(session: AsyncSession, offering_id: uuid.UUID) -> dict:
    from app.students.models import Student

    offering = await session.get(CourseOffering, offering_id)
    if offering is None:
        raise ResourceNotFoundError("Course offering not found.")
    course = await session.get(Course, offering.course_id)
    program = await program_of_offering(session, offering)
    parts = parts_of(program)
    split = await split_for(session, course_id=offering.course_id, program_id=program.id if program else None)
    rows_oa = (await session.execute(select(OfferingAssessment, AssessmentType).join(AssessmentType, AssessmentType.id == OfferingAssessment.assessment_type_id)
                                     .where(OfferingAssessment.course_offering_id == offering_id).order_by(AssessmentType.name))).all()
    internal = [(oa, at) for oa, at in rows_oa if (at.component or "SESSIONAL") not in parts]
    comp_max: dict[str, float] = {}
    for _oa, at in internal:
        comp_max[at.component or "SESSIONAL"] = comp_max.get(at.component or "SESSIONAL", 0.0) + float(at.max_marks)
    if split:
        weights = {c: w for c, w in split.items() if w > 0 and c not in parts}
        internal_max = sum(weights.values())
        missing_parts = [COMPONENT_LABELS[c] for c in weights if c not in comp_max]
    else:
        weights, internal_max, missing_parts = None, sum(comp_max.values()), []
    enrolls = (await session.execute(select(Enrollment, Student).join(Student, Student.id == Enrollment.student_id).where(
        Enrollment.course_offering_id == offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN))).all()
    marks = {(m.enrollment_id, m.offering_assessment_id): float(m.obtained_marks) for m in (await session.execute(select(StudentMark).where(
        StudentMark.enrollment_id.in_([e.id for e, _ in enrolls])))).scalars()} if enrolls else {}
    out_rows = []
    for e, st in sorted(enrolls, key=lambda x: x[1].roll_number or ""):
        got = {}
        cells = []
        for oa, at in internal:
            v = marks.get((e.id, oa.id))
            cells.append(v)
            got[at.component or "SESSIONAL"] = got.get(at.component or "SESSIONAL", 0.0) + (v or 0.0)
        if weights is not None:
            obtained = sum(got.get(c, 0.0) / comp_max[c] * w for c, w in weights.items() if comp_max.get(c))
        else:
            obtained = sum(got.values())
        out_rows.append({"roll_number": st.roll_number, "reg_number": st.reg_number, "student_name": f"{st.first_name} {st.last_name}",
                         "marks": cells, "missing": sum(1 for v in cells if v is None),
                         "internal_obtained": round(obtained, 2), "internal_max": round(internal_max, 2),
                         "internal_percent": round(obtained / internal_max * 100, 2) if internal_max else None})
    from app.academics.terms import AcademicTerm

    term = (await session.execute(select(AcademicTerm).where(func.lower(AcademicTerm.label) == (offering.term_label or "").lower()))).scalars().first()
    return {"course_code": course.code if course else "?", "course_title": course.title if course else "", "term_label": offering.term_label,
            "reg_deadline": term.reg_deadline if term else None, "section_label": offering.section_label, "program": {"code": program.code, "name": program.name} if program else None,
            "body": await _body_name(session, program) if program else None,
            "university_parts": [COMPONENT_LABELS[p] for p in COMPONENTS if p in parts],
            "assessments": [{"name": at.name, "max_marks": float(at.max_marks), "part": COMPONENT_LABELS.get(at.component or "SESSIONAL")} for _oa, at in internal],
            "internal_max": round(internal_max, 2), "scaled": weights is not None, "missing_parts": missing_parts, "rows": out_rows}


def internal_award_list_csv(data: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["roll_number", "reg_number", "student_name"] + [f"{a['name']} (/{a['max_marks']:g})" for a in data["assessments"]]
               + [f"internal_total (/{data['internal_max']:g})"])
    safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v   # noqa: E731
    for r in data["rows"]:
        w.writerow([safe(r["roll_number"] or ""), safe(r["reg_number"] or ""), safe(r["student_name"])]
                   + ["" if v is None else f"{v:g}" for v in r["marks"]] + [f"{r['internal_obtained']:g}"])
    return "﻿" + out.getvalue()


# ---------------------------------------------------------------------- importing the university's result
TEMPLATE_COLUMNS = ["reg_number", "roll_number", "course_code", "marks_obtained", "marks_total", "grade", "result"]
PASS_WORDS = {"PASS": True, "PASSED": True, "P": True, "FAIL": False, "FAILED": False, "F": False, "ABSENT": False, "ABS": False}


def _num(v: str):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return "bad"


class UniversityResultImport:
    """One CSV row = one student's university result in one subject of the chosen program and term:
        reg_number (or roll_number), course_code, marks_obtained + marks_total (the university's figures for the whole
        subject), and/or grade, and optionally result (PASS / FAIL).
    Every row is checked first; with any error nothing is saved.

    kind REGULAR (the first exam): importing again replaces that result (the university revised it).
    kind SUPPLEMENTARY (a re-sit): only for a subject the student FAILED in the university's result; it becomes the
    result that counts (latest attempt: GPA, transcript "(S)", promotion), and the earlier attempt is kept in
    UniversityResultAttempt. Importing the supplementary again revises it. A subject that already has a supplementary
    result cannot be overwritten by a REGULAR import."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run(self, raw: bytes, *, program_id: uuid.UUID, term_label: str, dry_run: bool, actor_id: uuid.UUID,
                  kind: str = "REGULAR") -> dict:
        from app.audit.service import AuditService
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.programs.models import Program
        from app.students.models import Student

        program = await self.session.get(Program, program_id)
        if program is None:
            raise ResourceNotFoundError("Program not found.")
        if not is_affiliated(program):
            raise BusinessRuleViolationError(f"{program.code} is not set as affiliated. Set who conducts its exams first (Grading tables & mark split).")
        term = " ".join((term_label or "").split())
        if not term:
            raise ValidationAppError("Choose the term.")
        kind = (kind or "REGULAR").upper()
        if kind not in ("REGULAR", "SUPPLEMENTARY"):
            raise ValidationAppError("kind must be REGULAR or SUPPLEMENTARY.")
        supp = kind == "SUPPLEMENTARY"
        if len(raw) > 5_000_000:
            raise ValidationAppError("The file is too big (at most 5 MB).")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationAppError("Save the file as CSV UTF-8.")
        reader = csv.DictReader(io.StringIO(text))
        headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
        if "course_code" not in headers or not ({"reg_number", "roll_number"} & headers):
            raise ValidationAppError("The file needs course_code and reg_number (or roll_number) columns. Download the template.")
        if not ({"marks_obtained", "grade"} & headers):
            raise ValidationAppError("The file needs marks_obtained + marks_total, or grade.")

        rules = await rules_for_program(self.session, program.id)
        letters = {b.letter.upper(): b for b in rules.bands}
        errors, plan, seen = [], [], {}
        rows = []
        for i, r in enumerate(reader, start=2):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            if not any(r.values()):
                continue
            rows.append((i, r))
            if len(rows) > MAX_ROWS:
                raise ValidationAppError(f"At most {MAX_ROWS} rows at a time.")

        regs = {r["reg_number"].upper() for _, r in rows if r.get("reg_number")}
        rolls = {r["roll_number"] for _, r in rows if r.get("roll_number") and not r.get("reg_number")}
        by_reg = {s.reg_number.upper(): s for s in (await self.session.execute(select(Student).where(func.upper(Student.reg_number).in_(regs)))).scalars()} if regs else {}
        by_roll = {s.roll_number: s for s in (await self.session.execute(select(Student).where(Student.roll_number.in_(rolls)))).scalars()} if rolls else {}
        codes = {r.get("course_code", "").upper() for _, r in rows}
        courses = {c.code.upper(): c for c in (await self.session.execute(select(Course).where(func.upper(Course.code).in_(codes)))).scalars()} if codes else {}

        for line, r in rows:
            def bad(msg):
                errors.append({"line": line, "who": r.get("reg_number") or r.get("roll_number") or "", "course_code": r.get("course_code", ""), "error": msg})
            st = by_reg.get(r["reg_number"].upper()) if r.get("reg_number") else by_roll.get(r.get("roll_number", ""))
            if st is None:
                bad("No student has this Reg No." if r.get("reg_number") else "No student has this roll number.")
                continue
            if st.program_id != program.id:
                bad(f"{st.roll_number} is not in {program.code}.")
                continue
            course = courses.get(r.get("course_code", "").upper())
            if course is None:
                bad("Unknown course code.")
                continue
            key = (st.id, course.id)
            if key in seen:
                bad(f"Repeated (line {seen[key]}).")
                continue
            seen[key] = line
            enrolment = (await self.session.execute(select(Enrollment).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id).where(
                Enrollment.student_id == st.id, CourseOffering.course_id == course.id, func.lower(CourseOffering.term_label) == term.lower(),
                Enrollment.status != EnrollmentStatus.WITHDRAWN))).scalars().first()
            if enrolment is None:
                bad(f"{st.roll_number} is not enrolled in {course.code} in {term}.")
                continue
            got, total = _num(r.get("marks_obtained")), _num(r.get("marks_total"))
            grade = (r.get("grade") or "").upper() or None
            if "bad" in (got, total):
                bad("Marks must be numbers.")
                continue
            if (got is None) != (total is None):
                bad("Give both marks_obtained and marks_total, or neither.")
                continue
            if got is not None and (total <= 0 or got < 0 or got > total):
                bad("marks_obtained must be between 0 and marks_total.")
                continue
            if got is None and grade is None:
                bad("Give the marks or the grade.")
                continue
            if grade is not None and grade not in letters:
                bad(f"Grade {grade} is not in the {rules.name} table ({', '.join(sorted(letters))}).")
                continue
            word = (r.get("result") or "").upper()
            if word and word not in PASS_WORDS:
                bad("result must be PASS or FAIL.")
                continue
            pct = round(got / total * 100, 2) if got is not None else float(letters[grade].min_percent)
            if word:
                passed = PASS_WORDS[word]
            elif grade is not None:
                passed = letters[grade].is_passing
            else:
                passed = pct >= rules.pass_percent
            letter, points, division = rules.grade(pct, passed)
            if grade is not None and letter.upper() != grade:      # the university's grade wins; its band's points
                band = letters[grade]
                letter, points, division = band.letter, float(band.points), band.division
            old = (await self.session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == enrolment.id))).scalar_one_or_none()
            from_university = old is not None and old.source == "UNIVERSITY"
            if supp:
                if not from_university:
                    bad(f"{st.roll_number} has no university result in {course.code} yet: import the first (regular) result first.")
                    continue
                if not old.supplementary and old.outcome != EnrollmentOutcome.FAILED.value:
                    bad(f"{st.roll_number} passed {course.code} in the first attempt: there is nothing to re-sit.")
                    continue
            elif from_university and old.supplementary:
                bad(f"{st.roll_number} already has a supplementary result in {course.code}: choose Supplementary to revise it.")
                continue
            if old is None:
                replaces = None
            elif from_university:
                replaces = f"{old.letter_grade} ({'supplementary' if old.supplementary else 'first attempt'})"
            else:
                replaces = f"{old.letter_grade} (institute)"
            plan.append({"enrollment": enrolment, "student": st, "course": course, "pct": pct, "got": got, "total": total, "letter": letter,
                         "points": points, "division": division, "passed": passed, "old": old,
                         "row": {"roll_number": st.roll_number, "reg_number": st.reg_number, "name": f"{st.first_name} {st.last_name}",
                                 "course_code": course.code, "percent": pct, "grade": letter, "result": "PASS" if passed else "FAIL",
                                 "replaces": replaces,
                                 "attempt": ("revises the supplementary" if (old is not None and old.supplementary and from_university) else "supplementary")
                                 if supp else ("revises the first attempt" if from_university else "first attempt")}})

        out = {"dry_run": dry_run or bool(errors), "kind": kind, "program": program.code, "term_label": term, "rows": len(rows), "errors": errors[:300],
               "error_count": len(errors), "results": [p["row"] for p in plan[:1000]], "result_count": len(plan),
               "passed": sum(1 for p in plan if p["passed"]), "failed": sum(1 for p in plan if not p["passed"]),
               "replacing": sum(1 for p in plan if p["old"] is not None), "grading": rules.name}
        if errors or dry_run:
            return out

        from app.academics.models import UniversityResultAttempt

        now = datetime.now(timezone.utc)
        for p in plan:
            e, course = p["enrollment"], p["course"]
            # the attempt record, kept for good (the earlier attempt is never lost)
            attempts = {a.attempt_no: a for a in (await self.session.execute(select(UniversityResultAttempt).where(
                UniversityResultAttempt.enrollment_id == e.id))).scalars()}
            old = p["old"]
            if supp and old is not None and not old.supplementary and 1 not in attempts:
                # a first result imported before attempts were kept: record it now, as it was
                attempts[1] = UniversityResultAttempt(enrollment_id=e.id, attempt_no=1, kind="REGULAR", percentage=old.percentage,
                                                      total_obtained=old.total_obtained, total_max=old.total_max, letter_grade=old.letter_grade,
                                                      grade_points=old.grade_points, outcome=old.outcome, division=old.division)
                self.session.add(attempts[1])
            if supp:
                latest = max(attempts) if attempts else 1
                no = latest if (old is not None and old.supplementary and latest > 1) else latest + 1
            else:
                no = 1
            a = attempts.get(no)
            if a is None:
                a = UniversityResultAttempt(enrollment_id=e.id, attempt_no=no, kind=kind)
                self.session.add(a)
            else:
                a.revised = (a.revised or 0) + 1
            a.percentage, a.total_obtained = p["pct"], p["got"] if p["got"] is not None else p["pct"]
            a.total_max = p["total"] if p["total"] is not None else 100
            a.letter_grade, a.grade_points, a.division, a.imported_by = p["letter"], p["points"], p["division"], actor_id
            a.outcome = EnrollmentOutcome.PASSED.value if p["passed"] else EnrollmentOutcome.FAILED.value
            row = old or EnrollmentResult(enrollment_id=e.id)
            if old is None:
                self.session.add(row)
            row.percentage = p["pct"]
            row.total_obtained = p["got"] if p["got"] is not None else p["pct"]
            row.total_max = p["total"] if p["total"] is not None else 100
            row.letter_grade, row.grade_points, row.credit_hours = p["letter"], p["points"], course.credit_hours
            row.outcome = EnrollmentOutcome.PASSED.value if p["passed"] else EnrollmentOutcome.FAILED.value
            row.attendance_percentage, row.failed_for_attendance = None, False
            row.theory_percent = row.practical_percent = None
            row.failed_part = None
            row.scheme_name, row.division, row.source, row.computed_at = rules.name, p["division"], "UNIVERSITY", now
            row.supplementary = supp                       # shown as (S); the latest attempt is the one that counts
            e.status = EnrollmentStatus.COMPLETED
            e.outcome = EnrollmentOutcome.PASSED if p["passed"] else EnrollmentOutcome.FAILED
            st = p["student"]
            if st.user_id:
                await create_notification(self.session, user_id=st.user_id, category=NotificationCategory.RESULT,
                                          title=f"{'Supplementary' if supp else 'University'} result for {course.code}: {'Pass' if p['passed'] else 'Fail'}",
                                          message=f"{rules.body or rules.name}: {p['pct']:g}% (grade {p['letter']}).", link="/dashboard/student/results")
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="results.university_imported", entity_type="program", entity_id=program.id,
                                             metadata={"term": term, "kind": kind, "results": len(plan), "replaced": out["replacing"]})
        out["applied"] = len(plan)
        return out


def template_csv() -> str:
    return ",".join(TEMPLATE_COLUMNS) + "\r\n" + "KMU-2026-0001,,NUR-301,68,100,B,PASS\r\n"
