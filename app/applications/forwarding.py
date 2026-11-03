"""Forwarding an application to a department, the department's answer, and the stage the applicant sees.

The department never decides the application: it does its part (checks a paper, fixes a record, recounts marks...)
and sends it back to the administration with a note. The applicant sees where it is, not the internal notes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.models import OPEN_STATUSES, Application, ApplicationForward, ApplicationStatus
from app.auth.models import Role, User, UserRole
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.notifications.models import Notification, NotificationCategory

# Departments an application can be forwarded to (role codes) and how people see them.
DEPARTMENTS = {
    "EXAM_CONTROLLER": "Exams", "ACCOUNTS_OFFICER": "Accounts", "LIBRARIAN": "Library", "HOSTEL_WARDEN": "Hostel",
    "HOD": "Head of department", "STAFF": "Administration staff", "SECURITY_GUARD": "Gate / security",
}
PORTAL_OF = {"EXAM_CONTROLLER": "exam", "ACCOUNTS_OFFICER": "accounts", "LIBRARIAN": "library", "HOSTEL_WARDEN": "warden",
             "HOD": "faculty", "STAFF": "staff", "SECURITY_GUARD": "gate"}
OPEN_FORWARD = ("FORWARDED", "IN_PROGRESS")
FORWARD_LABEL = {"FORWARDED": "Waiting", "IN_PROGRESS": "In progress", "DONE": "Done", "CANNOT_DO": "Cannot do", "RECALLED": "Taken back"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def dept_name(code: str) -> str:
    return DEPARTMENTS.get(code, code.replace("_", " ").title())


def stage_label(app: Application, forwards: list[ApplicationForward]) -> str:
    """One line the applicant understands: where the application is now."""
    if app.status == ApplicationStatus.APPROVED:
        return "Approved"
    if app.status == ApplicationStatus.REJECTED:
        return "Rejected"
    if app.status == ApplicationStatus.WITHDRAWN:
        return "Withdrawn"
    if app.status == ApplicationStatus.MORE_INFO_NEEDED:
        return "Waiting for your reply"
    live = [f for f in forwards if f.status in OPEN_FORWARD]
    if live:
        working = [f for f in live if f.status == "IN_PROGRESS"]
        if working:
            return " and ".join(dept_name(f.department) for f in working) + " " + ("is" if len(working) == 1 else "are") + " working on it"
        return "Forwarded to " + " and ".join(dept_name(f.department) for f in live)
    if any(f.status in ("DONE", "CANNOT_DO") for f in forwards) and app.status == ApplicationStatus.UNDER_REVIEW:
        return "Back with the administration"
    if app.status == ApplicationStatus.UNDER_REVIEW:
        return "In review"
    return "Submitted, waiting for the administration"


def timeline(app: Application, forwards: list[ApplicationForward], *, internal: bool) -> list[dict]:
    """Every step so far, oldest first. `internal` adds the administration's and departments' notes."""
    out = [{"at": app.created_at, "label": "Submitted", "state": "done", "note": None}]
    if app.reviewed_at:
        out.append({"at": app.reviewed_at, "label": "In review by the administration", "state": "done", "note": None})
    for f in forwards:
        if f.status == "RECALLED" and not internal:
            continue
        name = dept_name(f.department)
        out.append({"at": f.created_at, "label": f"Forwarded to {name}", "state": "done", "note": f.note if internal else None})
        if f.started_at:
            out.append({"at": f.started_at, "label": f"{name} started working on it", "state": "done", "note": None})
        if f.status in ("DONE", "CANNOT_DO") and f.finished_at:
            out.append({"at": f.finished_at, "label": f"{name} finished; back with the administration" if f.status == "DONE"
                        else f"{name} could not do it; back with the administration", "state": "done" if f.status == "DONE" else "warn",
                        "note": f.response if internal else None})
        if f.status == "RECALLED" and internal:
            out.append({"at": f.updated_at, "label": f"Taken back from {name}", "state": "warn", "note": None})
    if app.decided_at:
        state = {"APPROVED": "good", "REJECTED": "bad"}.get(app.status.value, "done")
        out.append({"at": app.decided_at, "label": {"APPROVED": "Approved", "REJECTED": "Rejected", "WITHDRAWN": "Withdrawn"}.get(app.status.value, "Closed"),
                    "state": state, "note": None})
    out.sort(key=lambda e: e["at"])
    return out


def forward_out(f: ApplicationForward, names: dict) -> dict:
    return {"id": f.id, "department": f.department, "department_name": dept_name(f.department), "status": f.status,
            "status_label": FORWARD_LABEL.get(f.status, f.status), "note": f.note, "response": f.response,
            "forwarded_at": f.created_at, "started_at": f.started_at, "finished_at": f.finished_at,
            "forwarded_by": names.get(f.forwarded_by), "responded_by": names.get(f.responded_by)}


class ForwardingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_apps(self, app_ids: list) -> dict:
        if not app_ids:
            return {}
        out: dict = {}
        for f in (await self.session.execute(select(ApplicationForward).where(ApplicationForward.application_id.in_(app_ids))
                                             .order_by(ApplicationForward.created_at))).scalars():
            out.setdefault(f.application_id, []).append(f)
        return out

    async def _names(self, ids: set) -> dict:
        from app.notifications.email_service import _person_label

        return {i: await _person_label(self.session, i) for i in ids if i}

    async def _members(self, department: str) -> list[uuid.UUID]:
        return list(set((await self.session.execute(select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
                                                    .where(Role.code == department, User.is_active.is_(True)))).scalars().all()))

    async def _admins(self) -> list[uuid.UUID]:
        return await self._members("ADMIN")

    def _notify(self, users: list, title: str, message: str, link: str | None) -> None:
        self.session.add_all([Notification(user_id=u, category=NotificationCategory.APPLICATION, title=title[:200], message=message[:1000], link=link) for u in users])

    # ------------------------------------------------------------------ the administration
    async def forward(self, application_id: uuid.UUID, *, admin_id: uuid.UUID, department: str, note: str | None) -> ApplicationForward:
        from app.applications.service import ApplicationService, reference
        from app.audit.service import AuditService

        if department not in DEPARTMENTS:
            raise ValidationAppError("Choose a department to forward it to.")
        service = ApplicationService(self.session)
        app = await service.get(application_id)
        ensure_not_self(admin_id, app)
        if app.status not in OPEN_STATUSES:
            raise BusinessRuleViolationError("This application is already closed, so it cannot be forwarded.")
        live = (await self.session.execute(select(ApplicationForward).where(ApplicationForward.application_id == app.id, ApplicationForward.department == department,
                                                                           ApplicationForward.status.in_(OPEN_FORWARD)))).scalars().first()
        if live is not None:
            raise BusinessRuleViolationError(f"It is already with {dept_name(department)}.")
        if not await self._members(department):
            raise BusinessRuleViolationError(f"Nobody has the {dept_name(department)} role yet, so nobody would see it. Give someone that role first (Employees).")
        f = ApplicationForward(application_id=app.id, department=department, note=(note or "").strip()[:2000] or None, forwarded_by=admin_id, status="FORWARDED")
        self.session.add(f)
        if app.status == ApplicationStatus.SUBMITTED:
            app.status = ApplicationStatus.UNDER_REVIEW
        app.reviewed_at = app.reviewed_at or _now()
        await self.session.flush()
        ref = reference(app)
        self._notify(await self._members(department), f"Application forwarded to {dept_name(department)}", f"{ref}: “{app.subject}”" + (f" — {f.note}" if f.note else ""),
                     f"/dashboard/{PORTAL_OF[department]}/applications#dept")
        await service._notify_applicant(app, f"Forwarded to {dept_name(department)}", f"{ref}: “{app.subject}” is now with the {dept_name(department)} department.")
        await AuditService(self.session).log(actor_user_id=admin_id, action="application.forwarded", entity_type="application", entity_id=app.id,
                                             after={"department": department, "note": f.note})
        return f

    async def recall(self, forward_id: uuid.UUID, *, admin_id: uuid.UUID) -> ApplicationForward:
        from app.audit.service import AuditService

        f = await self.session.get(ApplicationForward, forward_id)
        if f is None:
            raise ResourceNotFoundError("That forward was not found.")
        if f.status not in OPEN_FORWARD:
            raise BusinessRuleViolationError("The department has already answered; there is nothing to take back.")
        f.status = "RECALLED"
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=admin_id, action="application.forward_recalled", entity_type="application", entity_id=f.application_id,
                                             after={"department": f.department})
        return f

    # ------------------------------------------------------------------ the department
    @staticmethod
    def my_departments(roles: list[str]) -> list[str]:
        return [r for r in roles if r in DEPARTMENTS]

    async def inbox(self, roles: list[str], *, show: str = "open") -> list[dict]:
        from app.applications.service import ApplicationService

        depts = self.my_departments(roles)
        if not depts:
            return []
        stmt = select(ApplicationForward).where(ApplicationForward.department.in_(depts), ApplicationForward.status != "RECALLED")
        if show == "open":
            stmt = stmt.where(ApplicationForward.status.in_(OPEN_FORWARD))
        rows = list((await self.session.execute(stmt.order_by(ApplicationForward.created_at.desc()).limit(300))).scalars())
        if not rows:
            return []
        service = ApplicationService(self.session)
        apps = {a.id: a for a in (await self.session.execute(select(Application).where(Application.id.in_({f.application_id for f in rows})))).scalars()}
        summaries = {s["id"]: s for s in await service._summaries(list(apps.values()), admin=True)}
        names = await self._names({f.forwarded_by for f in rows} | {f.responded_by for f in rows})
        out = []
        for f in rows:
            s = summaries.get(f.application_id)
            if s is None:
                continue
            full = await service.applicant(apps[f.application_id].applicant_user_id)
            out.append({"forward": forward_out(f, names), "application": {
                "id": s["id"], "reference": s["reference"], "type_name": s["type_name"], "subject": s["subject"], "details": s["details"],
                "from_date": s["from_date"], "to_date": s["to_date"], "created_at": s["created_at"], "attachments": s["attachments"],
                "status": s["status"], "applicant": {k: full[k] for k in ("kind", "name", "identifier", "detail")}}})
        return out

    async def respond(self, forward_id: uuid.UUID, *, user_id: uuid.UUID, roles: list[str], status: str, response: str | None) -> ApplicationForward:
        from app.applications.service import ApplicationService, reference
        from app.audit.service import AuditService

        f = await self.session.get(ApplicationForward, forward_id)
        if f is None or f.department not in self.my_departments(roles):
            raise ResourceNotFoundError("That application was not found.")
        if f.status not in OPEN_FORWARD:
            raise BusinessRuleViolationError("This has already been sent back to the administration (or taken back).")
        if status not in ("IN_PROGRESS", "DONE", "CANNOT_DO"):
            raise ValidationAppError("Choose In progress, Done or Cannot do.")
        response = (response or "").strip()
        if status in ("DONE", "CANNOT_DO") and len(response) < 3:
            raise ValidationAppError("Write a short note for the administration: what was done, or why it cannot be done.")
        service = ApplicationService(self.session)
        app = await service.get(f.application_id)
        ref = reference(app)
        dept = dept_name(f.department)
        if status == "IN_PROGRESS":
            if f.status == "FORWARDED":
                f.status, f.started_at = "IN_PROGRESS", _now()
                if response:
                    f.response = response[:2000]
                await service._notify_applicant(app, f"{dept} is working on your application", f"{ref}: “{app.subject}”")
        else:
            f.status, f.finished_at = status, _now()
            f.started_at = f.started_at or f.finished_at
            f.response, f.responded_by = response[:2000], user_id
            self._notify(await self._admins(), f"{dept} {'finished' if status == 'DONE' else 'could not do'} {ref}", f"“{app.subject}” — {response}",
                         f"/dashboard/admin/applications?open={app.id}")
            await service._notify_applicant(app, "Back with the administration", f"{ref}: {dept} has done its part. The administration will make the final decision.")
        f.responded_by = user_id
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=user_id, action=f"application.department_{status.lower()}", entity_type="application", entity_id=app.id,
                                             after={"department": f.department, "response": f.response})
        return f

    async def can_see(self, application_id: uuid.UUID, roles: list[str]) -> bool:
        """A department sees an application (and its files) while it has been forwarded to them (not taken back)."""
        depts = self.my_departments(roles)
        if not depts:
            return False
        return (await self.session.execute(select(ApplicationForward.id).where(ApplicationForward.application_id == application_id,
                                                                              ApplicationForward.department.in_(depts), ApplicationForward.status != "RECALLED").limit(1))).first() is not None

    async def waiting_count(self, roles: list[str]) -> int:
        from sqlalchemy import func

        depts = self.my_departments(roles)
        if not depts:
            return 0
        return (await self.session.execute(select(func.count()).select_from(ApplicationForward).where(ApplicationForward.department.in_(depts),
                                                                                                     ApplicationForward.status == "FORWARDED"))).scalar_one()


def ensure_not_self(admin_id, app) -> None:
    if admin_id == app.applicant_user_id:
        raise AuthorizationError("This is your own application, so another administrator must handle it.")