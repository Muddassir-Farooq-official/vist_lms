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
                "percentage": float(r.percentage), "letter_grade": r.letter_grade}

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
            comp = await self.session.get(OfferingAssessment, offering_assessment_id) if offering_assessment_id else await self._default_component(e.course_offering_id)
            if comp is None or comp.course_offering_id != e.course_offering_id:
                raise ValidationAppError("That component does not belong to this course.")
            a = SupplementaryAttempt(enrollment_id=eid, offering_assessment_id=comp.id, original_result=_result_dict(r), registered_by=actor_id)
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
    async def _component_max(self, a: SupplementaryAttempt) -> float:
        comp = await self.session.get(OfferingAssessment, a.offering_assessment_id)
        return float((await self.session.get(AssessmentType, comp.assessment_type_id)).max_marks)

    async def enter_mark(self, attempt_id: uuid.UUID, *, obtained: float | None, absent: bool, actor_id: uuid.UUID) -> SupplementaryAttempt:
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
        a.status, a.marked_by = SupplementaryStatus.MARKED, actor_id
        await self.session.flush()
        return a

    # ---------------------------------------------------------------- publish
    async def preview(self, a: SupplementaryAttempt) -> dict:
        """What publishing would give (nothing saved)."""
        from app.academics.grade_scale import GradeScaleService, grade_for

        if a.absent or a.obtained_marks is None:
            return {**a.original_result, "absent": True, "changed": False}
        mark = (await self.session.execute(select(StudentMark).where(StudentMark.enrollment_id == a.enrollment_id,
                                                                    StudentMark.offering_assessment_id == a.offering_assessment_id))).scalar_one_or_none()
        old_component = float(mark.obtained_marks) if mark else 0.0
        orig = a.original_result
        total = orig["total_obtained"] - old_component + float(a.obtained_marks)
        pct = round(total / orig["total_max"] * 100, 2) if orig["total_max"] else 0.0
        passed = pct >= settings.marks_pass_threshold_percent
        scale = await GradeScaleService(self.session).entries()
        letter, points = grade_for(scale, pct, passed)
        cap = float(settings.supplementary_max_grade_points or 0)
        capped = False
        if passed and cap and points > cap:
            allowed = sorted([e for e in scale if e.is_passing and float(e.grade_points) <= cap], key=lambda e: float(e.grade_points), reverse=True)
            if allowed:
                letter, points, capped = allowed[0].letter, float(allowed[0].grade_points), True
        return {"percentage": pct, "total_obtained": round(total, 2), "total_max": orig["total_max"], "letter_grade": letter,
                "grade_points": points, "outcome": "PASSED" if passed else "FAILED", "capped": capped, "absent": False, "changed": passed}

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
            await SmsService(self.session).queue_for_student(e.student_id, event="RESULTS",
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
            row.update({"id": a.id, "status": a.status.value, "component": t.name, "component_max": float(t.max_marks),
                        "obtained_marks": float(a.obtained_marks) if a.obtained_marks is not None else None, "absent": a.absent,
                        "original": a.original_result, "result": a.new_result if a.status == SupplementaryStatus.PUBLISHED else None})
            if student_id and a.status != SupplementaryStatus.PUBLISHED:
                row["obtained_marks"] = None            # students see marks only once published
            out.append(row)
        return out