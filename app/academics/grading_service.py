from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import (
    AssessmentType,
    Course,
    Enrollment,
    EnrollmentOutcome,
    EnrollmentResult,
    EnrollmentStatus,
    OfferingAssessment,
    ResultSheetStatus,
    StudentMark,
)
from app.academics.repository import CourseOfferingRepository, EnrollmentRepository
from app.audit.service import AuditService
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.students.models import Student


class AssessmentTypeService:
    """The institute-wide result-type catalog — admin defines it once
    ('Quiz 1', 'Mid Term', 'Assignment 2', each with a fixed
    max_marks), and any teacher picks from this same list for their
    own subject via OfferingAssessmentService. See AssessmentType's
    own docstring for why max_marks is fixed centrally rather than
    per-subject — this is what makes the result module usable by a
    school, college, or university without hard-coding any of their
    specific structures."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def list_all(self) -> list[AssessmentType]:
        stmt = select(AssessmentType).order_by(AssessmentType.category, AssessmentType.name)
        return list((await self.session.execute(stmt)).scalars())

    async def create(
        self, *, name: str, category: str | None, max_marks: float, actor_id: uuid.UUID
    ) -> AssessmentType:
        if max_marks <= 0:
            raise BusinessRuleViolationError("max_marks must be greater than 0.")
        existing_stmt = select(AssessmentType).where(AssessmentType.name == name.strip())
        if (await self.session.execute(existing_stmt)).scalar_one_or_none() is not None:
            raise BusinessRuleViolationError(f"A type named '{name.strip()}' already exists.")

        assessment_type = AssessmentType(
            name=name.strip(), category=(category.strip() if category else None), max_marks=max_marks
        )
        self.session.add(assessment_type)
        await self.session.flush()

        await self.audit.log(
            actor_user_id=actor_id, action="assessment_type.created", entity_type="assessment_type",
            entity_id=assessment_type.id, after={"name": assessment_type.name, "max_marks": max_marks},
        )
        return assessment_type

    async def delete(self, assessment_type_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        assessment_type = await self.session.get(AssessmentType, assessment_type_id)
        if assessment_type is None:
            raise ResourceNotFoundError("Assessment type not found.")

        in_use_stmt = (
            select(OfferingAssessment).where(OfferingAssessment.assessment_type_id == assessment_type_id).limit(1)
        )
        if (await self.session.execute(in_use_stmt)).scalar_one_or_none() is not None:
            raise BusinessRuleViolationError(
                "Cannot delete this type — at least one subject is already using it. Remove it from every "
                "subject first."
            )

        await self.audit.log(
            actor_user_id=actor_id, action="assessment_type.deleted", entity_type="assessment_type",
            entity_id=assessment_type.id, before={"name": assessment_type.name},
        )
        await self.session.delete(assessment_type)


class GradingService:
    """The real Exams/Result module that replaces the old manual
    Pass/Fail placeholder (EnrollmentService.set_outcome, still there
    for a genuine manual administrative override, but no longer the
    normal path). A subject's grading scheme is whichever
    AssessmentTypes from the institute-wide catalog its teacher (or
    admin) has added via add_offering_assessment — the overall result
    is simply the raw sum of obtained marks over the sum of max_marks
    across everything added, which needs no weight bookkeeping and
    naturally fits any institute's structure (see AssessmentType's own
    docstring)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.offering_repo = CourseOfferingRepository(session)
        self.enrollment_repo = EnrollmentRepository(session)
        self.audit = AuditService(session)

    async def list_offering_assessments(self, course_offering_id: uuid.UUID) -> list[OfferingAssessment]:
        stmt = select(OfferingAssessment).where(OfferingAssessment.course_offering_id == course_offering_id)
        return list((await self.session.execute(stmt)).scalars())

    async def add_offering_assessment(
        self, course_offering_id: uuid.UUID, *, assessment_type_id: uuid.UUID, actor_id: uuid.UUID
    ) -> OfferingAssessment:
        offering = await self.offering_repo.get_by_id(course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        await self._assert_structure_editable(course_offering_id)
        assessment_type = await self.session.get(AssessmentType, assessment_type_id)
        if assessment_type is None:
            raise ResourceNotFoundError("Assessment type not found.")

        existing_stmt = select(OfferingAssessment).where(
            OfferingAssessment.course_offering_id == course_offering_id,
            OfferingAssessment.assessment_type_id == assessment_type_id,
        )
        if (await self.session.execute(existing_stmt)).scalar_one_or_none() is not None:
            raise BusinessRuleViolationError(f"'{assessment_type.name}' is already added to this subject.")

        offering_assessment = OfferingAssessment(
            course_offering_id=course_offering_id, assessment_type_id=assessment_type_id, created_by=actor_id,
        )
        self.session.add(offering_assessment)
        await self.session.flush()

        await self.audit.log(
            actor_user_id=actor_id, action="offering_assessment.added", entity_type="offering_assessment",
            entity_id=offering_assessment.id,
            after={"course_offering_id": str(course_offering_id), "assessment_type": assessment_type.name},
        )
        return offering_assessment

    async def remove_offering_assessment(self, offering_assessment_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        offering_assessment = await self.session.get(OfferingAssessment, offering_assessment_id)
        if offering_assessment is None:
            raise ResourceNotFoundError("This subject's assessment entry was not found.")
        await self._assert_structure_editable(offering_assessment.course_offering_id)

        existing_mark_stmt = (
            select(StudentMark).where(StudentMark.offering_assessment_id == offering_assessment_id).limit(1)
        )
        if (await self.session.execute(existing_mark_stmt)).scalar_one_or_none() is not None:
            raise BusinessRuleViolationError(
                "Cannot remove this — at least one student already has a mark recorded against it."
            )

        await self.audit.log(
            actor_user_id=actor_id, action="offering_assessment.removed", entity_type="offering_assessment",
            entity_id=offering_assessment.id,
        )
        await self.session.delete(offering_assessment)

    async def _sheet_status(self, course_offering_id: uuid.UUID) -> ResultSheetStatus:
        from app.academics.models import ResultSheet

        row = (await self.session.execute(select(ResultSheet.status).where(ResultSheet.course_offering_id == course_offering_id))).first()
        return row[0] if row else ResultSheetStatus.DRAFT

    async def _assert_structure_editable(self, course_offering_id: uuid.UUID) -> None:
        if await self._sheet_status(course_offering_id) not in (ResultSheetStatus.DRAFT, ResultSheetStatus.RETURNED):
            raise BusinessRuleViolationError("The marks for this subject have been submitted, so its grading structure is locked.")

    async def enter_mark(
        self, enrollment_id: uuid.UUID, offering_assessment_id: uuid.UUID, *, obtained_marks: float, actor_id: uuid.UUID,
        is_admin: bool = False, correction_reason: str | None = None,
    ) -> StudentMark:
        enrollment = await self.enrollment_repo.get_by_id(enrollment_id)
        if enrollment is None:
            raise ResourceNotFoundError("Enrollment not found.")
        if enrollment.status == EnrollmentStatus.WITHDRAWN:
            raise BusinessRuleViolationError("Can't record a mark for a withdrawn enrollment.")

        offering_assessment = await self.session.get(OfferingAssessment, offering_assessment_id)
        if offering_assessment is None or offering_assessment.course_offering_id != enrollment.course_offering_id:
            raise ResourceNotFoundError("This assessment doesn't belong to this enrollment's offering.")
        assessment_type = await self.session.get(AssessmentType, offering_assessment.assessment_type_id)
        if obtained_marks < 0 or obtained_marks > float(assessment_type.max_marks):
            raise BusinessRuleViolationError(f"obtained_marks must be between 0 and {assessment_type.max_marks}.")

        # The marks belong to a result sheet that is draft, locked for approval, or published -- and what may be done depends on which.
        status = await self._sheet_status(enrollment.course_offering_id)
        correcting = status == ResultSheetStatus.PUBLISHED
        if status in (ResultSheetStatus.SUBMITTED, ResultSheetStatus.APPROVED):
            raise BusinessRuleViolationError("These marks have been submitted for approval and are locked. They must be sent back before they can be changed.")
        if correcting:
            if not is_admin:
                raise BusinessRuleViolationError("These results are published. Only the administration can correct a published mark.")
            if len((correction_reason or "").strip()) < 5:
                raise BusinessRuleViolationError("Give a reason for correcting a published mark (at least a few words). It is kept on record.")

        now = datetime.now(timezone.utc)
        stmt = select(StudentMark).where(
            StudentMark.enrollment_id == enrollment_id, StudentMark.offering_assessment_id == offering_assessment_id
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        previous = float(existing.obtained_marks) if existing is not None else None
        if existing is not None:
            existing.obtained_marks = obtained_marks
            existing.entered_by = actor_id
            existing.entered_at = now
            mark = existing
        else:
            mark = StudentMark(
                enrollment_id=enrollment_id, offering_assessment_id=offering_assessment_id,
                obtained_marks=obtained_marks, entered_by=actor_id, entered_at=now,
            )
            self.session.add(mark)
            await self.session.flush()

        await self.audit.log(
            actor_user_id=actor_id, action="student_mark.corrected" if correcting else "student_mark.entered", entity_type="student_mark", entity_id=mark.id,
            before={"obtained_marks": previous} if correcting else None,
            after={
                "enrollment_id": str(enrollment_id), "assessment_type": assessment_type.name,
                "obtained_marks": obtained_marks,
            },
            metadata={"reason": correction_reason.strip()} if correcting else None,
        )

        # Marks are a DRAFT until the course's result sheet is submitted, approved and published. The pass/fail OUTCOME (and the
        # student's notification) therefore only happens once it is published -- after that, a correction recomputes it.
        if correcting:
            from app.academics.models import ResultSheet

            sheet = (await self.session.execute(select(ResultSheet).where(ResultSheet.course_offering_id == enrollment.course_offering_id))).scalar_one()
            if previous is None or previous != obtained_marks:
                sheet.correction_count += 1
            await self._maybe_recompute_outcome(enrollment, actor_id=actor_id, corrected=True)
        return mark

    async def get_marks_for_enrollment(self, enrollment_id: uuid.UUID) -> list[StudentMark]:
        stmt = select(StudentMark).where(StudentMark.enrollment_id == enrollment_id)
        return list((await self.session.execute(stmt)).scalars())

    async def evaluate(self, enrollment: Enrollment) -> dict | None:
        """What this student's result WOULD be, from their marks (nothing is saved). None while any assessment still has no mark."""
        offering_assessments = await self.list_offering_assessments(enrollment.course_offering_id)
        if not offering_assessments:
            return None
        marks_by_assessment = {m.offering_assessment_id: m for m in await self.get_marks_for_enrollment(enrollment.id)}
        if not all(oa.id in marks_by_assessment for oa in offering_assessments):
            return None  # incomplete -- at least one assessment still has no mark
        types = {oa.id: await self.session.get(AssessmentType, oa.assessment_type_id) for oa in offering_assessments}
        total_obtained = sum(float(marks_by_assessment[oa.id].obtained_marks) for oa in offering_assessments)
        total_max = sum(float(types[oa.id].max_marks) for oa in offering_assessments)
        if total_max <= 0:
            return None
        # The raw sum over the raw total: any mix of catalog types gives a sensible percentage without the components adding up to 100.
        final_percentage = round((total_obtained / total_max) * 100, 2)
        # The attendance-eligibility rule, checked live: below it a course can only be FAILED, whatever the score.
        from app.attendance.service import AttendanceService

        attendance = await AttendanceService(self.session).compute_percentage(enrollment.student_id, enrollment.course_offering_id)
        eligible = attendance["percentage"] >= settings.attendance_eligibility_threshold_percent
        if not eligible:
            # Graded attendance fines: once the fine for the student's band is paid (or waived), or they were excused, marks decide.
            from app.attendance.shortfall import cleared

            eligible = await cleared(self.session, enrollment.student_id, enrollment.course_offering_id, attendance["percentage"])
        outcome = EnrollmentOutcome.PASSED if eligible and final_percentage >= settings.marks_pass_threshold_percent else EnrollmentOutcome.FAILED
        return {"percentage": final_percentage, "total_obtained": total_obtained, "total_max": total_max, "attendance_percentage": attendance["percentage"],
                "eligible": eligible, "outcome": outcome, "marks": {oa.id: float(marks_by_assessment[oa.id].obtained_marks) for oa in offering_assessments}}

    async def _maybe_recompute_outcome(self, enrollment: Enrollment, *, actor_id: uuid.UUID, corrected: bool = False) -> None:
        ev = await self.evaluate(enrollment)
        if ev is None:
            return
        outcome = ev["outcome"]
        enrollment.status = EnrollmentStatus.COMPLETED
        enrollment.outcome = outcome
        letter, _points = await self._store_result(enrollment, ev)
        await self.audit.log(
            actor_user_id=actor_id, action="enrollment.outcome_auto_computed", entity_type="enrollment", entity_id=enrollment.id,
            after={"final_percentage": ev["percentage"], "total_obtained": ev["total_obtained"], "total_max": ev["total_max"],
                   "attendance_percentage": ev["attendance_percentage"], "attendance_eligible": ev["eligible"], "outcome": outcome.value, "corrected": corrected},
        )
        student = await self.session.get(Student, enrollment.student_id)
        if student is not None and student.user_id is not None:
            offering = await self.offering_repo.get_by_id(enrollment.course_offering_id)
            course = await self.session.get(Course, offering.course_id) if offering else None
            code = course.code if course else "your course"
            why = "" if ev["eligible"] else f" (attendance below {settings.attendance_eligibility_threshold_percent}% \u2014 ineligible for a pass regardless of score)"
            await create_notification(
                self.session, user_id=student.user_id, category=NotificationCategory.RESULT,
                title=(f"Your result for {code} was corrected: {outcome.value.title()}" if corrected else f"Result published for {code}: {outcome.value.title()}"),
                message=f"Final score: {ev['percentage']}% (grade {letter}).{why}", link="/dashboard/student/results",
            )

    async def _store_result(self, enrollment: Enrollment, ev: dict, *, outcome: EnrollmentOutcome | None = None) -> tuple[str, float]:
        """Freeze this attempt's result (percentage, letter, grade points, credit hours) so a later change to the grading scale never alters it."""
        from app.academics.grade_scale import GradeScaleService, grade_for

        outcome = outcome or ev["outcome"]
        offering = await self.offering_repo.get_by_id(enrollment.course_offering_id)
        course = await self.session.get(Course, offering.course_id)
        letter, points = grade_for(await GradeScaleService(self.session).entries(), ev["percentage"], outcome == EnrollmentOutcome.PASSED)
        row = (await self.session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == enrollment.id))).scalar_one_or_none()
        if row is None:
            row = EnrollmentResult(enrollment_id=enrollment.id)
            self.session.add(row)
        row.percentage, row.total_obtained, row.total_max = ev["percentage"], ev["total_obtained"], ev["total_max"]
        row.letter_grade, row.grade_points, row.credit_hours = letter, points, course.credit_hours
        row.outcome, row.attendance_percentage = outcome.value, ev["attendance_percentage"]
        row.failed_for_attendance = (outcome == EnrollmentOutcome.FAILED) and not ev["eligible"]
        row.computed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return letter, points

    async def publish_outcomes(self, course_offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> int:
        """Called when a result sheet is published: decide pass/fail for every student on the course and tell them."""
        stmt = select(Enrollment).where(Enrollment.course_offering_id == course_offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN)
        n = 0
        for enrollment in (await self.session.execute(stmt)).scalars():
            await self._maybe_recompute_outcome(enrollment, actor_id=actor_id)
            n += 1
        return n