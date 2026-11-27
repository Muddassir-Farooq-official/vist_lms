"""A student's standing: what a SUSPENDED or expelled (DROPPED) student may not have done for them while that lasts.

Owner decisions (1.64.0):
  * Suspended: kept on attendance sheets, greyed as "Suspended" and locked; nobody marks them, and those classes do not
    count for or against their attendance. No admit card, left out of seating plans, marks entry locked, result card and
    transcript held. Lifting the suspension undoes every lock at once (nothing is deleted; the locks are checks).
  * Expelled (Dropped): withdrawn from every course still running (StudentService.change_status already does this); the
    same holds as a suspension apply to anything that is still asked for, except the leaving certificate.
  * Both: the parent portal is view-only, no new library books, no certificates or letters (expelled: only the leaving
    certificate), no new hostel or transport place, no assignment submissions.
Past records (attendance, marks, results, fees) are never changed by any of this.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleViolationError
from app.students.models import Student, StudentStatus

LOCKED = (StudentStatus.SUSPENDED, StudentStatus.DROPPED)
WORD = {StudentStatus.SUSPENDED: "suspended", StudentStatus.DROPPED: "expelled or has left"}


def status_of(student) -> StudentStatus | None:
    s = getattr(student, "status", None)
    if s is None:
        return None
    return s if isinstance(s, StudentStatus) else StudentStatus(s)


def is_locked(student) -> bool:
    return status_of(student) in LOCKED


async def locked_ids(session: AsyncSession, student_ids: Iterable[uuid.UUID], *, only=LOCKED) -> dict[uuid.UUID, StudentStatus]:
    """{student_id: status} for those of these students who are suspended or expelled."""
    ids = list({i for i in student_ids if i is not None})
    if not ids:
        return {}
    rows = (await session.execute(select(Student.id, Student.status).where(Student.id.in_(ids), Student.status.in_(list(only))))).all()
    return {r[0]: r[1] for r in rows}


def refusal(student, what: str) -> str:
    name = f"{student.first_name} {student.last_name}".strip() or "This student"
    return f"{name} is {WORD.get(status_of(student), 'not active')}: {what} is on hold until the suspension is lifted." \
        if status_of(student) == StudentStatus.SUSPENDED else f"{name} is {WORD.get(status_of(student), 'not active')}: {what} is not possible."


async def ensure_not_locked(session: AsyncSession, student_or_id, what: str, *, allow=()) -> None:
    """Raise BusinessRuleViolationError when the student is suspended or expelled (statuses in `allow` pass)."""
    student = student_or_id if isinstance(student_or_id, Student) else await session.get(Student, student_or_id)
    if student is not None and is_locked(student) and status_of(student) not in allow:
        raise BusinessRuleViolationError(refusal(student, what))


async def release_held_results(session: AsyncSession, student_id: uuid.UUID, *, actor_id: uuid.UUID) -> int:
    """The suspension is lifted: work out the results that were held while it lasted (courses whose result sheet was published
    meanwhile, and the student has every mark). Courses with marks still missing wait for the office to enter them."""
    from app.academics.grading_service import GradingService
    from app.academics.models import Enrollment, EnrollmentStatus, ResultSheet, ResultSheetStatus

    rows = (await session.execute(select(Enrollment).join(ResultSheet, ResultSheet.course_offering_id == Enrollment.course_offering_id).where(
        Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED, Enrollment.outcome.is_(None),
        ResultSheet.status == ResultSheetStatus.PUBLISHED))).scalars().all()
    grading = GradingService(session)
    done = 0
    for e in rows:
        await grading._maybe_recompute_outcome(e, actor_id=actor_id)
        done += e.outcome is not None
    return done
