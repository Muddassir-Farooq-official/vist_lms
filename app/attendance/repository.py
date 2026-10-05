from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, ClassSessionStatus, CourseOffering, Enrollment, EnrollmentStatus
from app.attendance.models import AttendanceRecord


class AttendanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, record_id: uuid.UUID) -> AttendanceRecord | None:
        return await self.session.get(AttendanceRecord, record_id)

    async def get_for_session_and_student(self, class_session_id: uuid.UUID, student_id: uuid.UUID) -> AttendanceRecord | None:
        stmt = select(AttendanceRecord).where(
            AttendanceRecord.class_session_id == class_session_id, AttendanceRecord.student_id == student_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_session(self, class_session_id: uuid.UUID) -> list[AttendanceRecord]:
        stmt = select(AttendanceRecord).where(AttendanceRecord.class_session_id == class_session_id)
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_student_in_offering(self, student_id: uuid.UUID, course_offering_id: uuid.UUID) -> list[AttendanceRecord]:
        """Every attendance record for this student across every
        session of this one offering — the raw data
        AttendanceService.compute_percentage sums over. Joined through
        ClassSession rather than duplicating course_offering_id onto
        AttendanceRecord itself — one source of truth for which
        offering a session belongs to."""
        stmt = (
            select(AttendanceRecord)
            .join(ClassSession, AttendanceRecord.class_session_id == ClassSession.id)
            .where(
                ClassSession.course_offering_id == course_offering_id,
                AttendanceRecord.student_id == student_id,
                # A class that was later CANCELLED never happened: it must not
                # count for or against anyone.
                ClassSession.status != ClassSessionStatus.CANCELLED,
            )
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_dated_for_student_in_offering(self, student_id: uuid.UUID, course_offering_id: uuid.UUID):
        """(session, record) pairs for a student's history view, oldest first."""
        stmt = (
            select(ClassSession, AttendanceRecord)
            .join(AttendanceRecord, AttendanceRecord.class_session_id == ClassSession.id)
            .where(
                ClassSession.course_offering_id == course_offering_id,
                AttendanceRecord.student_id == student_id,
                ClassSession.status != ClassSessionStatus.CANCELLED,
            )
            .order_by(ClassSession.session_date, ClassSession.start_time)
        )
        return list((await self.session.execute(stmt)).all())

    async def list_taken_sessions_for_offering(self, course_offering_id: uuid.UUID) -> list[ClassSession]:
        stmt = (
            select(ClassSession)
            .where(
                ClassSession.course_offering_id == course_offering_id,
                ClassSession.status == ClassSessionStatus.SCHEDULED,
                ClassSession.attendance_taken_at.is_not(None),
            )
            .order_by(ClassSession.session_date, ClassSession.start_time)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_offering(self, course_offering_id: uuid.UUID) -> list[AttendanceRecord]:
        stmt = (
            select(AttendanceRecord)
            .join(ClassSession, AttendanceRecord.class_session_id == ClassSession.id)
            .where(ClassSession.course_offering_id == course_offering_id, ClassSession.status != ClassSessionStatus.CANCELLED)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, record: AttendanceRecord) -> AttendanceRecord:
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_enrolled_student_ids_for_offering(self, course_offering_id: uuid.UUID) -> list[uuid.UUID]:
        """Only currently-ENROLLED students are on the roster attendance
        gets taken against — a WITHDRAWN student (including one
        auto-withdrawn by lock_attendance's own drop-threshold check)
        is not expected to keep showing up in future sessions."""
        from app.academics.models import CourseOffering
        from app.academics.repository import attending_filter
        from app.core.clock import institute_today

        # still attending = ENROLLED, or COMPLETED by a published result while the term is still running
        stmt = (select(Enrollment.student_id).join(CourseOffering, Enrollment.course_offering_id == CourseOffering.id)
                .where(Enrollment.course_offering_id == course_offering_id, attending_filter(institute_today())))
        return list((await self.session.execute(stmt)).scalars())

    async def list_overdue_unmarked_sessions(
        self, cutoff: datetime, course_offering_id: uuid.UUID | None = None
    ) -> list[ClassSession]:
        """Every SCHEDULED session whose marking window has already closed
        (session_date + start_time before cutoff, which the caller sets to
        now - attendance_marking_window_minutes) and whose attendance was
        never taken (attendance_taken_at IS NULL) -- surfaced to admin as
        'needs attention'. Uses the explicit taken-flag rather than "has any
        record", because a class with zero enrolled students can never have
        records and would otherwise stay flagged forever. Offerings whose
        attendance is already locked are finished business and are skipped."""
        stmt = (
            select(ClassSession)
            .join(CourseOffering, ClassSession.course_offering_id == CourseOffering.id)
            .where(
                ClassSession.status == ClassSessionStatus.SCHEDULED,
                (ClassSession.session_date + ClassSession.start_time) < cutoff,
                ClassSession.attendance_taken_at.is_(None),
                CourseOffering.attendance_locked_at.is_(None),
            )
            .order_by(ClassSession.session_date, ClassSession.start_time)
        )
        if course_offering_id is not None:
            stmt = stmt.where(ClassSession.course_offering_id == course_offering_id)
        return list((await self.session.execute(stmt)).scalars())

    async def count_future_sessions(self, course_offering_id: uuid.UUID, now_local: datetime) -> int:
        stmt = select(func.count()).select_from(ClassSession).where(
            ClassSession.course_offering_id == course_offering_id,
            ClassSession.status == ClassSessionStatus.SCHEDULED,
            (ClassSession.session_date + ClassSession.start_time) >= now_local,
        )
        return (await self.session.execute(stmt)).scalar_one()