"""Course prerequisites: "to take B you must have passed A".

Checked when a student is enrolled in a course for the first time (a retake already met it). A pass counts once its
result sheet is published (a supplementary pass counts too). An administrator can still enrol a student who has not
met it, with a written reason (EnrollmentService.enroll(override_reason=...)), kept in the activity log.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError


async def required_for(session: AsyncSession, course_id: uuid.UUID) -> list:
    from app.academics.models import Course, CoursePrerequisite

    return list((await session.execute(select(Course).join(CoursePrerequisite, CoursePrerequisite.requires_course_id == Course.id)
                                       .where(CoursePrerequisite.course_id == course_id).order_by(Course.code))).scalars())


async def passed_course_ids(session: AsyncSession, student_id: uuid.UUID) -> set:
    """Courses this student has passed (published results)."""
    from app.academics.models import CourseOffering, Enrollment, EnrollmentResult, ResultSheet, ResultSheetStatus

    return set((await session.execute(
        select(CourseOffering.course_id).join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
        .join(EnrollmentResult, EnrollmentResult.enrollment_id == Enrollment.id)
        .join(ResultSheet, ResultSheet.course_offering_id == CourseOffering.id)
        .where(Enrollment.student_id == student_id, EnrollmentResult.outcome == "PASSED", ResultSheet.status == ResultSheetStatus.PUBLISHED)
    )).scalars())


async def missing_for(session: AsyncSession, student_id: uuid.UUID, course_id: uuid.UUID) -> list:
    """The prerequisite courses this student has not passed yet (empty = may enrol)."""
    needed = await required_for(session, course_id)
    if not needed:
        return []
    done = await passed_course_ids(session, student_id)
    return [c for c in needed if c.id not in done]


class PrerequisiteService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def listing(self, course_id: uuid.UUID) -> list[dict]:
        return [{"id": c.id, "code": c.code, "title": c.title} for c in await required_for(self.session, course_id)]

    async def all_pairs(self) -> list[dict]:
        from app.academics.models import CoursePrerequisite

        return [{"course_id": p.course_id, "requires_course_id": p.requires_course_id}
                for p in (await self.session.execute(select(CoursePrerequisite))).scalars()]

    async def replace(self, course_id: uuid.UUID, requires: list[uuid.UUID], *, actor_id: uuid.UUID) -> list[dict]:
        from app.academics.models import Course, CoursePrerequisite

        course = await self.session.get(Course, course_id)
        if course is None:
            raise ResourceNotFoundError("Course not found.")
        requires = list(dict.fromkeys(requires))
        if course_id in requires:
            raise BusinessRuleViolationError("A course cannot be its own prerequisite.")
        if len(requires) > 10:
            raise BusinessRuleViolationError("At most 10 prerequisites for one course.")
        found = {c.id: c for c in (await self.session.execute(select(Course).where(Course.id.in_(requires or [uuid.uuid4()])))).scalars()}
        if len(found) != len(requires):
            raise BusinessRuleViolationError("One of the chosen courses does not exist.")
        # no circle: none of the new prerequisites may (directly or through others) require this course
        graph: dict = {}
        for p in (await self.session.execute(select(CoursePrerequisite).where(CoursePrerequisite.course_id != course_id))).scalars():
            graph.setdefault(p.course_id, set()).add(p.requires_course_id)
        for start in requires:
            seen, stack = set(), [start]
            while stack:
                cur = stack.pop()
                if cur == course_id:
                    raise BusinessRuleViolationError(f"{found[start].code} already requires {course.code} (directly or through another course), "
                                                     "so it cannot also be its prerequisite.")
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(graph.get(cur, ()))
        before = [c["code"] for c in await self.listing(course_id)]
        await self.session.execute(delete(CoursePrerequisite).where(CoursePrerequisite.course_id == course_id))
        now = datetime.now(timezone.utc)
        for rid in requires:
            self.session.add(CoursePrerequisite(course_id=course_id, requires_course_id=rid, created_by=actor_id, created_at=now))
        await self.session.flush()
        after = [found[r].code for r in requires]
        if sorted(before) != sorted(after):
            await AuditService(self.session).log(actor_user_id=actor_id, action="course.prerequisites_set", entity_type="course", entity_id=course_id,
                                                 before={"requires": before}, after={"requires": sorted(after)})
        return await self.listing(course_id)
