from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
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
        self, *, name: str, category: str | None, max_marks: float, actor_id: uuid.UUID, component: str | None = None
    ) -> AssessmentType:
        if max_marks <= 0:
            raise BusinessRuleViolationError("max_marks must be greater than 0.")
        existing_stmt = select(AssessmentType).where(AssessmentType.name == name.strip())
        if (await self.session.execute(existing_stmt)).scalar_one_or_none() is not None:
            raise BusinessRuleViolationError(f"A type named '{name.strip()}' already exists.")

        assessment_type = AssessmentType(
            name=name.strip(), category=(category.strip() if category else None), max_marks=max_marks, component=component
        )
        self.session.add(assessment_type)
        await self.session.flush()

        await self.audit.log(
            actor_user_id=actor_id, action="assessment_type.created", entity_type="assessment_type",
            entity_id=assessment_type.id, after={"name": assessment_type.name, "max_marks": max_marks},
        )
        return assessment_type

    async def set_component(self, assessment_type_id: uuid.UUID, component: str | None, *, actor_id: uuid.UUID) -> AssessmentType:
        assessment_type = await self.session.get(AssessmentType, assessment_type_id)
        if assessment_type is None:
            raise ResourceNotFoundError("Assessment type not found.")
        before = assessment_type.component
        assessment_type.component = component
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="assessment_type.component_set", entity_type="assessment_type",
                             entity_id=assessment_type.id, before={"component": before}, after={"component": component})
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


    async def usage(self, assessment_type_id: uuid.UUID) -> dict:
        """What a forced delete would remove: subjects using the type, their published results, students and marks."""
        from sqlalchemy import func

        from app.academics.models import ResultSheet, ResultSheetStatus
        from app.supplementary.models import SupplementaryAttempt

        t = await self.session.get(AssessmentType, assessment_type_id)
        if t is None:
            raise ResourceNotFoundError("Assessment type not found.")
        oas = list((await self.session.execute(select(OfferingAssessment).where(OfferingAssessment.assessment_type_id == t.id))).scalars())
        oa_ids = [o.id for o in oas]
        offering_ids = list({o.course_offering_id for o in oas})
        marks = students = published = supp = 0
        if oa_ids:
            marks = (await self.session.execute(select(func.count()).select_from(StudentMark).where(StudentMark.offering_assessment_id.in_(oa_ids)))).scalar_one()
            students = (await self.session.execute(select(func.count(func.distinct(StudentMark.enrollment_id)))
                                                   .where(StudentMark.offering_assessment_id.in_(oa_ids)))).scalar_one()
            published = (await self.session.execute(select(func.count()).select_from(ResultSheet).where(
                ResultSheet.course_offering_id.in_(offering_ids), ResultSheet.status == ResultSheetStatus.PUBLISHED))).scalar_one()
            supp = (await self.session.execute(select(func.count()).select_from(SupplementaryAttempt).where(
                (SupplementaryAttempt.offering_assessment_id.in_(oa_ids)) | (SupplementaryAttempt.second_assessment_id.in_(oa_ids))))).scalar_one()
        return {"id": t.id, "name": t.name, "subjects": len(offering_ids), "subjects_published": int(published), "students": int(students),
                "marks": int(marks), "supplementary_attempts": int(supp)}

    async def marks_backup_csv(self, assessment_type_id: uuid.UUID) -> str:
        """Every mark given under this type, as CSV (taken before a forced delete)."""
        import csv
        import io

        from app.academics.models import Course, CourseOffering, Enrollment

        t = await self.session.get(AssessmentType, assessment_type_id)
        if t is None:
            raise ResourceNotFoundError("Assessment type not found.")
        rows = (await self.session.execute(
            select(Course.code, CourseOffering.section_label, CourseOffering.term_label, Student.roll_number, Student.first_name, Student.last_name,
                   StudentMark.obtained_marks, StudentMark.updated_at)
            .join(OfferingAssessment, OfferingAssessment.id == StudentMark.offering_assessment_id)
            .join(CourseOffering, CourseOffering.id == OfferingAssessment.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .join(Enrollment, Enrollment.id == StudentMark.enrollment_id)
            .join(Student, Student.id == Enrollment.student_id)
            .where(OfferingAssessment.assessment_type_id == t.id)
            .order_by(Course.code, CourseOffering.section_label, Student.roll_number))).all()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["assessment", "max_marks", "course", "section", "term", "roll_number", "student", "obtained_marks", "last_changed"])
        for code, sec, term, roll, fn, ln, obtained, changed in rows:
            w.writerow([t.name, float(t.max_marks), code, sec or "", term or "", roll or "", f"{fn} {ln}", float(obtained), changed.isoformat() if changed else ""])
        return buf.getvalue()

    async def force_delete(self, assessment_type_id: uuid.UUID, *, confirm_name: str, actor_id: uuid.UUID) -> dict:
        """Delete a type that subjects use, with every student's marks for it. Published results (stored separately) are
        NOT changed. Subjects waiting for approval go back to their teacher to check. Refused while any supplementary
        re-sit uses it (that would erase re-sit history)."""
        from app.academics.models import ResultSheet, ResultSheetStatus

        info = await self.usage(assessment_type_id)
        if " ".join(confirm_name.split()).lower() != info["name"].strip().lower():
            raise BusinessRuleViolationError(f"Type the exam type's name exactly ({info['name']}) to confirm.")
        if info["supplementary_attempts"]:
            raise BusinessRuleViolationError(f"{info['supplementary_attempts']} supplementary re-sit(s) use {info['name']}. "
                                             "It cannot be deleted without erasing re-sit history.")
        oas = list((await self.session.execute(select(OfferingAssessment).where(OfferingAssessment.assessment_type_id == assessment_type_id))).scalars())
        offering_ids = {o.course_offering_id for o in oas}
        returned = 0
        if offering_ids:
            sheets = (await self.session.execute(select(ResultSheet).where(ResultSheet.course_offering_id.in_(offering_ids),
                                                                           ResultSheet.status.in_((ResultSheetStatus.SUBMITTED, ResultSheetStatus.APPROVED))))).scalars().all()
            for sh in sheets:
                sh.status = ResultSheetStatus.RETURNED
                sh.returned_by, sh.returned_at = actor_id, datetime.now(timezone.utc)
                sh.return_reason = f"The exam type {info['name']} was deleted with its marks. Check the result and submit again."
                returned += 1
            await self.session.execute(delete(StudentMark).where(StudentMark.offering_assessment_id.in_([o.id for o in oas])))
            for o in oas:
                await self.session.delete(o)
        t = await self.session.get(AssessmentType, assessment_type_id)
        await self.audit.log(actor_user_id=actor_id, action="assessment_type.force_deleted", entity_type="assessment_type", entity_id=t.id,
                             before={"name": t.name, "max_marks": float(t.max_marks)},
                             metadata={k: v for k, v in info.items() if k not in ("id", "name")} | {"sheets_returned": returned})
        await self.session.delete(t)
        await self.session.flush()
        return {**{k: v for k, v in info.items() if k != "id"}, "sheets_returned": returned}


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
        self, course_offering_id: uuid.UUID, *, assessment_type_id: uuid.UUID, actor_id: uuid.UUID, mirror: bool = True
    ) -> OfferingAssessment:
        offering = await self.offering_repo.get_by_id(course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        await self._assert_structure_editable(course_offering_id)
        assessment_type = await self.session.get(AssessmentType, assessment_type_id)
        if assessment_type is None:
            raise ResourceNotFoundError("Assessment type not found.")
        from app.academics.affiliation import check_part_allowed

        await check_part_allowed(self.session, course_offering_id, assessment_type.component, doing=f"add {assessment_type.name} to this subject")

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
        if mirror and offering.combined_group_id is not None:        # a combined class keeps one marks scheme
            from app.academics.combined import members

            for other in await members(self.session, offering):
                if other.id != offering.id and (await self.session.execute(select(OfferingAssessment.id).where(
                        OfferingAssessment.course_offering_id == other.id, OfferingAssessment.assessment_type_id == assessment_type_id))).first() is None:
                    await self.add_offering_assessment(other.id, assessment_type_id=assessment_type_id, actor_id=actor_id, mirror=False)
        return offering_assessment

    async def remove_offering_assessment(self, offering_assessment_id: uuid.UUID, *, actor_id: uuid.UUID, mirror: bool = True) -> None:
        offering_assessment = await self.session.get(OfferingAssessment, offering_assessment_id)
        if offering_assessment is None:
            raise ResourceNotFoundError("This subject's assessment entry was not found.")
        if mirror:                                                    # a combined class keeps one marks scheme
            from app.academics.combined import members

            for other in await members(self.session, offering_assessment.course_offering_id):
                if other.id == offering_assessment.course_offering_id:
                    continue
                twin = (await self.session.execute(select(OfferingAssessment).where(
                    OfferingAssessment.course_offering_id == other.id,
                    OfferingAssessment.assessment_type_id == offering_assessment.assessment_type_id))).scalar_one_or_none()
                if twin is not None:
                    await self.remove_offering_assessment(twin.id, actor_id=actor_id, mirror=False)
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
        from app.academics.grading_schemes import COMPONENT_LABELS, OFFICE_ONLY

        from app.academics.affiliation import check_part_allowed

        await check_part_allowed(self.session, enrollment.course_offering_id, assessment_type.component, doing=f"enter {assessment_type.name} marks")
        if assessment_type.component in OFFICE_ONLY and not is_admin:
            raise BusinessRuleViolationError(f"{assessment_type.name} is a {COMPONENT_LABELS[assessment_type.component].lower()} mark, "
                                             "entered by the exam office.")

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

    async def evaluate(self, enrollment: Enrollment, *, override: dict | None = None) -> dict | None:
        """What this student's result WOULD be, from their marks (nothing is saved). None while any assessment still has no mark.
        `override` = {offering_assessment_id: marks} replaces some marks (a supplementary re-sit preview).

        The program's grading table decides the pass mark and the grade (app/academics/grading_schemes.py). With a mark split
        (program or subject), each part (Sessional / Mid / Final / Practical) is scaled to its marks and theory and practical
        must each be passed; without one, the result is the plain sum of the marks, as before."""
        from app.academics.grading_schemes import rules_for_program, split_for, weighted_result

        offering_assessments = await self.list_offering_assessments(enrollment.course_offering_id)
        if not offering_assessments:
            return None
        marks = {m.offering_assessment_id: float(m.obtained_marks) for m in await self.get_marks_for_enrollment(enrollment.id)}
        marks.update(override or {})
        if not all(oa.id in marks for oa in offering_assessments):
            return None  # incomplete -- at least one assessment still has no mark
        types = {oa.id: await self.session.get(AssessmentType, oa.assessment_type_id) for oa in offering_assessments}
        student = await self.session.get(Student, enrollment.student_id)
        program_id = student.program_id if student else None
        rules = await rules_for_program(self.session, program_id)
        offering = await self.offering_repo.get_by_id(enrollment.course_offering_id)
        split = await split_for(self.session, course_id=offering.course_id if offering else None, program_id=program_id)
        theory = practical = None
        failed_parts: list[str] = []
        missing_parts: list[str] = []
        if split:
            parts: dict[str, tuple[float, float]] = {}
            for oa in offering_assessments:
                comp = types[oa.id].component or "SESSIONAL"
                o, m = parts.get(comp, (0.0, 0.0))
                parts[comp] = (o + marks[oa.id], m + float(types[oa.id].max_marks))
            w = weighted_result(split, parts, rules)
            if w["total_max"] <= 0:
                return None
            final_percentage, total_obtained, total_max = w["percentage"], w["total_obtained"], w["total_max"]
            theory, practical, failed_parts, missing_parts = w["theory_percent"], w["practical_percent"], w["failed_parts"], w["missing_parts"]
        else:
            total_obtained = sum(marks[oa.id] for oa in offering_assessments)
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
        marks_passed = final_percentage >= rules.pass_percent and not failed_parts
        outcome = EnrollmentOutcome.PASSED if eligible and marks_passed else EnrollmentOutcome.FAILED
        letter, points, division = rules.grade(final_percentage, outcome == EnrollmentOutcome.PASSED)
        marks_letter, marks_points, _ = rules.grade(final_percentage, marks_passed)
        return {"percentage": final_percentage, "total_obtained": round(total_obtained, 2), "total_max": round(total_max, 2),
                "attendance_percentage": attendance["percentage"], "eligible": eligible, "outcome": outcome, "marks_passed": marks_passed,
                "letter": letter, "grade_points": points, "division": division, "marks_letter": marks_letter, "marks_grade_points": marks_points,
                "theory_percent": theory, "practical_percent": practical, "failed_parts": failed_parts, "missing_parts": missing_parts,
                "scheme_name": rules.name, "rules": rules, "marks": {oa.id: marks[oa.id] for oa in offering_assessments}}

    async def _maybe_recompute_outcome(self, enrollment: Enrollment, *, actor_id: uuid.UUID, corrected: bool = False) -> None:
        from app.academics.affiliation import is_affiliated, program_of_offering

        offering = await self.offering_repo.get_by_id(enrollment.course_offering_id)
        if offering is not None and is_affiliated(await program_of_offering(self.session, offering)):
            return          # an affiliated program's result is the university's (imported), never computed from internal marks
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
            why = "" if ev["eligible"] else (f" Short attendance: below the minimum {settings.attendance_eligibility_threshold_percent:g}%, so you were not allowed "
                                             "in the final exam and this subject must be repeated.")
            await create_notification(
                self.session, user_id=student.user_id, category=NotificationCategory.RESULT,
                title=(f"Your result for {code} was corrected: {outcome.value.title()}" if corrected else f"Result published for {code}: {outcome.value.title()}"),
                message=f"Final score: {ev['percentage']}% (grade {letter}).{why}", link="/dashboard/student/results",
            )

    async def _store_result(self, enrollment: Enrollment, ev: dict, *, outcome: EnrollmentOutcome | None = None) -> tuple[str, float]:
        """Freeze this attempt's result (percentage, letter, grade points, credit hours) so a later change to the grading scale never alters it."""
        outcome = outcome or ev["outcome"]
        offering = await self.offering_repo.get_by_id(enrollment.course_offering_id)
        course = await self.session.get(Course, offering.course_id)
        letter, points, division = ev["rules"].grade(ev["percentage"], outcome == EnrollmentOutcome.PASSED)
        row = (await self.session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == enrollment.id))).scalar_one_or_none()
        if row is None:
            row = EnrollmentResult(enrollment_id=enrollment.id)
            self.session.add(row)
        row.percentage, row.total_obtained, row.total_max = ev["percentage"], ev["total_obtained"], ev["total_max"]
        row.letter_grade, row.grade_points, row.credit_hours = letter, points, course.credit_hours
        row.outcome, row.attendance_percentage = outcome.value, ev["attendance_percentage"]
        row.failed_for_attendance = (outcome == EnrollmentOutcome.FAILED) and not ev["eligible"]
        row.theory_percent, row.practical_percent = ev.get("theory_percent"), ev.get("practical_percent")
        row.failed_part = "+".join(ev.get("failed_parts") or []) or None if outcome == EnrollmentOutcome.FAILED else None
        row.scheme_name, row.division = ev.get("scheme_name"), division
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