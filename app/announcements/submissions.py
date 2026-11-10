"""Announcements written by teachers and staff, approved by the admin before anyone sees them.

    teacher / HOD / principal / any staff member writes it  ->  review PENDING (status stays DRAFT: invisible)
    admin approves (may edit it first)  ->  published; its audience is notified; the writer is told
    admin rejects with a reason  ->  REJECTED; the writer fixes it and sends it again (PENDING), or takes it back (CANCELLED)

It is issued in the writer's own name and job, taken from their account ("Ayesha Khan, Lecturer"), never typed in.
Who it can be for: all students, students of chosen programs and/or semesters, students of chosen course sections, teachers,
other staff. A teacher who is not a head of department or principal may pick only the sections they teach.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.announcements.models import Announcement, AnnouncementStatus
from app.announcements.service import AnnouncementService
from app.audit.service import AuditService
from app.auth.permissions import Permissions, Roles
from app.core.clock import institute_today
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

MINE_LINK = "/dashboard/my-announcements"
REVIEW_LINK = "/dashboard/admin/announcements"
EDITABLE = ("PENDING", "REJECTED")
REVIEW_TEXT = {"PENDING": "Waiting for approval", "APPROVED": "Approved and published", "REJECTED": "Not approved", "CANCELLED": "Taken back"}
WIDE = (Roles.ADMIN, Roles.PRINCIPAL, Roles.HOD)          # may pick any course section


class SubmissionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)
        self.base = AnnouncementService(session)

    # ------------------------------------------------------------------ who is writing
    async def writer(self, user_id: uuid.UUID, roles: list[str]) -> tuple[str, str]:
        """(name, job) shown as 'issued by'. Only staff with an employee record (or the principal / an HOD) may write."""
        from app.auth.models import Role, User, UserRole
        from app.employees.models import Employee, JobRole

        row = (await self.session.execute(select(Employee, JobRole).outerjoin(JobRole, JobRole.id == Employee.job_role_id)
                                          .where(Employee.user_id == user_id))).first()
        if row is not None:
            e, job = row
            return e.full_name, (job.title if job else None) or (e.department or "Staff")
        if not any(r in roles for r in (Roles.PRINCIPAL, Roles.HOD)) or Roles.STUDENT in roles:
            raise AuthorizationError("Only teachers and staff can send announcements.")
        user = await self.session.get(User, user_id)
        role = (await self.session.execute(select(Role.name).join(UserRole, UserRole.role_id == Role.id)
                                           .where(UserRole.user_id == user_id, Role.code.in_([Roles.PRINCIPAL, Roles.HOD])))).scalars().first()
        return (user.username if user else "Staff"), role or "Staff"

    async def targets(self, user_id: uuid.UUID, roles: list[str]) -> dict:
        """What the form offers: programs, semesters, and the course sections this person may pick."""
        from app.academics.models import Course, CourseOffering
        from app.employees.models import Employee
        from app.programs.models import Program

        programs = [{"id": p.id, "name": p.name, "code": getattr(p, "code", None), "semesters": getattr(p, "total_semesters", None) or 8}
                    for p in (await self.session.execute(select(Program).order_by(Program.name))).scalars()]
        today = institute_today()
        stmt = (select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id)
                .where((CourseOffering.term_end_date.is_(None)) | (CourseOffering.term_end_date >= today)))
        if not any(r in roles for r in WIDE):
            emp_id = (await self.session.execute(select(Employee.id).where(Employee.user_id == user_id))).scalar_one_or_none()
            stmt = stmt.where(CourseOffering.assigned_teacher_id == (emp_id or uuid.uuid4()))
        sections = [{"id": o.id, "label": f"{c.code} {o.section_label or ''} — {c.title}".replace("  ", " ").strip(), "term": o.term_label}
                    for o, c in (await self.session.execute(stmt.order_by(Course.code, CourseOffering.section_label))).all()]
        max_sem = max([p["semesters"] for p in programs] or [8])
        return {"programs": programs, "semesters": list(range(1, max_sem + 1)), "sections": sections,
                "sections_are_own": not any(r in roles for r in WIDE)}

    async def _check_audience(self, data: dict, user_id: uuid.UUID, roles: list[str]) -> None:
        if not (data.get("audience_students") or data.get("audience_teachers") or data.get("audience_staff")):
            raise ValidationAppError("Choose who it is for: students, teachers or other staff.")
        offering_ids = [str(o) for o in (data.get("offering_ids") or [])]
        if offering_ids:
            allowed = {str(s["id"]) for s in (await self.targets(user_id, roles))["sections"]}
            if not set(offering_ids) <= allowed:
                raise ValidationAppError("You can only choose the course sections you teach.")
        if any(int(x) < 1 or int(x) > 12 for x in (data.get("semesters") or [])):
            raise ValidationAppError("Semesters are numbered from 1.")

    @staticmethod
    def _apply(a: Announcement, data: dict) -> None:
        for key in ("title", "body", "category", "priority", "audience_students", "audience_teachers", "audience_staff", "expires_at"):
            if key in data:
                setattr(a, key, data[key])
        if data.get("audience_students"):
            a.offering_ids = [str(o) for o in (data.get("offering_ids") or [])] or None
            a.program_ids = None if a.offering_ids else ([str(p) for p in (data.get("program_ids") or [])] or None)
            a.semesters = None if a.offering_ids else (sorted({int(x) for x in (data.get("semesters") or [])}) or None)
        else:
            a.offering_ids = a.program_ids = a.semesters = None

    async def _tell_admins(self, a: Announcement, text: str) -> None:
        from app.approvals.service import _users_with_permission
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        for uid in await _users_with_permission(self.session, Permissions.ANNOUNCEMENT_MANAGE):
            if uid != a.submitted_by:
                await create_notification(self.session, user_id=uid, category=NotificationCategory.ANNOUNCEMENT, title="Announcement to approve",
                                          message=f"{a.issuer_name} ({a.issuer_designation}): {a.title}. {text}"[:1000], link=REVIEW_LINK)

    async def _tell_writer(self, a: Announcement, title: str, message: str) -> None:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        if a.submitted_by:
            await create_notification(self.session, user_id=a.submitted_by, category=NotificationCategory.ANNOUNCEMENT, title=title,
                                      message=message[:1000], link=MINE_LINK)

    # ------------------------------------------------------------------ the writer
    async def submit(self, data: dict, *, user_id: uuid.UUID, roles: list[str]) -> Announcement:
        name, job = await self.writer(user_id, roles)
        await self._check_audience(data, user_id, roles)
        now = datetime.now(timezone.utc)
        a = Announcement(title=data["title"], body=data["body"], issuer_id=None, issuer_name=name[:120], issuer_designation=job[:120],
                         status=AnnouncementStatus.DRAFT, created_by=user_id, review="PENDING", submitted_by=user_id, submitted_at=now)
        self._apply(a, data)
        self.session.add(a)
        await self.session.flush()
        await self.audit.log(actor_user_id=user_id, action="announcement.submitted", entity_type="announcement", entity_id=a.id)
        await self._tell_admins(a, "Approve or reject it on the Announcements page.")
        return a

    async def _mine(self, announcement_id: uuid.UUID, user_id: uuid.UUID) -> Announcement:
        a = await self.session.get(Announcement, announcement_id, with_for_update=True)
        if a is None or a.submitted_by != user_id:
            raise ResourceNotFoundError("Announcement not found.")
        return a

    async def edit(self, announcement_id: uuid.UUID, data: dict, *, user_id: uuid.UUID, roles: list[str]) -> Announcement:
        """Change it while it waits, or fix a rejected one: either way it goes (back) to the admin."""
        a = await self._mine(announcement_id, user_id)
        if a.review not in EDITABLE:
            raise BusinessRuleViolationError("This announcement can no longer be changed." if a.review == "APPROVED" else "It was taken back.")
        merged = {"audience_students": a.audience_students, "audience_teachers": a.audience_teachers, "audience_staff": a.audience_staff,
                  "program_ids": a.program_ids, "semesters": a.semesters, "offering_ids": a.offering_ids} | data
        await self._check_audience(merged, user_id, roles)
        self._apply(a, merged)
        was_rejected = a.review == "REJECTED"
        a.review, a.submitted_at = "PENDING", datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=user_id, action="announcement.resubmitted" if was_rejected else "announcement.edited_by_writer",
                             entity_type="announcement", entity_id=a.id)
        if was_rejected:
            await self._tell_admins(a, "Sent again after changes.")
        return a

    async def cancel(self, announcement_id: uuid.UUID, *, user_id: uuid.UUID) -> Announcement:
        a = await self._mine(announcement_id, user_id)
        if a.review not in EDITABLE:
            raise BusinessRuleViolationError("Only an announcement that is not published can be taken back.")
        a.review = "CANCELLED"
        await self.audit.log(actor_user_id=user_id, action="announcement.cancelled_by_writer", entity_type="announcement", entity_id=a.id)
        return a

    async def mine(self, user_id: uuid.UUID) -> list[Announcement]:
        return list((await self.session.execute(select(Announcement).where(Announcement.submitted_by == user_id)
                                                .order_by(Announcement.submitted_at.desc()))).scalars())

    # ------------------------------------------------------------------ the admin
    async def approve(self, announcement_id: uuid.UUID, *, actor_id: uuid.UUID) -> Announcement:
        a = await self.session.get(Announcement, announcement_id, with_for_update=True)
        if a is None or a.review != "PENDING":
            raise BusinessRuleViolationError("Only an announcement waiting for approval can be approved.")
        a.review, a.reviewed_by, a.reviewed_at, a.review_note = "APPROVED", actor_id, datetime.now(timezone.utc), None
        await self.base.publish(a.id, actor_id=actor_id)
        await self.audit.log(actor_user_id=actor_id, action="announcement.approved", entity_type="announcement", entity_id=a.id)
        await self._tell_writer(a, "Your announcement is published", f"“{a.title}” was approved and is now live.")
        return a

    async def reject(self, announcement_id: uuid.UUID, *, actor_id: uuid.UUID, note: str) -> Announcement:
        note = (note or "").strip()
        if len(note) < 4:
            raise ValidationAppError("Say what to change, so they can fix it and send it again.")
        a = await self.session.get(Announcement, announcement_id, with_for_update=True)
        if a is None or a.review != "PENDING":
            raise BusinessRuleViolationError("Only an announcement waiting for approval can be rejected.")
        a.review, a.reviewed_by, a.reviewed_at, a.review_note = "REJECTED", actor_id, datetime.now(timezone.utc), note[:500]
        await self.audit.log(actor_user_id=actor_id, action="announcement.rejected", entity_type="announcement", entity_id=a.id, metadata={"note": note})
        await self._tell_writer(a, "Announcement not approved", f"“{a.title}”: {note} You can change it and send it again.")
        return a


async def audience_text(session: AsyncSession, items: list[Announcement]) -> dict:
    """{id: 'BSCS, BBA · semester 3' / 'CS-201 A' / 'All students' (+ teachers / staff)} for display."""
    from app.academics.models import Course, CourseOffering
    from app.programs.models import Program

    pids = {uuid.UUID(p) for a in items for p in (a.program_ids or [])}
    oids = {uuid.UUID(o) for a in items for o in (a.offering_ids or [])}
    pnames = {p.id: (getattr(p, "code", None) or p.name) for p in (await session.execute(select(Program).where(Program.id.in_(pids)))).scalars()} if pids else {}
    onames = {o.id: f"{c.code} {o.section_label or ''}".strip() for o, c in (await session.execute(
        select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id).where(CourseOffering.id.in_(oids)))).all()} if oids else {}
    out = {}
    for a in items:
        parts = []
        if a.audience_students:
            if a.offering_ids:
                parts.append("Students of " + ", ".join(onames.get(uuid.UUID(o), "a section") for o in a.offering_ids))
            else:
                who = ", ".join(pnames.get(uuid.UUID(p), "?") for p in (a.program_ids or [])) or "All"
                sem = (" · semester " + ", ".join(str(x) for x in a.semesters)) if a.semesters else ""
                parts.append(f"{who} students{sem}" if who != "All" else f"All students{sem}")
        if a.audience_teachers:
            parts.append("Teachers")
        if a.audience_staff:
            parts.append("Staff")
        out[a.id] = " · ".join(parts)
    return out
