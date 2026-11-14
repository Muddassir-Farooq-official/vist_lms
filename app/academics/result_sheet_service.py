"""The approval workflow for a course's marks: DRAFT -> SUBMITTED -> (RETURNED | APPROVED) -> PUBLISHED."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.grade_scale import GradeScaleService, grade_for
from app.academics.grading_service import GradingService
from app.academics.models import (
    AssessmentType,
    Course,
    CourseOffering,
    Enrollment,
    EnrollmentResult,
    EnrollmentStatus,
    OfferingAssessment,
    ResultSheet,
    ResultSheetStatus,
    StudentMark,
)
from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.config import settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.employees.models import Employee
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.students.models import Student

EDITABLE = (ResultSheetStatus.DRAFT, ResultSheetStatus.RETURNED)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResultSheetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)
        self.grading = GradingService(session)

    # ------------------------------------------------------------------ the sheet
    async def _offering(self, offering_id: uuid.UUID) -> tuple[CourseOffering, Course]:
        offering = await self.session.get(CourseOffering, offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        return offering, await self.session.get(Course, offering.course_id)

    async def get(self, offering_id: uuid.UUID) -> ResultSheet | None:
        return (await self.session.execute(select(ResultSheet).where(ResultSheet.course_offering_id == offering_id))).scalars().first()

    async def get_or_create(self, offering_id: uuid.UUID) -> ResultSheet:
        sheet = await self.get(offering_id)
        if sheet is None:
            sheet = ResultSheet(course_offering_id=offering_id, status=ResultSheetStatus.DRAFT)
            self.session.add(sheet)
            await self.session.flush()
        return sheet

    async def _admin_ids(self) -> list[uuid.UUID]:
        return list(set((await self.session.execute(
            select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.ADMIN, User.is_active.is_(True))
        )).scalars()))

    async def _teacher_user(self, offering: CourseOffering) -> uuid.UUID | None:
        if offering.assigned_teacher_id is None:
            return None
        return (await self.session.execute(select(Employee.user_id).where(Employee.id == offering.assigned_teacher_id))).scalar_one_or_none()

    # ------------------------------------------------------------------ what is entered so far
    async def completeness(self, offering_id: uuid.UUID) -> dict:
        assessments = await self.grading.list_offering_assessments(offering_id)
        enrollments = list((await self.session.execute(select(Enrollment).where(
            Enrollment.course_offering_id == offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN))).scalars())
        marked: dict[uuid.UUID, int] = {}
        if enrollments and assessments:
            marked = {eid: n for eid, n in (await self.session.execute(
                select(StudentMark.enrollment_id, func.count()).where(StudentMark.enrollment_id.in_([e.id for e in enrollments]),
                                                                       StudentMark.offering_assessment_id.in_([a.id for a in assessments])).group_by(StudentMark.enrollment_id))).all()}
        incomplete = [e for e in enrollments if marked.get(e.id, 0) < len(assessments)]
        names = []
        if incomplete:
            for st in (await self.session.execute(select(Student).where(Student.id.in_([e.student_id for e in incomplete[:5]])))).scalars():
                names.append(f"{st.first_name} {st.last_name}")
        return {"students": len(enrollments), "assessments": len(assessments), "complete_students": len(enrollments) - len(incomplete),
                "missing_students": len(incomplete), "missing_names": names,
                "ready": bool(assessments) and bool(enrollments) and not incomplete}

    def _need_complete(self, c: dict, what: str) -> None:
        if not c["assessments"]:
            raise BusinessRuleViolationError(f"Add at least one assessment (a quiz, a mid-term...) before you {what}.")
        if not c["students"]:
            raise BusinessRuleViolationError(f"There are no students on this course to {what}.")
        if c["missing_students"]:
            extra = f" ({', '.join(c['missing_names'])}{'...' if c['missing_students'] > len(c['missing_names']) else ''})" if c["missing_names"] else ""
            raise BusinessRuleViolationError(f"Marks are missing for {c['missing_students']} student{'s' if c['missing_students'] != 1 else ''}{extra}. Enter every mark (0 for an absentee) before you {what}.")

    # ------------------------------------------------------------------ the teacher
    async def detail(self, offering_id: uuid.UUID) -> dict:
        offering, course = await self._offering(offering_id)
        sheet = await self.get(offering_id)
        status = sheet.status if sheet else ResultSheetStatus.DRAFT
        c = await self.completeness(offering_id)
        return {"course_offering_id": offering_id, "course_code": course.code, "course_title": course.title, "status": status, "can_edit": status in EDITABLE,
                "can_submit": status in EDITABLE and c["ready"], "completeness": c, "return_reason": sheet.return_reason if sheet and status == ResultSheetStatus.RETURNED else None,
                "returned_at": sheet.returned_at if sheet else None, "submitted_at": sheet.submitted_at if sheet else None, "approved_at": sheet.approved_at if sheet else None,
                "published_at": sheet.published_at if sheet else None, "correction_count": sheet.correction_count if sheet else 0, "resubmission_count": sheet.resubmission_count if sheet else 0}

    async def submit(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> ResultSheet:
        offering, course = await self._offering(offering_id)
        sheet = await self.get_or_create(offering_id)
        if sheet.status not in EDITABLE:
            raise BusinessRuleViolationError("These marks have already been submitted.")
        self._need_complete(await self.completeness(offering_id), "submit")
        sheet.status, sheet.submitted_by, sheet.submitted_at, sheet.return_reason = ResultSheetStatus.SUBMITTED, actor_id, _now(), None
        await self.audit.log(actor_user_id=actor_id, action="result_sheet.submitted", entity_type="result_sheet", entity_id=sheet.id)
        exam_controllers = set((await self.session.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.EXAM_CONTROLLER))).scalars())
        for user, link in [(a, "/dashboard/admin/result-sheets") for a in await self._admin_ids()] + [(u, "/dashboard/exam") for u in exam_controllers]:
            if user != actor_id:
                await create_notification(self.session, user_id=user, category=NotificationCategory.RESULT, title=f"Marks submitted for approval: {course.code}",
                                          message=f"{course.title} \u2014 Section {offering.section_label}, {offering.term_label}.", link=link)
        return sheet

    # ------------------------------------------------------------------ the approver
    async def _sheet(self, offering_id: uuid.UUID, allowed: tuple, what: str) -> ResultSheet:
        sheet = await self.get(offering_id)
        if sheet is None or sheet.status not in allowed:
            raise BusinessRuleViolationError(f"This sheet cannot be {what} in its current state ({(sheet.status.value if sheet else 'DRAFT').replace('_', ' ').lower()}).")
        return sheet

    async def return_sheet(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str) -> ResultSheet:
        offering, course = await self._offering(offering_id)
        sheet = await self._sheet(offering_id, (ResultSheetStatus.SUBMITTED, ResultSheetStatus.APPROVED), "sent back")
        sheet.status, sheet.returned_by, sheet.returned_at, sheet.return_reason = ResultSheetStatus.RETURNED, actor_id, _now(), reason.strip()
        sheet.approved_by = sheet.approved_at = None
        sheet.resubmission_count += 1
        await self.audit.log(actor_user_id=actor_id, action="result_sheet.returned", entity_type="result_sheet", entity_id=sheet.id, metadata={"reason": reason.strip()})
        teacher = await self._teacher_user(offering)
        if teacher:
            await create_notification(self.session, user_id=teacher, category=NotificationCategory.RESULT, title=f"Marks sent back: {course.code}",
                                      message=f"{reason.strip()}", link="/dashboard/faculty/grading")
        return sheet

    async def approve(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> ResultSheet:
        offering, course = await self._offering(offering_id)
        sheet = await self._sheet(offering_id, (ResultSheetStatus.SUBMITTED,), "approved")
        # Separation of duties: the person who entered and submitted the marks (or teaches the
        # course) does not approve them; someone else checks -- unless they are the only approver there is.
        from app.auth.permissions import Permissions
        from app.core.controls import four_eyes

        teacher_user = await self._teacher_user(offering)
        alone = await four_eyes(self.session, actor_id, [sheet.submitted_by, teacher_user], Permissions.RESULT_APPROVE,
                                "You submitted or teach these marks, so another approver (exam office or administration) must approve them.")
        self._need_complete(await self.completeness(offering_id), "approve")
        sheet.status, sheet.approved_by, sheet.approved_at = ResultSheetStatus.APPROVED, actor_id, _now()
        await self.audit.log(actor_user_id=actor_id, action="result_sheet.approved", entity_type="result_sheet", entity_id=sheet.id,
                             metadata={"self_approved_only_approver": True} if alone else None)
        teacher = teacher_user
        if teacher:
            await create_notification(self.session, user_id=teacher, category=NotificationCategory.RESULT, title=f"Marks approved: {course.code}",
                                      message="They will be published to students by the administration.", link="/dashboard/faculty/grading")
        return sheet

    async def publish(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID,
                      override_reason: str | None = None, actor_is_admin: bool = False) -> ResultSheet:
        offering, course = await self._offering(offering_id)
        sheet = await self._sheet(offering_id, (ResultSheetStatus.APPROVED,), "published")
        self._need_complete(await self.completeness(offering_id), "publish")
        # Publishing decides pass/fail, including the attendance-eligibility rule,
        # so attendance must be final first. Override: ADMIN only, with a reason.
        if settings.require_attendance_lock_before_publish and offering.attendance_locked_at is None:
            reason = (override_reason or "").strip()
            if not reason:
                raise BusinessRuleViolationError(
                    f"{course.code}: attendance is not locked yet. Lock this course's attendance at the end of term "
                    "(Attendance page), then publish, so pass/fail uses the final attendance.",
                    details={"needs": "ATTENDANCE_LOCK", "override_allowed": actor_is_admin},
                )
            if not actor_is_admin:
                raise BusinessRuleViolationError(f"{course.code}: attendance is not locked yet. Only an administrator can publish before that.")
            if len(reason) < 10:
                raise BusinessRuleViolationError("Give a reason of at least 10 characters for publishing before attendance is locked.")
            await self.audit.log(actor_user_id=actor_id, action="result_sheet.published_before_attendance_lock",
                                 entity_type="result_sheet", entity_id=sheet.id, metadata={"reason": reason})
        sheet.status, sheet.published_by, sheet.published_at = ResultSheetStatus.PUBLISHED, actor_id, _now()
        decided = await self.grading.publish_outcomes(offering_id, actor_id=actor_id)
        from app.sms.service import SmsService

        sms = SmsService(self.session)
        for sid in (await self.session.execute(select(Enrollment.student_id).where(Enrollment.course_offering_id == offering_id,
                                                                                  Enrollment.status != EnrollmentStatus.WITHDRAWN))).scalars():
            await sms.queue_for_student(sid, event="RESULTS", attachment=f"RESULT:{sid}",
                                        body=f"{settings.app_name}: result for {course.code} {course.title[:40]} ({offering.term_label}) is published. See My Results.")
        await self.audit.log(actor_user_id=actor_id, action="result_sheet.published", entity_type="result_sheet", entity_id=sheet.id, metadata={"students": decided})
        teacher = await self._teacher_user(offering)
        if teacher:
            await create_notification(self.session, user_id=teacher, category=NotificationCategory.RESULT, title=f"Results published: {course.code}",
                                      message=f"{decided} students can now see their results.", link="/dashboard/faculty/grading")
        return sheet

    async def publish_all_approved(self, *, actor_id: uuid.UUID) -> dict:
        ids = list((await self.session.execute(select(ResultSheet.course_offering_id).where(ResultSheet.status == ResultSheetStatus.APPROVED))).scalars())
        done, failed = [], []
        for oid in ids:
            try:
                async with self.session.begin_nested():
                    await self.publish(oid, actor_id=actor_id)
                done.append(oid)
            except BusinessRuleViolationError as exc:
                failed.append({"course_offering_id": oid, "reason": str(exc)})
        return {"published": len(done), "failed": failed}

    # ------------------------------------------------------------------ what the approver sees
    async def listing(self, status: ResultSheetStatus | None) -> list[dict]:
        stmt = select(ResultSheet, CourseOffering, Course).join(CourseOffering, CourseOffering.id == ResultSheet.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
        # Past results imported from before the system (section IMPORTED) are not sheets anyone approves; keep them off this screen.
        stmt = stmt.where(CourseOffering.section_label != "IMPORTED")
        if status:
            stmt = stmt.where(ResultSheet.status == status)
        rows = (await self.session.execute(stmt.order_by(ResultSheet.submitted_at.asc().nullslast(), Course.code))).all()
        teachers = {e.id: f"{e.first_name} {e.last_name}" for e in (await self.session.execute(select(Employee).where(Employee.id.in_([o.assigned_teacher_id for _, o, _ in rows if o.assigned_teacher_id])))).scalars()} if rows else {}
        out = []
        for sheet, offering, course in rows:
            c = await self.completeness(offering.id)
            out.append({"course_offering_id": offering.id, "course_code": course.code, "course_title": course.title, "section_label": offering.section_label, "term_label": offering.term_label,
                        "teacher_name": teachers.get(offering.assigned_teacher_id), "status": sheet.status, "students": c["students"], "assessments": c["assessments"],
                        "missing_students": c["missing_students"], "submitted_at": sheet.submitted_at, "approved_at": sheet.approved_at, "published_at": sheet.published_at,
                        "return_reason": sheet.return_reason, "correction_count": sheet.correction_count, "resubmission_count": sheet.resubmission_count})
        return out

    async def outstanding(self) -> list[dict]:
        """Courses with students whose marks have NOT been submitted yet (no sheet started, still a draft, or
        sent back): what the exam office chases teachers for before result day."""
        from sqlalchemy import func as _func

        counts = dict((await self.session.execute(
            select(Enrollment.course_offering_id, _func.count()).where(Enrollment.status != EnrollmentStatus.WITHDRAWN)
            .group_by(Enrollment.course_offering_id))).all())
        if not counts:
            return []
        rows = (await self.session.execute(
            select(CourseOffering, Course, ResultSheet).join(Course, Course.id == CourseOffering.course_id)
            .outerjoin(ResultSheet, ResultSheet.course_offering_id == CourseOffering.id)
            .where(CourseOffering.id.in_(list(counts)), CourseOffering.section_label != "IMPORTED")
            .order_by(CourseOffering.term_label, Course.code, CourseOffering.section_label))).all()
        rows = [(o, c, s) for o, c, s in rows if s is None or s.status in (ResultSheetStatus.DRAFT, ResultSheetStatus.RETURNED)]
        teachers = {e.id: f"{e.first_name} {e.last_name}" for e in (await self.session.execute(
            select(Employee).where(Employee.id.in_([o.assigned_teacher_id for o, _, _ in rows if o.assigned_teacher_id])))).scalars()} if rows else {}
        out = []
        for o, c, s in rows:
            comp = await self.completeness(o.id)
            out.append({"course_offering_id": o.id, "course_code": c.code, "course_title": c.title, "section_label": o.section_label, "term_label": o.term_label,
                        "teacher_name": teachers.get(o.assigned_teacher_id), "status": (s.status if s else ResultSheetStatus.DRAFT), "students": counts.get(o.id, 0),
                        "missing_students": comp["missing_students"], "assessments": comp["assessments"], "return_reason": s.return_reason if s else None,
                        "attendance_locked": o.attendance_locked_at is not None})
        return out

    async def review(self, offering_id: uuid.UUID) -> dict:
        """The marks of a whole class, with each student's percentage and what the result WOULD be (nothing is saved)."""
        offering, course = await self._offering(offering_id)
        info = await self.detail(offering_id)
        assessments = await self.grading.list_offering_assessments(offering_id)
        types = {a.id: await self.session.get(AssessmentType, a.assessment_type_id) for a in assessments}
        enrollments = list((await self.session.execute(select(Enrollment).where(Enrollment.course_offering_id == offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN))).scalars())
        students = {s.id: s for s in (await self.session.execute(select(Student).where(Student.id.in_([e.student_id for e in enrollments])))).scalars()} if enrollments else {}
        scale = await GradeScaleService(self.session).entries()
        frozen = {r.enrollment_id: r for r in (await self.session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id.in_([e.id for e in enrollments])))).scalars()} if enrollments else {}
        rows, percentages = [], []
        for e in sorted(enrollments, key=lambda e: (students[e.student_id].roll_number or "")):
            st = students[e.student_id]
            marks = {m.offering_assessment_id: float(m.obtained_marks) for m in await self.grading.get_marks_for_enrollment(e.id)}
            ev = await self.grading.evaluate(e)
            if ev:
                percentages.append(ev["percentage"])
            published = info["status"] == ResultSheetStatus.PUBLISHED and e.id in frozen
            short = bool(ev) and not ev["eligible"]
            if published:
                letter, points = frozen[e.id].letter_grade, float(frozen[e.id].grade_points)
                outcome = "SHORT_ATTENDANCE" if frozen[e.id].failed_for_attendance else (e.outcome.value if e.outcome else None)
            elif ev:
                # Short attendance bars the student from the final paper; it is NOT shown as a failed grade during the
                # term: the projection shows the grade the marks earn, and "Short attendance" instead of Pass/Fail.
                by_marks = ev["percentage"] >= settings.marks_pass_threshold_percent
                letter, points = grade_for(scale, ev["percentage"], by_marks if short else ev["outcome"].value == "PASSED")
                outcome = "SHORT_ATTENDANCE" if short else ev["outcome"].value
            else:
                letter, points, outcome = None, None, None
            rows.append({"enrollment_id": e.id, "student_name": f"{st.first_name} {st.last_name}", "roll_number": st.roll_number, "grade": letter, "grade_points": points,
                         "marks": [marks.get(a.id) for a in assessments], "percentage": ev["percentage"] if ev else None, "attendance_percentage": ev["attendance_percentage"] if ev else None,
                         "attendance_eligible": ev["eligible"] if ev else None, "outcome": outcome, "is_projection": info["status"] != ResultSheetStatus.PUBLISHED})
        passed = sum(1 for r in rows if r["outcome"] == "PASSED")
        return {**info, "section_label": offering.section_label, "term_label": offering.term_label,
                "assessments_list": [{"id": a.id, "name": types[a.id].name, "max_marks": float(types[a.id].max_marks)} for a in assessments], "rows": rows,
                "stats": {"students": len(rows), "average": round(sum(percentages) / len(percentages), 1) if percentages else None, "highest": max(percentages) if percentages else None,
                          "lowest": min(percentages) if percentages else None, "passed": passed, "failed": sum(1 for r in rows if r["outcome"] in ("FAILED", "SHORT_ATTENDANCE")),
                          "pass_rate": round(passed / len(rows) * 100, 1) if rows else None,
                          "ineligible_for_attendance": sum(1 for r in rows if r["attendance_eligible"] is False),
                          "short_attendance": sum(1 for r in rows if r["outcome"] == "SHORT_ATTENDANCE"),
                          "distribution": [{"grade": e.letter, "count": sum(1 for r in rows if r["grade"] == e.letter)} for e in scale if any(r["grade"] == e.letter for r in rows)]}}