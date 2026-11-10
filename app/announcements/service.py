from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.announcements.models import (
    CATEGORY_LABELS,
    Announcement,
    AnnouncementAttachment,
    AnnouncementPriority,
    AnnouncementRead,
    AnnouncementStatus,
    IssuingAuthority,
)
from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.core.storage import MAX_ATTACHMENTS_PER_ANNOUNCEMENT, delete_file, save_attachment
from app.notifications.models import Notification, NotificationCategory
from app.programs.models import Program
from app.students.models import Student, StudentStatus

# Where each kind of person reads announcements, for the notification link.
PORTAL_FOR_ROLE = {
    Roles.STUDENT: "/dashboard/student/announcements", Roles.TEACHER: "/dashboard/faculty/announcements",
    Roles.LIBRARIAN: "/dashboard/library/announcements", Roles.ACCOUNTS_OFFICER: "/dashboard/accounts/announcements", Roles.SECURITY_GUARD: "/dashboard/gate/announcements", Roles.EXAM_CONTROLLER: "/dashboard/exam/announcements", Roles.HOSTEL_WARDEN: "/dashboard/warden",
    Roles.STAFF: "/dashboard/staff/announcements",
}


@dataclass
class Viewer:
    """What an announcement's audience is matched against."""

    user_id: uuid.UUID
    is_admin: bool
    student: bool
    teacher: bool
    staff: bool
    program_id: uuid.UUID | None = None
    semester: int | None = None
    offering_ids: frozenset = frozenset()          # course sections the student is enrolled in (as strings)


class AnnouncementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ authorities
    async def list_authorities(self, *, active_only: bool = False) -> list[IssuingAuthority]:
        stmt = select(IssuingAuthority).order_by(IssuingAuthority.is_active.desc(), IssuingAuthority.designation, IssuingAuthority.name)
        if active_only:
            stmt = stmt.where(IssuingAuthority.is_active.is_(True))
        return list((await self.session.execute(stmt)).scalars())

    async def create_authority(self, *, name: str, designation: str, actor_id: uuid.UUID) -> IssuingAuthority:
        authority = IssuingAuthority(name=name.strip(), designation=designation.strip(), is_active=True)
        self.session.add(authority)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="announcement.authority_created", entity_type="issuing_authority", entity_id=authority.id)
        return authority

    async def update_authority(self, authority_id: uuid.UUID, *, actor_id: uuid.UUID, **changes) -> IssuingAuthority:
        authority = await self.session.get(IssuingAuthority, authority_id)
        if authority is None:
            raise ResourceNotFoundError("Issuing authority not found.")
        for key, value in changes.items():
            if value is not None:
                setattr(authority, key, value.strip() if isinstance(value, str) else value)
        await self.audit.log(actor_user_id=actor_id, action="announcement.authority_updated", entity_type="issuing_authority", entity_id=authority.id)
        return authority

    # ------------------------------------------------------------------ announcements
    async def get(self, announcement_id: uuid.UUID) -> Announcement:
        announcement = await self.session.get(Announcement, announcement_id)
        if announcement is None:
            raise ResourceNotFoundError("Announcement not found.")
        return announcement

    async def _issuer(self, issuer_id: uuid.UUID) -> IssuingAuthority:
        authority = await self.session.get(IssuingAuthority, issuer_id)
        if authority is None or not authority.is_active:
            raise BusinessRuleViolationError("Choose an active issuing authority (the official this announcement is issued by).")
        return authority

    async def create(self, *, data: dict, actor_id: uuid.UUID) -> Announcement:
        publish = data.pop("publish", False)
        authority = await self._issuer(data.pop("issuer_id"))
        program_ids = [str(p) for p in (data.pop("program_ids", None) or [])] or None
        semesters = sorted({int(x) for x in (data.pop("semesters", None) or [])}) or None
        offering_ids = [str(o) for o in (data.pop("offering_ids", None) or [])] or None
        announcement = Announcement(
            **data, issuer_id=authority.id, issuer_name=authority.name, issuer_designation=authority.designation,
            program_ids=program_ids, semesters=semesters, offering_ids=offering_ids, status=AnnouncementStatus.DRAFT, created_by=actor_id,
        )
        self.session.add(announcement)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="announcement.created", entity_type="announcement", entity_id=announcement.id)
        if publish:
            await self.publish(announcement.id, actor_id=actor_id)
        return announcement

    async def update(self, announcement_id: uuid.UUID, *, changes: dict, actor_id: uuid.UUID) -> Announcement:
        announcement = await self.get(announcement_id)
        issuer_id = changes.pop("issuer_id", None)
        if issuer_id is not None and issuer_id != announcement.issuer_id:
            authority = await self._issuer(issuer_id)
            announcement.issuer_id, announcement.issuer_name, announcement.issuer_designation = authority.id, authority.name, authority.designation
        if changes.pop("clear_program_filter", False):
            announcement.program_ids = None
        elif changes.get("program_ids") is not None:
            announcement.program_ids = [str(p) for p in changes.pop("program_ids")] or None
        else:
            changes.pop("program_ids", None)
        if "semesters" in changes:
            announcement.semesters = sorted({int(x) for x in (changes.pop("semesters") or [])}) or None
        if "offering_ids" in changes:
            announcement.offering_ids = [str(o) for o in (changes.pop("offering_ids") or [])] or None
        if changes.pop("clear_expiry", False):
            announcement.expires_at = None
        for key, value in changes.items():
            if value is not None:
                setattr(announcement, key, value)
        if not (announcement.audience_students or announcement.audience_teachers or announcement.audience_staff):
            raise BusinessRuleViolationError("Choose who this announcement is for (students, teachers or other staff).")
        await self.audit.log(actor_user_id=actor_id, action="announcement.updated", entity_type="announcement", entity_id=announcement.id)
        return announcement

    async def publish(self, announcement_id: uuid.UUID, *, actor_id: uuid.UUID) -> Announcement:
        """Make it live and tell the people it is for (a bell notification each). Notified only the first time."""
        announcement = await self.get(announcement_id)
        first_time = announcement.published_at is None
        announcement.status = AnnouncementStatus.PUBLISHED
        announcement.published_by = actor_id
        if first_time:
            announcement.published_at = datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=actor_id, action="announcement.published", entity_type="announcement", entity_id=announcement.id)
        if first_time:
            await self._notify(announcement)
        return announcement

    async def archive(self, announcement_id: uuid.UUID, *, actor_id: uuid.UUID) -> Announcement:
        announcement = await self.get(announcement_id)
        announcement.status = AnnouncementStatus.ARCHIVED
        await self.audit.log(actor_user_id=actor_id, action="announcement.archived", entity_type="announcement", entity_id=announcement.id)
        return announcement

    async def delete(self, announcement_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        announcement = await self.get(announcement_id)
        for att in await self.attachments_of([announcement.id]):
            delete_file(att.storage_key)
        await self.session.delete(announcement)
        await self.audit.log(actor_user_id=actor_id, action="announcement.deleted", entity_type="announcement", entity_id=announcement_id)

    # ------------------------------------------------------------------ attachments
    async def attachments_of(self, ids: list[uuid.UUID]) -> list[AnnouncementAttachment]:
        if not ids:
            return []
        stmt = select(AnnouncementAttachment).where(AnnouncementAttachment.announcement_id.in_(ids)).order_by(AnnouncementAttachment.created_at)
        return list((await self.session.execute(stmt)).scalars())

    async def add_attachment(self, announcement_id: uuid.UUID, *, content: bytes, filename: str, declared_type: str | None, actor_id: uuid.UUID) -> AnnouncementAttachment:
        announcement = await self.get(announcement_id)
        existing = (await self.session.execute(
            select(func.count()).select_from(AnnouncementAttachment).where(AnnouncementAttachment.announcement_id == announcement.id)
        )).scalar_one()
        if existing >= MAX_ATTACHMENTS_PER_ANNOUNCEMENT:
            raise BusinessRuleViolationError(f"An announcement can have at most {MAX_ATTACHMENTS_PER_ANNOUNCEMENT} attachments.")
        storage_key, name, content_type = save_attachment(
            entity_id=announcement.id, content=content, original_filename=filename, declared_content_type=declared_type
        )
        attachment = AnnouncementAttachment(announcement_id=announcement.id, original_name=name, storage_key=storage_key,
                                            content_type=content_type, size_bytes=len(content))
        self.session.add(attachment)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="announcement.attachment_added", entity_type="announcement", entity_id=announcement.id)
        return attachment

    async def get_attachment(self, attachment_id: uuid.UUID) -> AnnouncementAttachment:
        attachment = await self.session.get(AnnouncementAttachment, attachment_id)
        if attachment is None:
            raise ResourceNotFoundError("Attachment not found.")
        return attachment

    async def remove_attachment(self, attachment_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        attachment = await self.get_attachment(attachment_id)
        delete_file(attachment.storage_key)
        await self.session.delete(attachment)
        await self.audit.log(actor_user_id=actor_id, action="announcement.attachment_removed", entity_type="announcement", entity_id=attachment.announcement_id)

    # ------------------------------------------------------------------ who can read what
    async def viewer_for(self, user_id: uuid.UUID, roles: list[str]) -> Viewer:
        is_admin = Roles.ADMIN in roles
        student = Roles.STUDENT in roles
        staff = any(r not in (Roles.STUDENT, Roles.TEACHER, Roles.ADMIN, Roles.PARENT) for r in roles)
        base = Viewer(user_id=user_id, is_admin=is_admin, student=student, teacher=Roles.TEACHER in roles, staff=staff)
        if student:
            s = (await self.session.execute(select(Student).where(Student.user_id == user_id))).scalars().first()
            if s is not None:
                return await self.student_viewer(s, user_id=user_id, teacher=base.teacher, staff=staff)
        return base

    async def student_viewer(self, s: Student, *, user_id: uuid.UUID, teacher: bool = False, staff: bool = False) -> Viewer:
        """What a student's announcements are matched on (also used for a parent looking at their child)."""
        from app.academics.models import Enrollment, EnrollmentStatus

        offerings = (await self.session.execute(select(Enrollment.course_offering_id).where(
            Enrollment.student_id == s.id, Enrollment.status == EnrollmentStatus.ENROLLED))).scalars().all()
        return Viewer(user_id=user_id, is_admin=False, student=True, teacher=teacher, staff=staff, program_id=s.program_id,
                      semester=s.current_semester, offering_ids=frozenset(str(o) for o in offerings))

    @staticmethod
    def is_for(a: Announcement, viewer: Viewer) -> bool:
        if viewer.is_admin:
            return True
        if viewer.student and a.audience_students:
            if a.offering_ids:
                if viewer.offering_ids.intersection(a.offering_ids):
                    return True
            elif (not a.program_ids or str(viewer.program_id) in a.program_ids) and (not a.semesters or viewer.semester in a.semesters):
                return True
        return (viewer.teacher and a.audience_teachers) or (viewer.staff and a.audience_staff)

    @staticmethod
    def is_live(a: Announcement, now: datetime) -> bool:
        return a.status == AnnouncementStatus.PUBLISHED and (a.expires_at is None or a.expires_at > now)

    async def visible_to(self, viewer: Viewer) -> list[Announcement]:
        now = datetime.now(timezone.utc)
        stmt = select(Announcement).where(Announcement.status == AnnouncementStatus.PUBLISHED).order_by(
            Announcement.pinned.desc(), Announcement.published_at.desc()
        )
        return [a for a in (await self.session.execute(stmt)).scalars() if self.is_live(a, now) and self.is_for(a, viewer)]

    async def read_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        return set((await self.session.execute(select(AnnouncementRead.announcement_id).where(AnnouncementRead.user_id == user_id))).scalars())

    async def mark_read(self, announcement_id: uuid.UUID, user_id: uuid.UUID) -> None:
        exists = await self.session.get(AnnouncementRead, (announcement_id, user_id))
        if exists is None:
            self.session.add(AnnouncementRead(announcement_id=announcement_id, user_id=user_id, read_at=datetime.now(timezone.utc)))

    async def mark_all_read(self, viewer: Viewer) -> int:
        seen = await self.read_ids(viewer.user_id)
        fresh = [a for a in await self.visible_to(viewer) if a.id not in seen]
        for a in fresh:
            self.session.add(AnnouncementRead(announcement_id=a.id, user_id=viewer.user_id, read_at=datetime.now(timezone.utc)))
        return len(fresh)

    # ------------------------------------------------------------------ recipients, notifications, reach
    async def recipients(self, a: Announcement) -> list[tuple[uuid.UUID, str | None]]:
        """(user_id, link to read it) for everyone the announcement is meant for."""
        out: dict[uuid.UUID, str | None] = {}
        if a.audience_students:
            stmt = select(Student.user_id).where(Student.user_id.is_not(None), Student.status == StudentStatus.ACTIVE)
            if a.offering_ids:
                from app.academics.models import Enrollment, EnrollmentStatus

                stmt = stmt.where(Student.id.in_(select(Enrollment.student_id).where(
                    Enrollment.course_offering_id.in_([uuid.UUID(o) for o in a.offering_ids]), Enrollment.status == EnrollmentStatus.ENROLLED)))
            else:
                if a.program_ids:
                    stmt = stmt.where(Student.program_id.in_([uuid.UUID(p) for p in a.program_ids]))
                if a.semesters:
                    stmt = stmt.where(Student.current_semester.in_([int(x) for x in a.semesters]))
            for (uid,) in (await self.session.execute(stmt)).all():
                out[uid] = PORTAL_FOR_ROLE[Roles.STUDENT]
        wanted = []
        if a.audience_teachers:
            wanted.append(lambda code: code == Roles.TEACHER)
        if a.audience_staff:
            wanted.append(lambda code: code not in (Roles.STUDENT, Roles.TEACHER, Roles.ADMIN, Roles.PARENT))
        if wanted:
            rows = (await self.session.execute(
                select(User.id, Role.code).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(User.is_active.is_(True))
            )).all()
            for uid, code in rows:
                if any(w(code) for w in wanted):
                    out.setdefault(uid, PORTAL_FOR_ROLE.get(code))
        return list(out.items())

    async def _notify(self, a: Announcement) -> None:
        tag = {AnnouncementPriority.URGENT: "URGENT: ", AnnouncementPriority.IMPORTANT: "Important: "}.get(a.priority, "")
        snippet = " ".join(a.body.split())
        snippet = snippet[:150] + ("\u2026" if len(snippet) > 150 else "")
        message = f"{snippet}  \u2014 {a.issuer_name}, {a.issuer_designation}"[:1000]
        rows = [
            Notification(user_id=uid, category=NotificationCategory.ANNOUNCEMENT, title=(tag + a.title)[:200], message=message,
                         link=f"{base}?open={a.id}" if base else None)
            for uid, base in await self.recipients(a)
        ]
        for i in range(0, len(rows), 1000):
            self.session.add_all(rows[i:i + 1000])

    async def reach(self, announcements: list[Announcement]) -> dict[uuid.UUID, tuple[int, int]]:
        """{announcement id: (people it was sent to, people who have opened it)} -- for the administrator."""
        if not announcements:
            return {}
        counts = {
            aid: n for aid, n in (await self.session.execute(
                select(AnnouncementRead.announcement_id, func.count()).where(AnnouncementRead.announcement_id.in_([a.id for a in announcements]))
                .group_by(AnnouncementRead.announcement_id)
            )).all()
        }
        out = {}
        for a in announcements:
            out[a.id] = (len(await self.recipients(a)) if a.status != AnnouncementStatus.DRAFT else 0, counts.get(a.id, 0))
        return out

    async def program_names(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        return {pid: code for pid, code in (await self.session.execute(select(Program.id, Program.code).where(Program.id.in_(ids)))).all()}