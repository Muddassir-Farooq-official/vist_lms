"""Semester (or year) results for a whole class, the promotion list, and the gazette.

For one program + semester + term: every student now in that semester, their published results in that term, the GPA
(Semester tables) or total / % / division (Annual tables), and a status from the program's grading table:
  * Semester: GPA >= "GPA to be promoted" -> Promoted; >= "Probation from GPA" -> Promoted on probation; else Not promoted.
    A table without these limits promotes everyone (failed subjects are listed for re-sit / repeat).
  * Annual: every subject passed -> Promoted; else Not promoted (failed subjects listed).
  * A student with a subject not yet published -> Result pending (never promoted).
The admin reviews the list and presses Promote: the chosen students move to the next semester (year). Each batch is
audited and can be undone while the students have not moved again.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.grade_scale import gpa
from app.academics.grading_schemes import rules_for_program
from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentResult
from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError

STATUS_LABEL = {"PROMOTED": "Promoted", "PROBATION": "Promoted on probation", "NOT_PROMOTED": "Not promoted", "PENDING": "Result pending",
                "NO_SUBJECTS": "No subjects this term"}
PART_LABEL = {"THEORY": "Theory", "PRACTICAL": "Practical", "THEORY+PRACTICAL": "Theory & Practical"}


class ClassResultService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(self, *, program_id: uuid.UUID, semester: int, term_label: str) -> dict:
        from app.programs.models import Program
        from app.students.family import guardian_labels
        from app.students.models import Student, StudentStatus

        program = await self.session.get(Program, program_id)
        if program is None:
            raise ResourceNotFoundError("Program not found.")
        rules = await rules_for_program(self.session, program_id)
        students = list((await self.session.execute(select(Student).where(
            Student.program_id == program_id, Student.current_semester == semester, Student.status == StudentStatus.ACTIVE)
            .order_by(Student.roll_number))).scalars())
        fam = await guardian_labels(self.session, [s.id for s in students]) if students else {}
        annual = rules.system == "ANNUAL"
        rows = []
        for st in students:
            pairs = (await self.session.execute(
                select(Enrollment, EnrollmentResult, Course)
                .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                .join(Course, Course.id == CourseOffering.course_id)
                .outerjoin(EnrollmentResult, EnrollmentResult.enrollment_id == Enrollment.id)
                .where(Enrollment.student_id == st.id, CourseOffering.term_label == term_label)
                .order_by(Course.code))).all()
            subjects, pending, failed = [], False, []
            for e, r, c in pairs:
                if getattr(e.status, "value", e.status) == "DROPPED":
                    continue
                if r is None:
                    pending = True
                    subjects.append({"code": c.code, "title": c.title, "credit_hours": c.credit_hours, "published": False})
                    continue
                subj = {"code": c.code, "title": c.title, "credit_hours": r.credit_hours, "published": True, "percentage": float(r.percentage),
                        "total_obtained": float(r.total_obtained), "total_max": float(r.total_max), "grade": r.letter_grade,
                        "grade_points": float(r.grade_points), "outcome": r.outcome, "failed_part": r.failed_part,
                        "short_attendance": r.failed_for_attendance, "theory_percent": float(r.theory_percent) if r.theory_percent is not None else None,
                        "practical_percent": float(r.practical_percent) if r.practical_percent is not None else None, "supplementary": r.supplementary}
                subjects.append(subj)
                if r.outcome == "FAILED":
                    failed.append(c.code + (" (short attendance)" if r.failed_for_attendance else
                                            (f" ({PART_LABEL.get(r.failed_part, r.failed_part)})" if r.failed_part else "")))
            done = [x for x in subjects if x["published"]]
            term_gpa = gpa([(x["grade_points"], x["credit_hours"]) for x in done]) if done else None
            obtained, maximum = sum(x["total_obtained"] for x in done), sum(x["total_max"] for x in done)
            pct = round(obtained / maximum * 100, 2) if maximum else None
            division = None
            if annual and pct is not None:
                division = rules.grade(pct, not failed)[2]
            if not subjects:
                status = "NO_SUBJECTS"          # not enrolled in anything in this term (wrong term picked, or on a break)
            elif pending:
                status = "PENDING"
            elif annual:
                status = "PROMOTED" if not failed else "NOT_PROMOTED"
            else:
                status = self._semester_status(rules, term_gpa)
            g = fam.get(st.id) or {}
            rows.append({"student_id": st.id, "roll_number": st.roll_number, "name": f"{st.first_name} {st.last_name}",
                         "guardian_name": g.get("name"), "guardian_label": g.get("label"), "subjects": subjects, "gpa": term_gpa,
                         "total_obtained": round(obtained, 2), "total_max": round(maximum, 2), "percentage": pct, "division": division,
                         "failed": failed, "status": status, "status_label": STATUS_LABEL[status], "position": None})
        # Position holders: the top three among students who passed everything (ties share a position).
        key = (lambda r: r["percentage"] or 0) if annual else (lambda r: r["gpa"] or 0)
        ranked = sorted([r for r in rows if r["status"] not in ("PENDING", "NO_SUBJECTS") and not r["failed"]], key=key, reverse=True)
        position, last = 0, None
        for r in ranked:
            if key(r) != last:
                position += 1
                last = key(r)
            if position > 3:
                break
            r["position"] = position
        return {"program": {"id": program.id, "code": program.code, "name": program.name, "total_semesters": program.total_semesters},
                "semester": semester, "term_label": term_label, "annual": annual, "period_word": "Year" if annual else "Semester",
                "grading": {"name": rules.name, "body": rules.body, "system": rules.system, "pass_percent": rules.pass_percent},
                "rows": rows, "counts": {k: sum(1 for r in rows if r["status"] == k) for k in STATUS_LABEL}}

    @staticmethod
    def _semester_status(rules, term_gpa: float | None) -> str:
        if term_gpa is None:
            return "PENDING"
        if rules.min_gpa_promotion is None or term_gpa >= rules.min_gpa_promotion:
            return "PROMOTED"
        if rules.probation_gpa is not None and term_gpa >= rules.probation_gpa:
            return "PROBATION"
        return "NOT_PROMOTED"

    # ---------------------------------------------------------------- promote / undo
    async def promote(self, *, program_id: uuid.UUID, semester: int, student_ids: list[uuid.UUID], actor_id: uuid.UUID) -> dict:
        from app.programs.models import Program
        from app.students.models import Student

        program = await self.session.get(Program, program_id)
        if program is None:
            raise ResourceNotFoundError("Program not found.")
        if program.total_semesters and semester >= program.total_semesters:
            raise BusinessRuleViolationError(f"Semester {semester} is the last of {program.code}: these students finish the program; there is no next semester.")
        moved, skipped = [], []
        for sid in student_ids:
            st = await self.session.get(Student, sid)
            if st is None or st.program_id != program_id or st.current_semester != semester:
                skipped.append(str(sid))
                continue
            st.current_semester = semester + 1
            moved.append(str(st.id))
        if not moved:
            raise BusinessRuleViolationError("None of these students are in that semester any more.")
        batch = str(uuid.uuid4())
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="students.promoted", entity_type="program", entity_id=program_id,
                                             before={"semester": semester}, after={"semester": semester + 1},
                                             metadata={"batch": batch, "students": moved, "skipped": skipped})
        return {"batch": batch, "promoted": len(moved), "skipped": len(skipped), "to_semester": semester + 1}

    async def undo(self, *, batch: str, actor_id: uuid.UUID) -> dict:
        from app.audit.models import AuditLog
        from app.students.models import Student

        logs = (await self.session.execute(select(AuditLog).where(AuditLog.action == "students.promoted"))).scalars().all()
        log = next((x for x in logs if (x.log_metadata or {}).get("batch") == batch), None) if logs else None
        if log is None:
            raise ResourceNotFoundError("That promotion was not found.")
        if any((x.log_metadata or {}).get("undoes") == batch for x in (await self.session.execute(
                select(AuditLog).where(AuditLog.action == "students.promotion_undone"))).scalars().all()):
            raise BusinessRuleViolationError("That promotion was already undone.")
        frm, to = log.before["semester"], log.after["semester"]
        back, left = [], []
        for sid in (log.log_metadata or {}).get("students", []):
            st = await self.session.get(Student, uuid.UUID(sid))
            if st is not None and st.current_semester == to:
                st.current_semester = frm
                back.append(sid)
            else:
                left.append(sid)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="students.promotion_undone", entity_type="program", entity_id=log.entity_id,
                                             before={"semester": to}, after={"semester": frm}, metadata={"undoes": batch, "students": back, "left": left})
        return {"moved_back": len(back), "left_as_is": len(left)}
