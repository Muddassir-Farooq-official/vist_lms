from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus
from app.academics.repository import attending_filter
from app.assignments import drive
from app.assignments.models import Assignment, AssignmentSubmission, SubmissionStatus
from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import AppError, AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.employees.models import Employee
from app.notifications.models import Notification, NotificationCategory
from app.students.models import Student

STUDENT_PAGE = "/dashboard/student/assignments"
RANK = {"SUBMITTED": 0, "NOT_SUBMITTED": 1, "REJECTED": 2, "ACCEPTED": 3}


class DriveLinkPrivateError(AppError):
    """The link asks visitors to sign in. The student is warned and may submit it anyway."""

    code = "DRIVE_LINK_PRIVATE"
    status_code = 422


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(settings.institute_timezone))


class AssignmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ who is asking
    async def _employee(self, user_id: uuid.UUID) -> Employee:
        employee = (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalars().first()
        if employee is None:
            raise AuthorizationError("This account is not linked to an employee record.")
        return employee

    async def _student(self, user_id: uuid.UUID) -> Student:
        student = (await self.session.execute(select(Student).where(Student.user_id == user_id))).scalars().first()
        if student is None:
            raise AuthorizationError("This account is not linked to a student record.")
        return student

    async def _assignment(self, assignment_id: uuid.UUID) -> Assignment:
        assignment = await self.session.get(Assignment, assignment_id)
        if assignment is None:
            raise ResourceNotFoundError("Assignment not found.")
        return assignment

    async def _owned(self, assignment_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Assignment, CourseOffering]:
        """The assignment, but only for the teacher of that class."""
        assignment = await self._assignment(assignment_id)
        offering = await self.session.get(CourseOffering, assignment.course_offering_id)
        employee = await self._employee(user_id)
        if offering is None or offering.assigned_teacher_id != employee.id:
            raise AuthorizationError("This assignment belongs to another teacher's class.")
        return assignment, offering

    async def _context(self, offering_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
        """course code/title, section, term and teacher name for each offering."""
        if not offering_ids:
            return {}
        rows = (await self.session.execute(
            select(CourseOffering, Course, Employee).join(Course, Course.id == CourseOffering.course_id)
            .outerjoin(Employee, Employee.id == CourseOffering.assigned_teacher_id).where(CourseOffering.id.in_(offering_ids))
        )).all()
        return {
            o.id: {"course_code": c.code, "course_title": c.title, "section_label": o.section_label, "term_label": o.term_label,
                   "teacher_name": f"{e.first_name} {e.last_name}" if e else None}
            for o, c, e in rows
        }

    def _base(self, a: Assignment, ctx: dict) -> dict:
        return {"id": a.id, "course_offering_id": a.course_offering_id, "course_code": ctx["course_code"], "course_title": ctx["course_title"],
                "section_label": ctx["section_label"], "term_label": ctx["term_label"], "title": a.title, "instructions": a.instructions,
                "deadline": a.deadline, "created_at": a.created_at, "is_open": _now() < a.deadline}

    async def _enrolled_students(self, offering_id: uuid.UUID) -> list[Student]:
        stmt = (select(Student).join(Enrollment, Enrollment.student_id == Student.id).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                .where(Enrollment.course_offering_id == offering_id, attending_filter(institute_today()))
                .order_by(Student.roll_number, Student.first_name))
        return list((await self.session.execute(stmt)).scalars())

    def _notify(self, user_ids: list[uuid.UUID], title: str, message: str, assignment_id: uuid.UUID) -> None:
        link = f"{STUDENT_PAGE}?open={assignment_id}"
        self.session.add_all([
            Notification(user_id=uid, category=NotificationCategory.ASSIGNMENT, title=title[:200], message=message[:1000], link=link)
            for uid in user_ids
        ])

    # ------------------------------------------------------------------ teacher: set and manage assignments
    async def create(self, *, user_id: uuid.UUID, offering_id: uuid.UUID, title: str, instructions: str, deadline: datetime) -> Assignment:
        offering = await self.session.get(CourseOffering, offering_id)
        if offering is None:
            raise ResourceNotFoundError("Class not found.")
        employee = await self._employee(user_id)
        if offering.assigned_teacher_id != employee.id:
            raise AuthorizationError("You can only set assignments for classes you teach.")
        deadline = _aware(deadline)
        if deadline <= _now():
            raise BusinessRuleViolationError("The deadline must be in the future.")
        assignment = Assignment(course_offering_id=offering.id, title=title.strip(), instructions=instructions.strip(), deadline=deadline, created_by=user_id)
        self.session.add(assignment)
        await self.session.flush()
        await self.audit.log(actor_user_id=user_id, action="assignment.created", entity_type="assignment", entity_id=assignment.id)
        ctx = (await self._context([offering.id]))[offering.id]
        due = deadline.astimezone(ZoneInfo(settings.institute_timezone)).strftime("%a %d %b, %I:%M %p")
        self._notify([s.user_id for s in await self._enrolled_students(offering.id) if s.user_id],
                     f"New assignment: {assignment.title}", f"{ctx['course_code']} \u2014 due {due}. Submit your Google Drive link.", assignment.id)
        return assignment

    async def update(self, assignment_id: uuid.UUID, *, user_id: uuid.UUID, changes: dict) -> Assignment:
        assignment, _ = await self._owned(assignment_id, user_id)
        if changes.get("deadline") is not None:
            assignment.deadline = _aware(changes.pop("deadline"))      # later = reopens it; earlier (even past) = closes it
        for key in ("title", "instructions"):
            if changes.get(key) is not None:
                setattr(assignment, key, changes[key].strip())
        await self.audit.log(actor_user_id=user_id, action="assignment.updated", entity_type="assignment", entity_id=assignment.id)
        return assignment

    async def close_now(self, assignment_id: uuid.UUID, *, user_id: uuid.UUID) -> Assignment:
        assignment, _ = await self._owned(assignment_id, user_id)
        assignment.deadline = _now()
        await self.audit.log(actor_user_id=user_id, action="assignment.closed", entity_type="assignment", entity_id=assignment.id)
        return assignment

    async def delete(self, assignment_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        assignment, _ = await self._owned(assignment_id, user_id)
        count = (await self.session.execute(select(func.count()).select_from(AssignmentSubmission).where(AssignmentSubmission.assignment_id == assignment.id))).scalar_one()
        if count:
            raise BusinessRuleViolationError("Students have already submitted work for this assignment, so it cannot be deleted. Close it instead.")
        await self.session.delete(assignment)
        await self.audit.log(actor_user_id=user_id, action="assignment.deleted", entity_type="assignment", entity_id=assignment_id)

    async def teaching_list(self, *, user_id: uuid.UUID, offering_id: uuid.UUID | None = None) -> list[dict]:
        employee = await self._employee(user_id)
        stmt = (select(Assignment).join(CourseOffering, CourseOffering.id == Assignment.course_offering_id)
                .where(CourseOffering.assigned_teacher_id == employee.id).order_by(Assignment.deadline.desc()))
        if offering_id:
            stmt = stmt.where(Assignment.course_offering_id == offering_id)
        assignments = list((await self.session.execute(stmt)).scalars())
        if not assignments:
            return []
        ctx = await self._context(list({a.course_offering_id for a in assignments}))
        enrolled = {oid: n for oid, n in (await self.session.execute(
            select(Enrollment.course_offering_id, func.count()).where(Enrollment.course_offering_id.in_(list(ctx)), Enrollment.status == EnrollmentStatus.ENROLLED)
            .group_by(Enrollment.course_offering_id))).all()}
        # only students who are still in the class count
        counts: dict[uuid.UUID, dict] = {}
        rows = (await self.session.execute(
            select(AssignmentSubmission.assignment_id, AssignmentSubmission.status, func.count())
            .join(Assignment, Assignment.id == AssignmentSubmission.assignment_id)
            .join(Enrollment, (Enrollment.student_id == AssignmentSubmission.student_id) & (Enrollment.course_offering_id == Assignment.course_offering_id)
                  & (Enrollment.status == EnrollmentStatus.ENROLLED))
            .where(AssignmentSubmission.assignment_id.in_([a.id for a in assignments])).group_by(AssignmentSubmission.assignment_id, AssignmentSubmission.status)
        )).all()
        for aid, status, n in rows:
            counts.setdefault(aid, {})[status] = n
        out = []
        for a in assignments:
            c = counts.get(a.id, {})
            n_enrolled = enrolled.get(a.course_offering_id, 0)
            submitted = sum(c.values())
            out.append({**self._base(a, ctx[a.course_offering_id]), "enrolled": n_enrolled, "submitted": submitted,
                        "awaiting_review": c.get(SubmissionStatus.SUBMITTED, 0), "accepted": c.get(SubmissionStatus.ACCEPTED, 0),
                        "rejected": c.get(SubmissionStatus.REJECTED, 0), "not_submitted": max(n_enrolled - submitted, 0)})
        return out

    async def roster(self, assignment_id: uuid.UUID, *, user_id: uuid.UUID) -> list[dict]:
        assignment, offering = await self._owned(assignment_id, user_id)
        students = await self._enrolled_students(offering.id)
        subs = {s.student_id: s for s in (await self.session.execute(
            select(AssignmentSubmission).where(AssignmentSubmission.assignment_id == assignment.id))).scalars()}
        rows = []
        for st in students:
            sub = subs.get(st.id)
            rows.append({
                "student_id": st.id, "student_name": f"{st.first_name} {st.last_name}", "roll_number": st.roll_number,
                "state": sub.status.value if sub else "NOT_SUBMITTED", "submission_id": sub.id if sub else None,
                "drive_url": sub.drive_url if sub else None, "note": sub.note if sub else None,
                "submitted_at": (sub.resubmitted_at or sub.submitted_at) if sub else None,
                "resubmission_count": sub.resubmission_count if sub else 0, "feedback": sub.feedback if sub else None,
                "reviewed_at": sub.reviewed_at if sub else None,
            })
        rows.sort(key=lambda r: (RANK[r["state"]], r["student_name"]))
        return rows

    async def review(self, submission_id: uuid.UUID, *, user_id: uuid.UUID, decision: str, feedback: str | None) -> AssignmentSubmission:
        sub = await self.session.get(AssignmentSubmission, submission_id)
        if sub is None:
            raise ResourceNotFoundError("Submission not found.")
        assignment, offering = await self._owned(sub.assignment_id, user_id)
        sub.status = SubmissionStatus(decision)
        sub.feedback = (feedback or "").strip() or None
        sub.reviewed_at = _now()
        sub.reviewed_by = user_id
        await self.audit.log(actor_user_id=user_id, action=f"assignment.submission_{decision.lower()}", entity_type="assignment", entity_id=assignment.id)
        student = await self.session.get(Student, sub.student_id)
        if student and student.user_id:
            ctx = (await self._context([offering.id]))[offering.id]
            if decision == "ACCEPTED":
                title, tail = "Assignment accepted", (f" \u2014 {sub.feedback}" if sub.feedback else "")
            else:
                title, tail = "Assignment needs changes", f" \u2014 {sub.feedback}"
            self._notify([student.user_id], title, f"{ctx['course_code']}: {assignment.title}{tail}", assignment.id)
        return sub

    # ------------------------------------------------------------------ student: see and submit
    async def _state(self, a: Assignment, sub: AssignmentSubmission | None) -> tuple[str, bool]:
        is_open = _now() < a.deadline
        if sub is None:
            return ("NOT_SUBMITTED" if is_open else "MISSED"), is_open
        return sub.status.value, is_open and sub.status != SubmissionStatus.ACCEPTED

    @staticmethod
    def _sub_out(sub: AssignmentSubmission | None) -> dict | None:
        if sub is None:
            return None
        return {"id": sub.id, "drive_url": sub.drive_url, "note": sub.note, "status": sub.status, "submitted_at": sub.submitted_at,
                "resubmitted_at": sub.resubmitted_at, "resubmission_count": sub.resubmission_count, "feedback": sub.feedback, "reviewed_at": sub.reviewed_at}

    async def student_list(self, *, user_id: uuid.UUID) -> list[dict]:
        student = await self._student(user_id)
        stmt = (select(Assignment).join(Enrollment, (Enrollment.course_offering_id == Assignment.course_offering_id))
                .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                .where(Enrollment.student_id == student.id, attending_filter(institute_today())))
        assignments = list((await self.session.execute(stmt)).scalars())
        if not assignments:
            return []
        ctx = await self._context(list({a.course_offering_id for a in assignments}))
        subs = {s.assignment_id: s for s in (await self.session.execute(
            select(AssignmentSubmission).where(AssignmentSubmission.student_id == student.id, AssignmentSubmission.assignment_id.in_([a.id for a in assignments])))).scalars()}
        out = []
        for a in assignments:
            sub = subs.get(a.id)
            state, can_submit = await self._state(a, sub)
            out.append({**self._base(a, ctx[a.course_offering_id]), "teacher_name": ctx[a.course_offering_id]["teacher_name"],
                        "state": state, "can_submit": can_submit, "submission": self._sub_out(sub)})
        # open ones first (soonest deadline first), then the closed ones (most recent first)
        out.sort(key=lambda r: (not r["is_open"], r["deadline"] if r["is_open"] else -r["deadline"].timestamp()))
        return out

    async def submit(self, assignment_id: uuid.UUID, *, user_id: uuid.UUID, drive_url: str, note: str | None, acknowledge_private: bool) -> dict:
        student = await self._student(user_id)
        assignment = await self._assignment(assignment_id)
        enrolled = (await self.session.execute(
            select(func.count()).select_from(Enrollment).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
            .where(Enrollment.student_id == student.id, Enrollment.course_offering_id == assignment.course_offering_id, attending_filter(institute_today())))).scalar_one()
        if not enrolled:
            raise ResourceNotFoundError("Assignment not found.")        # it simply is not there for people outside the class
        if _now() >= assignment.deadline:
            raise BusinessRuleViolationError("The deadline has passed, so submissions are closed.")
        sub = (await self.session.execute(select(AssignmentSubmission).where(
            AssignmentSubmission.assignment_id == assignment.id, AssignmentSubmission.student_id == student.id))).scalars().first()
        if sub is not None and sub.status == SubmissionStatus.ACCEPTED:
            raise BusinessRuleViolationError("Your teacher has already accepted this work, so it can no longer be changed.")
        url = drive.normalise(drive_url)
        if not acknowledge_private and await drive.check_public_access(url) == "private":
            raise DriveLinkPrivateError(
                "This link looks private: your teacher would have to ask for access. Open it in Google Drive, choose Share, set "
                "General access to \u201cAnyone with the link\u201d (Viewer), then paste the link again.")
        now = _now()
        note = (note or "").strip() or None
        if sub is None:
            sub = AssignmentSubmission(assignment_id=assignment.id, student_id=student.id, drive_url=url, note=note, status=SubmissionStatus.SUBMITTED, submitted_at=now)
            self.session.add(sub)
        else:                                               # replacing a link (before the deadline), including after a rejection
            sub.drive_url, sub.note, sub.status = url, note, SubmissionStatus.SUBMITTED
            sub.resubmitted_at, sub.resubmission_count = now, sub.resubmission_count + 1
            sub.reviewed_at = sub.reviewed_by = sub.feedback = None       # a fresh submission starts clean; the audit log keeps the history
        await self.session.flush()
        await self.audit.log(actor_user_id=user_id, action="assignment.submitted", entity_type="assignment", entity_id=assignment.id)
        ctx = (await self._context([assignment.course_offering_id]))[assignment.course_offering_id]
        state, can_submit = await self._state(assignment, sub)
        return {**self._base(assignment, ctx), "teacher_name": ctx["teacher_name"], "state": state, "can_submit": can_submit, "submission": self._sub_out(sub)}