"""Supplementary (reappear) examinations.

Who can sit one: a student whose PUBLISHED result in a course is FAILED on marks.
A student failed for low attendance cannot (they must repeat the course), and each
failed result gets at most one supplementary attempt.

How it is graded: every component keeps its original mark except one (by default the
course's final paper: the biggest "Exam" component), whose mark is replaced by the
supplementary paper's. The percentage, pass/fail and letter are then worked out exactly
as for a normal result. Policy "supplementary_max_grade_points" (Settings -> Institute
Policy -> Exams) can cap the grade a supplementary pass earns, e.g. 2.0 = at most a C.

What publishing does:
  - passed: the course result becomes the new (passing) result, marked as earned in a
    supplementary; transcript, GPA/CGPA and promotion holds follow automatically;
  - failed again or absent: the original failed result stays; the student must repeat.
The original failed result is always kept on the attempt for the record.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import (
    AssessmentType,
    Course,
    CourseOffering,
    Enrollment,
    EnrollmentOutcome,
    EnrollmentResult,
    OfferingAssessment,
    ResultSheet,
    ResultSheetStatus,
    StudentMark,
)
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.supplementary.models import SupplementaryAttempt, SupplementaryStatus


def _result_dict(r: EnrollmentResult) -> dict:
    return {"percentage": float(r.percentage), "total_obtained": float(r.total_obtained), "total_max": float(r.total_max),
            "letter_grade": r.letter_grade, "grade_points": float(r.grade_points), "outcome": r.outcome}


class SupplementaryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------------- who can sit
    async def eligible(self, term_label: str) -> list[dict]:
        rows = (await self.session.execute(
            select(Enrollment, EnrollmentResult, CourseOffering, Course)
            .join(EnrollmentResult, EnrollmentResult.enrollment_id == Enrollment.id)
            .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .join(ResultSheet, ResultSheet.course_offering_id == CourseOffering.id)
            .where(CourseOffering.term_label == term_label, ResultSheet.status == ResultSheetStatus.PUBLISHED,
                   CourseOffering.section_label != "IMPORTED", EnrollmentResult.outcome == "FAILED",
                   EnrollmentResult.failed_for_attendance.is_(False))
            .order_by(Course.code))).all()
        taken = set((await self.session.execute(select(SupplementaryAttempt.enrollment_id))).scalars())
        return [await self._row(e, r, o, c) for e, r, o, c in rows if e.id not in taken]

    async def _row(self, e: Enrollment, r: EnrollmentResult, o: CourseOffering, c: Course) -> dict:
        from app.students.models import Student

        st = await self.session.get(Student, e.student_id)
        return {"enrollment_id": e.id, "student_id": e.student_id, "roll_number": st.roll_number if st else None,
                "student_name": f"{st.first_name} {st.last_name}" if st else None, "course_offering_id": o.id,
                "course_code": c.code, "course_title": c.title, "term_label": o.term_label,
                "percentage": float(r.percentage), "letter_grade": r.letter_grade, "failed_part": r.failed_part}

    async def _papers_for(self, offering_id: uuid.UUID, failed_part: str | None) -> list[OfferingAssessment]:
        """The paper(s) to re-sit: only the failed part (theory -> the final; practical -> the practical), both when both
        were failed; otherwise (no mark split) the final exam as before."""
        rows = (await self.session.execute(select(OfferingAssessment, AssessmentType)
                                           .join(AssessmentType, AssessmentType.id == OfferingAssessment.assessment_type_id)
                                           .where(OfferingAssessment.course_offering_id == offering_id))).all()
        parts = (failed_part or "").split("+")
        out = []
        for part, comp in (("THEORY", "FINAL"), ("PRACTICAL", "PRACTICAL")):
            if part in parts:
                match = [x for x in rows if x[1].component == comp]
                if match:
                    out.append(max(match, key=lambda x: float(x[1].max_marks))[0])
        return out or [await self._default_component(offering_id)]

    async def _default_component(self, offering_id: uuid.UUID) -> OfferingAssessment:
        """The final paper: the biggest component whose type is in the 'Exam' category (else the biggest of all)."""
        rows = (await self.session.execute(select(OfferingAssessment, AssessmentType)
                                           .join(AssessmentType, AssessmentType.id == OfferingAssessment.assessment_type_id)
                                           .where(OfferingAssessment.course_offering_id == offering_id))).all()
        if not rows:
            raise BusinessRuleViolationError("This course has no assessment components.")
        exams = [x for x in rows if (x[1].category or "").strip().lower() == "exam"] or rows
        return max(exams, key=lambda x: (float(x[1].max_marks), (x[1].name or "").lower() == "final"))[0]

    # ---------------------------------------------------------------- register
    async def register(self, enrollment_ids: list[uuid.UUID], *, actor_id: uuid.UUID,
                       offering_assessment_id: uuid.UUID | None = None) -> list[SupplementaryAttempt]:
        out = []
        for eid in enrollment_ids:
            e = await self.session.get(Enrollment, eid)
            r = (await self.session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == eid))).scalar_one_or_none()
            if e is None or r is None:
                raise ResourceNotFoundError("Result not found.")
            sheet = (await self.session.execute(select(ResultSheet).where(ResultSheet.course_offering_id == e.course_offering_id))).scalar_one_or_none()
            if sheet is None or sheet.status != ResultSheetStatus.PUBLISHED:
                raise BusinessRuleViolationError("A supplementary is only for a published result.")
            if r.outcome != "FAILED":
                raise BusinessRuleViolationError("Only a failed result can have a supplementary.")
            if r.failed_for_attendance:
                raise BusinessRuleViolationError("Failed for low attendance: the course must be repeated, not taken as a supplementary.")
            if (await self.session.execute(select(SupplementaryAttempt).where(SupplementaryAttempt.enrollment_id == eid))).scalar_one_or_none():
                raise BusinessRuleViolationError("This result already has a supplementary attempt.")
            if offering_assessment_id:
                papers = [await self.session.get(OfferingAssessment, offering_assessment_id)]
            else:
                papers = await self._papers_for(e.course_offering_id, r.failed_part)
            if any(p is None or p.course_offering_id != e.course_offering_id for p in papers):
                raise ValidationAppError("That component does not belong to this course.")
            a = SupplementaryAttempt(enrollment_id=eid, offering_assessment_id=papers[0].id, original_result=_result_dict(r), registered_by=actor_id,
                                     second_assessment_id=papers[1].id if len(papers) > 1 else None)
            self.session.add(a)
            out.append(a)
        await self.session.flush()
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="supplementary.registered", entity_type="enrollment",
                                             entity_id=None, metadata={"enrollments": [str(x) for x in enrollment_ids]})
        return out

    async def cancel(self, attempt_id: uuid.UUID) -> None:
        a = await self._get(attempt_id)
        if a.status == SupplementaryStatus.PUBLISHED:
            raise BusinessRuleViolationError("A published supplementary cannot be cancelled.")
        await self.session.delete(a)

    async def _get(self, attempt_id: uuid.UUID) -> SupplementaryAttempt:
        a = await self.session.get(SupplementaryAttempt, attempt_id)
        if a is None:
            raise ResourceNotFoundError("Supplementary attempt not found.")
        return a

    # ---------------------------------------------------------------- marks
    async def _component_max(self, a: SupplementaryAttempt, oa_id: uuid.UUID | None = None) -> float:
        comp = await self.session.get(OfferingAssessment, oa_id or a.offering_assessment_id)
        return float((await self.session.get(AssessmentType, comp.assessment_type_id)).max_marks)

    async def enter_mark(self, attempt_id: uuid.UUID, *, obtained: float | None, absent: bool, actor_id: uuid.UUID,
                         second_obtained: float | None = None) -> SupplementaryAttempt:
        a = await self._get(attempt_id)
        if a.status == SupplementaryStatus.PUBLISHED:
            raise BusinessRuleViolationError("This supplementary result is already published.")
        if absent:
            a.obtained_marks, a.absent = None, True
        else:
            if obtained is None:
                raise ValidationAppError("Enter the marks, or mark the student absent.")
            top = await self._component_max(a)
            if not 0 <= obtained <= top:
                raise ValidationAppError(f"Marks must be between 0 and {top:g}.")
            a.obtained_marks, a.absent = obtained, False
            if a.second_assessment_id is not None:
                if second_obtained is None:
                    raise ValidationAppError("This re-sit has two papers: enter the practical paper's marks too.")
                top2 = await self._component_max(a, a.second_assessment_id)
                if not 0 <= second_obtained <= top2:
                    raise ValidationAppError(f"Practical marks must be between 0 and {top2:g}.")
                a.second_obtained_marks = second_obtained
        if absent:
            a.second_obtained_marks = None
        a.status, a.marked_by = SupplementaryStatus.MARKED, actor_id
        await self.session.flush()
        return a

    # ---------------------------------------------------------------- publish
    async def preview(self, a: SupplementaryAttempt) -> dict:
        """What publishing would give (nothing saved). The re-sit mark replaces the old one and the subject is graded again by
        the program's grading table (and mark split); the table's re-sit cap limits the percentage a re-sit can earn."""
        from app.academics.grading_service import GradingService

        if a.absent or a.obtained_marks is None:
            return {**a.original_result, "absent": True, "changed": False}
        orig = a.original_result
        enrollment = await self.session.get(Enrollment, a.enrollment_id)
        override = {a.offering_assessment_id: float(a.obtained_marks)}
        if a.second_assessment_id is not None and a.second_obtained_marks is not None:
            override[a.second_assessment_id] = float(a.second_obtained_marks)
        ev = await GradingService(self.session).evaluate(enrollment, override=override)
        if ev is None:                 # marks missing (should not happen for a published result): plain arithmetic as a fallback
            mark = (await self.session.execute(select(StudentMark).where(StudentMark.enrollment_id == a.enrollment_id,
                                                                        StudentMark.offering_assessment_id == a.offering_assessment_id))).scalar_one_or_none()
            total = orig["total_obtained"] - (float(mark.obtained_marks) if mark else 0.0) + float(a.obtained_marks)
            pct = round(total / orig["total_max"] * 100, 2) if orig["total_max"] else 0.0
            from app.academics.grading_schemes import rules_for_student

            rules = await rules_for_student(self.session, enrollment.student_id)
            passed, total_max = pct >= rules.pass_percent, orig["total_max"]
        else:
            rules, pct, total, total_max, passed = ev["rules"], ev["percentage"], ev["total_obtained"], ev["total_max"], ev["marks_passed"]
        capped = False
        if passed and rules.resit_cap is not None and pct > rules.resit_cap:
            pct, capped = rules.resit_cap, True
        letter, points, _division = rules.grade(pct, passed)
        cap = float(settings.supplementary_max_grade_points or 0)
        if passed and cap and points > cap:
            allowed = sorted([b for b in rules.bands if b.is_passing and b.points <= cap], key=lambda b: b.points, reverse=True)
            if allowed:
                letter, points, capped = allowed[0].letter, float(allowed[0].points), True
        return {"percentage": pct, "total_obtained": round(total, 2), "total_max": total_max, "letter_grade": letter,
                "grade_points": points, "outcome": "PASSED" if passed else "FAILED", "capped": capped, "absent": False, "changed": passed,
                "theory_percent": ev.get("theory_percent") if ev else None, "practical_percent": ev.get("practical_percent") if ev else None,
                "failed_parts": ev.get("failed_parts") if ev else []}

    async def publish(self, attempt_ids: list[uuid.UUID], *, actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.sms.service import SmsService
        from app.students.models import Student

        passed = failed = 0
        for aid in attempt_ids:
            a = await self._get(aid)
            if a.status != SupplementaryStatus.MARKED:
                raise BusinessRuleViolationError("Enter every mark (or mark absent) before publishing.")
            res = await self.preview(a)
            e = await self.session.get(Enrollment, a.enrollment_id)
            row = (await self.session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == e.id))).scalar_one()
            if res["changed"]:
                row.percentage, row.total_obtained = res["percentage"], res["total_obtained"]
                row.letter_grade, row.grade_points, row.outcome = res["letter_grade"], res["grade_points"], "PASSED"
                row.supplementary = True
                row.theory_percent, row.practical_percent, row.failed_part = res.get("theory_percent"), res.get("practical_percent"), None
                row.computed_at = datetime.now(timezone.utc)
                e.outcome = EnrollmentOutcome.PASSED
                passed += 1
            else:
                failed += 1
            a.status, a.new_result, a.published_at = SupplementaryStatus.PUBLISHED, res, datetime.now(timezone.utc)
            offering = await self.session.get(CourseOffering, e.course_offering_id)
            course = await self.session.get(Course, offering.course_id)
            outcome_text = (f"passed with grade {res['letter_grade']}" if res["changed"] else ("absent: you must repeat the course" if res["absent"]
                            else f"not passed ({res['percentage']:g}%): you must repeat the course"))
            st = await self.session.get(Student, e.student_id)
            if st and st.user_id:
                await create_notification(self.session, user_id=st.user_id, category=NotificationCategory.RESULT,
                                          title=f"Supplementary result: {course.code}", message=f"{course.code} {course.title}: {outcome_text}.",
                                          link="/dashboard/student/results")
            await SmsService(self.session).queue_for_student(e.student_id, event="RESULTS", attachment=f"RESULT:{e.student_id}",
                                                            body=f"{settings.app_name}: supplementary result {course.code}: {outcome_text}.")
            await AuditService(self.session).log(actor_user_id=actor_id, action="supplementary.published", entity_type="enrollment",
                                                 entity_id=e.id, before=a.original_result, after=res)
        await self.session.flush()
        return {"published": passed + failed, "passed": passed, "not_passed": failed}

    # ---------------------------------------------------------------- lists
    async def listing(self, term_label: str | None = None, *, student_id: uuid.UUID | None = None) -> list[dict]:
        stmt = (select(SupplementaryAttempt, Enrollment, EnrollmentResult, CourseOffering, Course)
                .join(Enrollment, Enrollment.id == SupplementaryAttempt.enrollment_id)
                .join(EnrollmentResult, EnrollmentResult.enrollment_id == Enrollment.id)
                .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                .join(Course, Course.id == CourseOffering.course_id).order_by(Course.code))
        if term_label:
            stmt = stmt.where(CourseOffering.term_label == term_label)
        if student_id:
            stmt = stmt.where(Enrollment.student_id == student_id)
        out = []
        for a, e, r, o, c in (await self.session.execute(stmt)).all():
            row = await self._row(e, r, o, c)
            comp = await self.session.get(OfferingAssessment, a.offering_assessment_id)
            t = await self.session.get(AssessmentType, comp.assessment_type_id)
            second = None
            if a.second_assessment_id is not None:
                comp2 = await self.session.get(OfferingAssessment, a.second_assessment_id)
                t2 = await self.session.get(AssessmentType, comp2.assessment_type_id)
                second = {"component": t2.name, "component_max": float(t2.max_marks),
                          "obtained_marks": float(a.second_obtained_marks) if a.second_obtained_marks is not None and not student_id else None}
            row.update({"id": a.id, "status": a.status.value, "component": t.name, "component_max": float(t.max_marks), "second": second,
                        "obtained_marks": float(a.obtained_marks) if a.obtained_marks is not None else None, "absent": a.absent,
                        "original": a.original_result, "result": a.new_result if a.status == SupplementaryStatus.PUBLISHED else None})
            if student_id and a.status != SupplementaryStatus.PUBLISHED:
                row["obtained_marks"] = None            # students see marks only once published
            out.append(row)
        return out