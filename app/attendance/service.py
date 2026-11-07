from __future__ import annotations

from app.core.clock import institute_now

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, ClassSessionStatus, CourseOffering, EnrollmentStatus
from app.academics.repository import ClassSessionRepository, CourseOfferingRepository, EnrollmentRepository
from app.attendance.models import AttendanceCorrectionRequest, AttendanceRecord, AttendanceStatus
from app.attendance.repository import AttendanceRepository
from app.audit.service import AuditService
from app.auth.models import User
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.students.models import Student


class AttendanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AttendanceRepository(session)
        self.session_repo = ClassSessionRepository(session)
        self.offering_repo = CourseOfferingRepository(session)
        self.enrollment_repo = EnrollmentRepository(session)
        self.audit = AuditService(session)

    async def _get_session_and_offering(self, class_session_id: uuid.UUID) -> tuple[ClassSession, CourseOffering]:
        class_session = await self.session_repo.get_by_id(class_session_id)
        if class_session is None:
            raise ResourceNotFoundError("Class session not found.")
        offering = await self.offering_repo.get_by_id(class_session.course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        return class_session, offering

    async def mark_session(
        self, class_session_id: uuid.UUID, *, present_student_ids: list[uuid.UUID], actor_id: uuid.UUID,
        actor_is_admin: bool = False, late_note: str | None = None,
    ) -> list[AttendanceRecord]:
        """All-or-nothing per session: every currently-ENROLLED student
        gets a record — PRESENT if their id is in present_student_ids,
        ABSENT otherwise (approved default: unmarked = absent, so
        there's no such thing as a partially-taken register). Safe to
        call again for the same session — an existing record for a
        student is updated in place rather than duplicated (that's how
        a correction happens, up until the offering's attendance is
        locked or its term concludes).

        Approved rule: nobody, including admin, can mark a session
        before its start_time — there's no legitimate "early"
        attendance data. From start_time onward, a TEACHER has
        settings.attendance_marking_window_minutes to mark it. ADMIN
        is exempt from that closing side of the window, but must
        supply late_note when marking after it — stored directly on
        the session (see ClassSession.late_marking_note), which is
        also how admin's 'needs attention' view (see
        list_overdue_unmarked_sessions) knows a session was resolved."""
        class_session, offering = await self._get_session_and_offering(class_session_id)
        if offering.attendance_locked_at is not None:
            raise BusinessRuleViolationError(
                "Attendance for this offering has been locked and can no longer be marked or corrected."
            )
        if class_session.status == ClassSessionStatus.CANCELLED:
            raise BusinessRuleViolationError("This session has been cancelled and cannot have attendance marked.")

        session_start = datetime.combine(class_session.session_date, class_session.start_time)
        window_end = session_start + timedelta(minutes=settings.attendance_marking_window_minutes)
        # Naive local comparison, deliberately — class times are stored
        # as plain wall-clock values with no institute timezone concept
        # anywhere else in this app, so datetime.now() (also naive) is
        # the consistent comparison, not datetime.now(timezone.utc).
        now_local = institute_now()

        if now_local < session_start:
            # No admin exemption here, unlike the late-marking case below —
            # there's no legitimate reason to have attendance data for a
            # class that hasn't started yet, for anyone.
            raise BusinessRuleViolationError(
                "This session hasn't started yet — attendance can only be marked from its start time onward."
            )

        window_closed = now_local > window_end

        if window_closed and not actor_is_admin:
            raise BusinessRuleViolationError(
                f"The {settings.attendance_marking_window_minutes}-minute window to mark this session's "
                "attendance has passed. Ask admin to mark it."
            )
        if window_closed and actor_is_admin and not late_note:
            raise BusinessRuleViolationError(
                "This session is outside the normal marking window — add a note explaining why (e.g. teacher was unwell)."
            )

        enrolled_ids = await self.repo.list_enrolled_student_ids_for_offering(offering.id)
        present_set = set(present_student_ids)
        unknown_ids = present_set - set(enrolled_ids)
        if unknown_ids:
            raise BusinessRuleViolationError(
                "One or more students in the register are not currently enrolled in this offering."
            )

        now = datetime.now(timezone.utc)
        records: list[AttendanceRecord] = []
        changed: list[dict] = []
        for student_id in enrolled_ids:
            status = AttendanceStatus.PRESENT if student_id in present_set else AttendanceStatus.ABSENT
            existing = await self.repo.get_for_session_and_student(class_session_id, student_id)
            if existing is not None and existing.status == AttendanceStatus.LEAVE and status == AttendanceStatus.ABSENT:
                records.append(existing)              # approved leave stays leave (only "present" overrides it)
                continue
            if existing is not None:
                if existing.status != status:          # an edit: who changed from what to what is kept in the audit log
                    changed.append({"student_id": str(student_id), "from": existing.status.value, "to": status.value})
                existing.status = status
                existing.marked_by = actor_id
                existing.marked_at = now
                records.append(existing)
            else:
                records.append(
                    await self.repo.create(
                        AttendanceRecord(
                            class_session_id=class_session_id, student_id=student_id, status=status,
                            marked_by=actor_id, marked_at=now,
                        )
                    )
                )

        if window_closed and actor_is_admin:
            class_session.late_marking_note = late_note
        if class_session.attendance_taken_at is None:
            class_session.attendance_taken_at = now

        await self.audit.log(
            actor_user_id=actor_id,
            action="attendance.marked",
            entity_type="class_session",
            entity_id=class_session_id,
            after={
                "enrolled_count": len(enrolled_ids),
                "present_count": len(present_set),
                "absent_count": len(enrolled_ids) - len(present_set),
                "marked_late_by_admin": window_closed and actor_is_admin,
                "late_note": late_note if (window_closed and actor_is_admin) else None,
                "changed": changed,
            },
        )
        return records

    async def compute_percentage(self, student_id: uuid.UUID, course_offering_id: uuid.UUID) -> dict:
        """Present classes / classes taken (cancelled classes excluded).
        Attendance is Present or Absent -- nothing else. Returns 100% (nothing
        to penalize yet) when no class has been taken."""
        records = await self.repo.list_for_student_in_offering(student_id, course_offering_id)
        leave = sum(1 for r in records if r.status == AttendanceStatus.LEAVE)
        present = sum(1 for r in records if r.status == AttendanceStatus.PRESENT)
        total = len(records) - leave                   # approved leave is left out of the count
        percentage = 100.0 if total == 0 else round((present / total) * 100, 2)
        return {
            "total_sessions_taken": total, "present_count": present, "percentage": percentage,
            "absent_count": total - present, "leave_count": leave,
        }

    async def grant_leave(self, student_id: uuid.UUID, *, from_date, to_date, reason: str, actor_id: uuid.UUID) -> dict:
        """Approved leave (medical, bereavement, official duty): every class of the student's current
        courses in those days is recorded as LEAVE, so it neither counts for nor against attendance.
        Classes already marked PRESENT stay present; locked courses are left alone."""
        from app.academics.models import ClassSession, CourseOffering, Enrollment, EnrollmentStatus

        if to_date < from_date:
            raise BusinessRuleViolationError("The last day of leave is before the first.")
        if (to_date - from_date).days > 120:
            raise BusinessRuleViolationError("Leave can be recorded for at most 120 days at a time.")
        if len((reason or "").strip()) < 4:
            raise BusinessRuleViolationError("Give the reason for the leave (e.g. medical certificate no.).")
        rows = (await self.session.execute(
            select(ClassSession, CourseOffering).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
            .join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
            .where(Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED,
                   ClassSession.session_date >= from_date, ClassSession.session_date <= to_date,
                   ClassSession.status != ClassSessionStatus.CANCELLED, CourseOffering.attendance_locked_at.is_(None))
        )).all()
        now = datetime.now(timezone.utc)
        marked = 0
        for cs, _off in rows:
            existing = await self.repo.get_for_session_and_student(cs.id, student_id)
            if existing is not None:
                if existing.status == AttendanceStatus.PRESENT:
                    continue
                existing.status, existing.marked_by, existing.marked_at = AttendanceStatus.LEAVE, actor_id, now
            else:
                await self.repo.create(AttendanceRecord(class_session_id=cs.id, student_id=student_id, status=AttendanceStatus.LEAVE,
                                                        marked_by=actor_id, marked_at=now))
            marked += 1
        await self.audit.log(actor_user_id=actor_id, action="attendance.leave_granted", entity_type="student", entity_id=student_id,
                             metadata={"from": str(from_date), "to": str(to_date), "reason": reason.strip(), "classes": marked})
        return {"classes_marked_leave": marked}

    async def compute_percentages_for_offering(self, course_offering_id: uuid.UUID) -> dict[uuid.UUID, dict]:
        """Bulk form of compute_percentage — one call for every
        currently-enrolled student's roster row, instead of the caller
        (e.g. the roster table in the teacher's UI) making one HTTP
        request per student."""
        enrolled_ids = await self.repo.list_enrolled_student_ids_for_offering(course_offering_id)
        return {student_id: await self.compute_percentage(student_id, course_offering_id) for student_id in enrolled_ids}

    async def list_overdue_unmarked_sessions(self, course_offering_id: uuid.UUID | None = None) -> list[ClassSession]:
        """Admin's 'needs attention' view — every session whose
        settings.attendance_marking_window_minutes has closed with no
        attendance ever marked. Admin resolves these via mark_session
        with actor_is_admin=True and a late_note."""
        cutoff = institute_now() - timedelta(minutes=settings.attendance_marking_window_minutes)
        return await self.repo.list_overdue_unmarked_sessions(cutoff, course_offering_id)

    async def set_topic_completed(self, class_session_id: uuid.UUID, *, completed: bool, actor_id: uuid.UUID) -> ClassSession:
        """Marked by the teacher alongside attendance (or separately) —
        feeds CourseProgressService's on-time/delayed computation."""
        class_session, _offering = await self._get_session_and_offering(class_session_id)
        class_session.topic_completed = completed
        await self.audit.log(
            actor_user_id=actor_id, action="class_session.topic_completed_set",
            entity_type="class_session", entity_id=class_session_id, after={"topic_completed": completed},
        )
        return class_session

    async def early_lock_reason(self, offering, future_sessions: int) -> str | None:
        """Why locking now would be EARLY (None = the term is over for this course)."""
        if future_sessions > 0:
            return f"{future_sessions} class(es) of this course are still to come"
        if offering.term_end_date is not None and institute_now().date() < offering.term_end_date:
            return f"the term only ends on {offering.term_end_date:%d %b %Y}"
        return None

    async def lock_attendance(self, course_offering_id: uuid.UUID, *, actor_id: uuid.UUID,
                              early_reason: str | None = None, actor_is_admin: bool = True) -> dict:
        """The deliberate, one-way admin action that finalizes an
        offering's attendance for the term (ATTENDANCE_LOCK, admin
        only). Stops all further marking, then evaluates every
        currently-ENROLLED student's final % against the institute's
        two thresholds (app/core/config.py) — worse outcome wins:

        1. % <= attendance_drop_threshold_percent -> auto-withdraw
           (same effect as EnrollmentService.withdraw)
        2. else if % <= attendance_fine_threshold_percent -> auto-issue
           a Fine (source=ATTENDANCE, no approval step) for
           attendance_fine_amount, and immediately block the student's
           entire login (user.is_active=False) until an admin manually
           reactivates it — which itself requires the fine to be
           PAID/WAIVED first (see StudentService.reactivate_after_attendance_fine).

        The 75%-eligibility rule (blocks a PASSED outcome) is NOT
        applied here — that's checked live at grade-computation time in
        EnrollmentService.recompute_outcome, since it depends on
        whatever the student's attendance is AT THAT MOMENT, not
        specifically at lock time."""
        offering = await self.offering_repo.get_by_id(course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        # Lock the offering row: two people pressing "Lock" at once must not both fine and drop (audit ACAD-02).
        from sqlalchemy import select as _select

        from app.academics.models import CourseOffering

        offering = (await self.session.execute(_select(CourseOffering).where(CourseOffering.id == offering.id).with_for_update()
                                               .execution_options(populate_existing=True))).scalar_one()
        if offering.attendance_locked_at is not None:
            raise BusinessRuleViolationError("Attendance for this offering is already locked.")

        # Locking freezes every student's percentage and fires fines/drops from
        # it. If classes were held but never recorded, those numbers would be
        # wrong -- so every overdue class must first be marked (admin late-
        # marking with a note) or cancelled (holiday / teacher absent).
        unmarked = await self.list_overdue_unmarked_sessions(course_offering_id)
        if unmarked:
            raise BusinessRuleViolationError(
                f"{len(unmarked)} class(es) of this offering have no attendance recorded. Mark each one "
                "(Needs Attention list) or cancel it, then lock again."
            )

        # Locking drops and fines students from their FINAL percentage, so it
        # must not happen mid-term. An admin may lock early (e.g. term cut short)
        # only with a written reason, which is kept in the audit log.
        early = await self.early_lock_reason(offering, await self.repo.count_future_sessions(course_offering_id, institute_now()))
        if early and settings.block_early_attendance_lock:
            reason = (early_reason or "").strip()
            if not reason:
                raise BusinessRuleViolationError(
                    f"Too early to lock: {early}. Attendance is locked once the term is over, because locking drops "
                    "and fines students on their final percentage. Cancel the remaining classes if the term ended early, "
                    "or (administrator only) lock early with a written reason.",
                    details={"early": early, "override_allowed": actor_is_admin},
                )
            if not actor_is_admin:
                raise BusinessRuleViolationError(f"Too early to lock: {early}. Only an administrator can lock early.")
            if len(reason) < 10:
                raise BusinessRuleViolationError("Give a reason of at least 10 characters for locking early.")
            await self.audit.log(actor_user_id=actor_id, action="attendance.locked_early", entity_type="course_offering",
                                 entity_id=offering.id, metadata={"why_early": early, "reason": reason})

        offering.attendance_locked_at = datetime.now(timezone.utc)

        from app.attendance.shortfall import ShortfallService, graded

        if graded():
            # Fines by band, per subject (app/attendance/shortfall.py): the same check as the before-exams run, so a
            # student charged then is not charged again; anyone below the lowest band waits for an administrator's decision.
            ran = await ShortfallService(self.session).run(offering_id=course_offering_id, actor_id=actor_id, dry_run=False)
            enrolled = len(await self.repo.list_enrolled_student_ids_for_offering(course_offering_id))
            out = {"enrolled_count": enrolled, "dropped_count": 0, "fined_count": ran["new_fines"] + ran["top_ups"], "to_review_count": ran["to_review"]}
            await self.audit.log(actor_user_id=actor_id, action="attendance.locked", entity_type="course_offering", entity_id=course_offering_id,
                                 after={**out, "fines_amount": ran["amount"], "mode": "graded"})
            return out

        enrolled_ids = await self.repo.list_enrolled_student_ids_for_offering(course_offering_id)
        dropped_count = 0
        fined_count = 0
        for student_id in enrolled_ids:
            result = await self.compute_percentage(student_id, course_offering_id)
            percentage = result["percentage"]

            if percentage <= settings.attendance_drop_threshold_percent:
                enrollment = await self.enrollment_repo.get_by_student_and_offering(student_id, course_offering_id)
                if enrollment is not None and enrollment.status == EnrollmentStatus.ENROLLED:
                    from app.academics.service import EnrollmentService

                    await EnrollmentService(self.session).withdraw(enrollment.id, student_id=None, actor_id=actor_id, reason="ATTENDANCE")
                    dropped_count += 1
                    await self._notify_student(
                        student_id,
                        title="Dropped from a course — attendance too low",
                        message=f"Your attendance ({percentage}%) was at or below the institute's drop threshold "
                        f"({settings.attendance_drop_threshold_percent}%). You have been withdrawn from this course.",
                    )
            elif percentage <= settings.attendance_fine_threshold_percent:
                if await self._issue_attendance_fine_and_block(student_id, percentage, actor_id=actor_id, offering=offering):
                    fined_count += 1

        await self.audit.log(
            actor_user_id=actor_id,
            action="attendance.locked",
            entity_type="course_offering",
            entity_id=course_offering_id,
            after={"enrolled_count": len(enrolled_ids), "dropped_count": dropped_count, "fined_count": fined_count},
        )
        return {"enrolled_count": len(enrolled_ids), "dropped_count": dropped_count, "fined_count": fined_count}

    async def _issue_attendance_fine_and_block(self, student_id: uuid.UUID, percentage: float, *, actor_id: uuid.UUID, offering=None) -> bool:
        """One attendance fine per student per term, however many courses are short (owner decision 8). A second short
        course in the same term is added to the existing fine's description; no second fine. Returns True if a fine was issued."""
        from sqlalchemy import select as _select

        from app.academics.models import Course, CourseOffering
        from app.fines.models import Fine, FinePayerType, FineSource, FineStatus
        from app.fines.service import FineService

        course = await self.session.get(Course, offering.course_id) if offering is not None else None
        label = course.code if course is not None else "a course"
        if offering is not None:
            earlier = (await self.session.execute(
                _select(Fine).join(CourseOffering, CourseOffering.id == Fine.related_entity_id)
                .where(Fine.payer_type == FinePayerType.STUDENT, Fine.payer_id == student_id, Fine.source == FineSource.ATTENDANCE,
                       Fine.related_entity_type == "course_offering", CourseOffering.term_label == offering.term_label,
                       Fine.status.not_in((FineStatus.REJECTED,)))
                .with_for_update(of=Fine))).scalars().first()
            if earlier is not None:
                if earlier.status == FineStatus.PENDING and label not in (earlier.reason or ""):
                    earlier.reason = f"{earlier.reason}; also {label} ({percentage}%)"[:500]
                await self.audit.log(actor_user_id=actor_id, action="attendance.fine_already_issued_this_term", entity_type="fine", entity_id=earlier.id,
                                     metadata={"student_id": str(student_id), "course": label, "percentage": percentage, "term": offering.term_label})
                return False
        await FineService(self.session).issue(
            payer_type=FinePayerType.STUDENT,
            payer_id=student_id,
            amount=settings.attendance_fine_amount,
            reason=(f"Attendance below {settings.attendance_fine_threshold_percent}% in {offering.term_label}: {label} ({percentage}%)" if offering is not None
                    else f"Attendance below {settings.attendance_fine_threshold_percent}% (actual: {percentage}%)"),
            source=FineSource.ATTENDANCE,
            actor_id=actor_id,
            related_entity_type="course_offering" if offering is not None else None,
            related_entity_id=offering.id if offering is not None else None,
        )
        from app.sms.service import SmsService

        stu = await self.session.get(Student, student_id)
        await SmsService(self.session).queue_for_student(
            student_id, event="ATTENDANCE_FINE",
            body=f"{settings.app_name}: attendance {percentage:g}% (minimum {settings.attendance_eligibility_threshold_percent:g}%). "
                 f"Fine Rs. {settings.attendance_fine_amount:,.0f} issued; registration and result card on hold until paid.",
            guardian_body=f"{settings.app_name}: {stu.first_name if stu else 'Your child'}'s attendance is {percentage:g}%. "
                          f"A fine of Rs. {settings.attendance_fine_amount:,.0f} was issued. Please contact the institute.")

        if not settings.attendance_fine_blocks_login:
            # The login stays open; self-enrollment and the result card are held
            # instead (app/attendance/holds.py) until the fine is paid or waived.
            await self._notify_student(
                student_id, title="Attendance fine issued",
                message=f"Your attendance ({percentage}%) was at or below {settings.attendance_fine_threshold_percent}%. "
                        f"A fine of Rs. {settings.attendance_fine_amount:,.0f} was issued. Course registration and your "
                        "result card are on hold until it is paid.",
            )
            return True

        student = await self.session.get(Student, student_id)
        if student is not None and student.user_id is not None:
            user = await self.session.get(User, student.user_id)
            if user is not None:
                # Deliberately NOT student.status (SUSPENDED etc.) — see
                # the module docstring's reasoning: this is an
                # independent, fine-driven lock, not an academic status
                # change, and has its own separate unlock rule
                # (StudentService.reactivate_after_attendance_fine).
                user.is_active = False
                await self.audit.log(
                    actor_user_id=actor_id,
                    action="student.portal_blocked_attendance_fine",
                    entity_type="student",
                    entity_id=student.id,
                    after={"reason": "unpaid attendance fine", "attendance_percentage": percentage},
                )
        return True

    async def _notify_student(self, student_id: uuid.UUID, *, title: str, message: str) -> None:
        student = await self.session.get(Student, student_id)
        if student is not None and student.user_id is not None:
            await create_notification(
                self.session, user_id=student.user_id, category=NotificationCategory.GENERAL,
                title=title, message=message, link="/dashboard/student",
            )

    async def lock_preview(self, course_offering_id: uuid.UUID) -> dict:
        """Read-only: exactly what lock_attendance would do right now."""
        offering = await self.offering_repo.get_by_id(course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        unmarked = await self.list_overdue_unmarked_sessions(course_offering_id)
        future = await self.repo.count_future_sessions(course_offering_id, institute_now())
        enrolled_ids = await self.repo.list_enrolled_student_ids_for_offering(course_offering_id)
        would_drop = would_fine = below_eligibility = would_review = 0
        from app.attendance.shortfall import ShortfallService, graded

        if graded():
            pre = await ShortfallService(self.session).run(offering_id=course_offering_id, actor_id=None, dry_run=True)
            would_fine, would_review = pre["new_fines"] + pre["top_ups"], pre["to_review"]
            below_eligibility = len(pre["items"])
            enrolled_ids = []                                               # counted above
        for student_id in enrolled_ids:
            pct = (await self.compute_percentage(student_id, course_offering_id))["percentage"]
            if pct < settings.attendance_eligibility_threshold_percent:
                below_eligibility += 1
            if pct <= settings.attendance_drop_threshold_percent:
                would_drop += 1
            elif pct <= settings.attendance_fine_threshold_percent:
                would_fine += 1
        early = await self.early_lock_reason(offering, future) if settings.block_early_attendance_lock else None
        return {
            "early_reason": early, "term_end_date": offering.term_end_date,
            "course_offering_id": course_offering_id, "already_locked": offering.attendance_locked_at is not None,
            "enrolled_count": len(await self.repo.list_enrolled_student_ids_for_offering(course_offering_id)), "unmarked_overdue_sessions": len(unmarked),
            "future_sessions_remaining": future, "would_drop": would_drop, "would_fine": would_fine, "would_review": would_review,
            "graded_fines": graded(),
            "below_eligibility": below_eligibility, "can_lock": not unmarked and offering.attendance_locked_at is None and early is None,
            "eligibility_threshold": settings.attendance_eligibility_threshold_percent,
            "fine_threshold": settings.attendance_fine_threshold_percent,
            "drop_threshold": settings.attendance_drop_threshold_percent, "fine_amount": settings.attendance_fine_amount,
        }

    # ------------------------------------------------------------------
    # Correction requests (teacher -> admin) for classes outside the window
    # ------------------------------------------------------------------
    async def create_correction_request(
        self, class_session_id: uuid.UUID, *, reason: str, present_student_ids: list[uuid.UUID],
        actor_id: uuid.UUID,
    ) -> AttendanceCorrectionRequest:
        class_session, offering = await self._get_session_and_offering(class_session_id)
        if offering.attendance_locked_at is not None:
            raise BusinessRuleViolationError("Attendance for this offering is locked; it can no longer be corrected.")
        if class_session.status == ClassSessionStatus.CANCELLED:
            raise BusinessRuleViolationError("This class was cancelled.")
        if institute_now() < datetime.combine(class_session.session_date, class_session.start_time):
            raise BusinessRuleViolationError("This class hasn't started yet.")
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise BusinessRuleViolationError("Please give a reason (at least a few words).")
        pending = (
            await self.session.execute(
                select(AttendanceCorrectionRequest).where(
                    AttendanceCorrectionRequest.class_session_id == class_session_id,
                    AttendanceCorrectionRequest.status == "PENDING",
                )
            )
        ).scalars().first()
        if pending is not None:
            raise BusinessRuleViolationError("A correction request for this class is already waiting for the admin.")
        enrolled = set(await self.repo.list_enrolled_student_ids_for_offering(offering.id))
        if set(present_student_ids) - enrolled:
            raise BusinessRuleViolationError("One or more students are not currently enrolled in this offering.")
        request = AttendanceCorrectionRequest(
            class_session_id=class_session_id, requested_by=actor_id, reason=reason,
            present_student_ids=[str(x) for x in present_student_ids],
            status="PENDING",
        )
        self.session.add(request)
        await self.session.flush()
        await self.audit.log(
            actor_user_id=actor_id, action="attendance.correction_requested", entity_type="class_session",
            entity_id=class_session_id, after={"request_id": str(request.id), "reason": reason},
        )
        return request

    async def decide_correction_request(
        self, request_id: uuid.UUID, *, approve: bool, note: str | None, actor_id: uuid.UUID
    ) -> AttendanceCorrectionRequest:
        request = await self.session.get(AttendanceCorrectionRequest, request_id)
        if request is None:
            raise ResourceNotFoundError("Correction request not found.")
        if request.status != "PENDING":
            raise BusinessRuleViolationError(f"This request was already {request.status.lower()}.")
        if approve:
            await self.mark_session(
                request.class_session_id,
                present_student_ids=[uuid.UUID(x) for x in request.present_student_ids],
                actor_id=actor_id, actor_is_admin=True,
                late_note=f"Approved teacher request: {request.reason}",
            )
        request.status = "APPROVED" if approve else "REJECTED"
        request.decided_by = actor_id
        request.decided_at = datetime.now(timezone.utc)
        request.decision_note = (note or "").strip() or None
        await self.audit.log(
            actor_user_id=actor_id, action="attendance.correction_approved" if approve else "attendance.correction_rejected",
            entity_type="class_session", entity_id=request.class_session_id,
            after={"request_id": str(request.id)}, metadata={"note": request.decision_note} if request.decision_note else None,
        )
        if request.requested_by is not None:
            await create_notification(
                self.session, user_id=request.requested_by, category=NotificationCategory.GENERAL,
                title="Attendance correction " + ("approved" if approve else "rejected"),
                message=(request.decision_note or ("Your correction was applied." if approve else "Your request was rejected.")),
                link="/dashboard/faculty/attendance",
            )
        return request

    async def list_correction_requests(
        self, *, status: str | None = None, requested_by: uuid.UUID | None = None
    ) -> list[AttendanceCorrectionRequest]:
        stmt = select(AttendanceCorrectionRequest).order_by(AttendanceCorrectionRequest.created_at.desc())
        if status:
            stmt = stmt.where(AttendanceCorrectionRequest.status == status)
        if requested_by is not None:
            stmt = stmt.where(AttendanceCorrectionRequest.requested_by == requested_by)
        return list((await self.session.execute(stmt.limit(200))).scalars())