from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.models import (
    OPEN_STATUSES,
    ApplicantKind,
    Application,
    ApplicationAttachment,
    ApplicationComment,
    ApplicationStatus,
    ApplicationType,
)
from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.core.storage import FileTooLargeError, UnsupportedFileTypeError, delete_file, save_attachment, validate_attachment
from app.employees.models import Employee, JobRole
from app.notifications.models import Notification, NotificationCategory
from app.programs.models import Program
from app.students.models import Student

MAX_FILES = 5
FOLDER = "applications"
PORTAL = {Roles.STUDENT: "student", Roles.TEACHER: "faculty", Roles.LIBRARIAN: "library", Roles.ACCOUNTS_OFFICER: "accounts", Roles.SECURITY_GUARD: "gate", Roles.EXAM_CONTROLLER: "exam", Roles.HOSTEL_WARDEN: "warden", Roles.STAFF: "staff"}


def reference(app: Application) -> str:
    return f"APP-{app.created_at.year}-{app.serial_no:05d}"


def days_of(app: Application) -> int | None:
    return (app.to_date - app.from_date).days + 1 if app.from_date and app.to_date else None


def awaiting(status: ApplicationStatus) -> str | None:
    if status in (ApplicationStatus.SUBMITTED, ApplicationStatus.UNDER_REVIEW):
        return "ADMIN"
    return "APPLICANT" if status == ApplicationStatus.MORE_INFO_NEEDED else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ who is applying
    async def applicant(self, user_id: uuid.UUID) -> dict:
        student = (await self.session.execute(select(Student).where(Student.user_id == user_id))).scalars().first()
        if student is not None:
            program = await self.session.get(Program, student.program_id) if student.program_id else None
            detail = (program.code if program else "") + (f" \u00b7 Semester {student.current_semester}" if student.current_semester else "")
            return {"kind": ApplicantKind.STUDENT, "ref_id": student.id, "name": f"{student.first_name} {student.last_name}",
                    "identifier": student.roll_number, "detail": detail.strip(" \u00b7") or None, "contact_no": student.contact_no}
        employee = (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalars().first()
        if employee is not None:
            role = await self.session.get(JobRole, employee.job_role_id)
            return {"kind": ApplicantKind.EMPLOYEE, "ref_id": employee.id, "name": f"{employee.first_name} {employee.last_name}",
                    "identifier": employee.employee_code, "detail": role.title if role else None, "contact_no": employee.contact_no}
        raise AuthorizationError("This account is not linked to a student or employee record.")

    async def _applicants_by_user(self, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
        out: dict[uuid.UUID, dict] = {}
        if not user_ids:
            return out
        for st in (await self.session.execute(select(Student).where(Student.user_id.in_(user_ids)))).scalars():
            out[st.user_id] = {"kind": ApplicantKind.STUDENT, "name": f"{st.first_name} {st.last_name}", "identifier": st.roll_number, "detail": None, "contact_no": st.contact_no}
        for em in (await self.session.execute(select(Employee).where(Employee.user_id.in_(user_ids)))).scalars():
            out.setdefault(em.user_id, {"kind": ApplicantKind.EMPLOYEE, "name": f"{em.first_name} {em.last_name}", "identifier": em.employee_code, "detail": None, "contact_no": em.contact_no})
        return out

    async def _admin_ids(self) -> list[uuid.UUID]:
        rows = (await self.session.execute(
            select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.ADMIN, User.is_active.is_(True))
        )).scalars().all()
        return list(set(rows))

    async def _page_for(self, user_id: uuid.UUID) -> str | None:
        codes = (await self.session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id))).scalars().all()
        for code in codes:
            if code in PORTAL:
                return f"/dashboard/{PORTAL[code]}/applications"
        return None

    async def _notify_admins(self, app: Application, title: str, message: str) -> None:
        link = f"/dashboard/admin/applications?open={app.id}"
        self.session.add_all([Notification(user_id=uid, category=NotificationCategory.APPLICATION, title=title[:200], message=message[:1000], link=link) for uid in await self._admin_ids()])

    async def _notify_applicant(self, app: Application, title: str, message: str) -> None:
        base = await self._page_for(app.applicant_user_id)
        self.session.add(Notification(user_id=app.applicant_user_id, category=NotificationCategory.APPLICATION, title=title[:200], message=message[:1000],
                                      link=f"{base}?open={app.id}" if base else None))

    # ------------------------------------------------------------------ application types
    async def get_type(self, type_id: uuid.UUID) -> ApplicationType:
        t = await self.session.get(ApplicationType, type_id)
        if t is None:
            raise ResourceNotFoundError("That kind of application does not exist.")
        return t

    async def list_types(self, *, kind: ApplicantKind | None = None, active_only: bool = True) -> list[ApplicationType]:
        stmt = select(ApplicationType).order_by(ApplicationType.sort_order, ApplicationType.name)
        if active_only:
            stmt = stmt.where(ApplicationType.is_active.is_(True))
        rows = list((await self.session.execute(stmt)).scalars())
        if kind == ApplicantKind.STUDENT:
            rows = [t for t in rows if t.for_students]
        elif kind == ApplicantKind.EMPLOYEE:
            rows = [t for t in rows if t.for_employees]
        return rows

    async def create_type(self, *, data: dict, actor_id: uuid.UUID) -> ApplicationType:
        data["name"] = data["name"].strip()
        if (await self.session.execute(select(func.count()).select_from(ApplicationType).where(func.lower(ApplicationType.name) == data["name"].lower()))).scalar_one():
            raise BusinessRuleViolationError("There is already an application type with that name.")
        t = ApplicationType(**data)
        self.session.add(t)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="application.type_created", entity_type="application_type", entity_id=t.id)
        return t

    async def update_type(self, type_id: uuid.UUID, *, changes: dict, actor_id: uuid.UUID) -> ApplicationType:
        t = await self.get_type(type_id)
        if changes.pop("clear_limit", False):
            t.yearly_limit_days = None
        for key, value in changes.items():
            if value is not None:
                setattr(t, key, value.strip() if isinstance(value, str) and key == "name" else value)
        if not (t.for_students or t.for_employees):
            raise BusinessRuleViolationError("Choose who can apply for this (students, employees or both).")
        await self.audit.log(actor_user_id=actor_id, action="application.type_updated", entity_type="application_type", entity_id=t.id)
        return t

    async def usage(self, user_id: uuid.UUID, t: ApplicationType, year: int | None = None, exclude: uuid.UUID | None = None) -> dict | None:
        """Days used in the year against the type's yearly limit (informational: the administration decides)."""
        if not t.yearly_limit_days or not t.requires_dates:
            return None
        year = year or institute_today().year
        stmt = select(Application).where(Application.applicant_user_id == user_id, Application.type_id == t.id,
                                         Application.status.in_(list(OPEN_STATUSES) + [ApplicationStatus.APPROVED]))
        approved = pending = 0
        for a in (await self.session.execute(stmt)).scalars():
            if a.id == exclude or not a.from_date or a.from_date.year != year:
                continue
            if a.status == ApplicationStatus.APPROVED:
                approved += days_of(a) or 0
            else:
                pending += days_of(a) or 0
        return {"year": year, "limit_days": t.yearly_limit_days, "approved_days": approved, "pending_days": pending}

    # ------------------------------------------------------------------ applying
    async def create(self, *, user_id: uuid.UUID, type_id: uuid.UUID, subject: str, details: str, from_date: date | None, to_date: date | None,
                     files: list[tuple[str, bytes, str | None]]) -> Application:
        who = await self.applicant(user_id)
        t = await self.get_type(type_id)
        if not t.is_active or (who["kind"] == ApplicantKind.STUDENT and not t.for_students) or (who["kind"] == ApplicantKind.EMPLOYEE and not t.for_employees):
            raise BusinessRuleViolationError("This kind of application is not available to you.")
        subject, details = subject.strip(), details.strip()
        if len(subject) < 3 or len(details) < 3:
            raise BusinessRuleViolationError("Please fill in the subject and the details.")
        if t.requires_dates:
            if not from_date or not to_date:
                raise BusinessRuleViolationError("Choose the first and last date.")
            if to_date < from_date:
                raise BusinessRuleViolationError("The last date cannot be before the first date.")
            today = institute_today()
            if from_date < today - timedelta(days=settings.application_max_backdate_days):
                raise BusinessRuleViolationError(f"The first date is too far in the past (at most {settings.application_max_backdate_days} days back).")
            if to_date > today + timedelta(days=366) or (to_date - from_date).days > 365:
                raise BusinessRuleViolationError("Those dates are too far ahead, or span more than a year.")
            clash = (await self.session.execute(
                select(Application).join(ApplicationType, ApplicationType.id == Application.type_id).where(
                    Application.applicant_user_id == user_id, ApplicationType.requires_dates.is_(True),
                    Application.status.in_(list(OPEN_STATUSES) + [ApplicationStatus.APPROVED]),
                    Application.from_date <= to_date, Application.to_date >= from_date)
            )).scalars().first()
            if clash is not None:
                raise BusinessRuleViolationError(f"You already have an application for some of these dates ({reference(clash)}, {clash.status.value.replace('_', ' ').lower()}).")
        else:
            from_date = to_date = None
        if len(files) > MAX_FILES:
            raise BusinessRuleViolationError(f"You can attach at most {MAX_FILES} files.")
        if t.requires_attachment and not files:
            raise BusinessRuleViolationError("This kind of application needs a document attached.")
        self._check_files(files)

        app = Application(type_id=t.id, applicant_user_id=user_id, applicant_kind=who["kind"], applicant_ref_id=who["ref_id"], subject=subject, details=details,
                          from_date=from_date, to_date=to_date, status=ApplicationStatus.SUBMITTED)
        self.session.add(app)
        await self.session.flush()
        await self.session.refresh(app)
        saved: list[str] = []
        try:
            for name, content, ctype in files:
                saved.append(await self._store(app, user_id, name, content, ctype))
        except Exception:
            for key in saved:
                delete_file(key)
            raise
        await self.audit.log(actor_user_id=user_id, action="application.submitted", entity_type="application", entity_id=app.id)
        await self._notify_admins(app, f"New application: {t.name}", f"{who['name']} ({'student' if who['kind'] == ApplicantKind.STUDENT else 'employee'}) \u2014 {subject}")
        return app

    @staticmethod
    def _check_files(files: list[tuple[str, bytes, str | None]]) -> None:
        """Every file is checked BEFORE anything is saved, so a bad file never leaves a half-made application behind."""
        for name, content, ctype in files:
            try:
                validate_attachment(content=content, original_filename=name, declared_content_type=ctype)
            except (UnsupportedFileTypeError, FileTooLargeError) as exc:
                raise BusinessRuleViolationError(f"\u201c{name}\u201d: {exc}") from exc

    async def _store(self, app: Application, user_id: uuid.UUID, name: str, content: bytes, ctype: str | None) -> str:
        try:
            key, display, content_type = save_attachment(entity_id=app.id, content=content, original_filename=name, declared_content_type=ctype, folder=FOLDER)
        except (UnsupportedFileTypeError, FileTooLargeError) as exc:
            raise BusinessRuleViolationError(f"\u201c{name}\u201d: {exc}") from exc
        self.session.add(ApplicationAttachment(application_id=app.id, original_name=display, storage_key=key, content_type=content_type, size_bytes=len(content), uploaded_by=user_id))
        return key

    # ------------------------------------------------------------------ reading
    async def get(self, application_id: uuid.UUID) -> Application:
        app = await self.session.get(Application, application_id)
        if app is None:
            raise ResourceNotFoundError("Application not found.")
        return app

    async def _visible(self, application_id: uuid.UUID, user_id: uuid.UUID, is_admin: bool) -> Application:
        app = await self.get(application_id)
        if not is_admin and app.applicant_user_id != user_id:
            raise ResourceNotFoundError("Application not found.")          # other people's applications simply are not there
        return app

    async def _summaries(self, apps: list[Application], *, admin: bool) -> list[dict]:
        if not apps:
            return []
        types = {t.id: t for t in (await self.session.execute(select(ApplicationType).where(ApplicationType.id.in_({a.type_id for a in apps})))).scalars()}
        atts: dict[uuid.UUID, list[ApplicationAttachment]] = {}
        for att in (await self.session.execute(select(ApplicationAttachment).where(ApplicationAttachment.application_id.in_([a.id for a in apps])).order_by(ApplicationAttachment.created_at))).scalars():
            atts.setdefault(att.application_id, []).append(att)
        counts = {aid: n for aid, n in (await self.session.execute(
            select(ApplicationComment.application_id, func.count()).where(ApplicationComment.application_id.in_([a.id for a in apps])).group_by(ApplicationComment.application_id))).all()}
        people = await self._applicants_by_user(list({a.applicant_user_id for a in apps})) if admin else {}
        from app.applications.forwarding import ForwardingService, forward_out, stage_label, timeline
        from app.core.storage import attachment_is_inline

        fwd = ForwardingService(self.session)
        forwards = await fwd.for_apps([a.id for a in apps])
        fnames = await fwd._names({f.forwarded_by for fs in forwards.values() for f in fs} | {f.responded_by for fs in forwards.values() for f in fs}) if admin else {}

        out = []
        for a in apps:
            t = types[a.type_id]
            who = people.get(a.applicant_user_id)
            out.append({
                "id": a.id, "reference": reference(a), "type_id": t.id, "type_name": t.name, "requires_dates": t.requires_dates, "subject": a.subject, "details": a.details,
                "from_date": a.from_date, "to_date": a.to_date, "days": days_of(a), "status": a.status, "awaiting": awaiting(a.status), "decision_note": a.decision_note,
                "decided_at": a.decided_at, "reviewed_at": a.reviewed_at, "created_at": a.created_at, "updated_at": a.updated_at,
                "attachments": [{"id": x.id, "original_name": x.original_name, "content_type": x.content_type, "size_bytes": x.size_bytes,
                                 "is_image": x.content_type.startswith("image/"), "inline": attachment_is_inline(x.content_type)} for x in atts.get(a.id, [])],
                "comment_count": counts.get(a.id, 0),
                "applicant": who,
                "stage_label": stage_label(a, forwards.get(a.id, [])),
                "timeline": timeline(a, forwards.get(a.id, []), internal=admin),
                "forwards": [forward_out(f, fnames) for f in forwards.get(a.id, [])] if admin else [],
            })
        return out

    async def mine(self, user_id: uuid.UUID) -> list[dict]:
        apps = list((await self.session.execute(select(Application).where(Application.applicant_user_id == user_id).order_by(Application.created_at.desc()))).scalars())
        return await self._summaries(apps, admin=False)

    async def detail(self, application_id: uuid.UUID, *, user_id: uuid.UUID, is_admin: bool) -> dict:
        app = await self._visible(application_id, user_id, is_admin)
        out = (await self._summaries([app], admin=is_admin))[0]
        t = await self.get_type(app.type_id)
        who = await self._applicants_by_user([app.applicant_user_id])
        info = who.get(app.applicant_user_id)
        if is_admin:
            full = await self.applicant(app.applicant_user_id) if info else None
            out["applicant"] = {k: full[k] for k in ("kind", "name", "identifier", "detail", "contact_no")} if full else info
        comments = list((await self.session.execute(select(ApplicationComment).where(ApplicationComment.application_id == app.id).order_by(ApplicationComment.created_at))).scalars())
        name = info["name"] if info else "Applicant"
        out["comments"] = [{"id": c.id, "side": c.side, "author_name": name if c.side == "APPLICANT" else "Administration", "body": c.body, "created_at": c.created_at} for c in comments]
        out["usage"] = await self.usage(app.applicant_user_id, t, (app.from_date.year if app.from_date else None), exclude=None)
        if is_admin:
            others = list((await self.session.execute(select(Application).where(Application.applicant_user_id == app.applicant_user_id, Application.id != app.id)
                                                      .order_by(Application.created_at.desc()).limit(5))).scalars())
            types = {x.id: x for x in (await self.session.execute(select(ApplicationType).where(ApplicationType.id.in_({o.type_id for o in others})))).scalars()} if others else {}
            out["others"] = [{"id": o.id, "reference": reference(o), "type_name": types[o.type_id].name, "subject": o.subject, "status": o.status, "from_date": o.from_date,
                              "to_date": o.to_date, "created_at": o.created_at} for o in others]
        return out

    async def list_for_admin(self, *, status: ApplicationStatus | None, needs_action: bool, type_id: uuid.UUID | None, kind: ApplicantKind | None,
                             q: str | None, limit: int, offset: int) -> list[dict]:
        stmt = select(Application)
        if needs_action:
            stmt = stmt.where(Application.status == ApplicationStatus.SUBMITTED).order_by(Application.created_at.asc())
        else:
            if status:
                stmt = stmt.where(Application.status == status)
            stmt = stmt.order_by(Application.created_at.desc())
        if type_id:
            stmt = stmt.where(Application.type_id == type_id)
        if kind:
            stmt = stmt.where(Application.applicant_kind == kind)
        apps = list((await self.session.execute(stmt.limit(1000))).scalars())
        rows = await self._summaries(apps, admin=True)
        if q and q.strip():
            needle = q.strip().lower()
            rows = [r for r in rows if needle in r["subject"].lower() or needle in r["reference"].lower() or needle in r["type_name"].lower()
                    or (r["applicant"] and (needle in r["applicant"]["name"].lower() or needle in (r["applicant"]["identifier"] or "").lower()))]
        return rows[max(0, offset): max(0, offset) + max(1, min(limit, 200))]

    async def badge(self, user_id: uuid.UUID, is_admin: bool) -> int:
        if is_admin:
            stmt = select(func.count()).select_from(Application).where(Application.status == ApplicationStatus.SUBMITTED)
        else:
            stmt = select(func.count()).select_from(Application).where(Application.applicant_user_id == user_id, Application.status == ApplicationStatus.MORE_INFO_NEEDED)
        return (await self.session.execute(stmt)).scalar_one()

    # ------------------------------------------------------------------ the conversation
    async def comment(self, application_id: uuid.UUID, *, user_id: uuid.UUID, is_admin: bool, body: str) -> Application:
        app = await self._visible(application_id, user_id, is_admin)
        body = body.strip()
        if not body:
            raise BusinessRuleViolationError("Write a message first.")
        side = "ADMIN" if is_admin else "APPLICANT"
        if side == "APPLICANT":
            if app.status not in OPEN_STATUSES:
                raise BusinessRuleViolationError("This application is closed, so it can no longer be changed.")
            if app.status == ApplicationStatus.MORE_INFO_NEEDED:
                app.status = ApplicationStatus.SUBMITTED            # they have answered: it is the administration's move again
        self.session.add(ApplicationComment(application_id=app.id, author_user_id=user_id, side=side, body=body))
        await self.session.flush()
        if side == "APPLICANT":
            who = await self.applicant(app.applicant_user_id)
            await self._notify_admins(app, f"Reply on {reference(app)}", f"{who['name']}: {body}")
        else:
            await self._notify_applicant(app, "Message about your application", f"{reference(app)}: {body}")
        return app

    async def add_attachments(self, application_id: uuid.UUID, *, user_id: uuid.UUID, files: list[tuple[str, bytes, str | None]]) -> Application:
        app = await self._visible(application_id, user_id, False)
        if app.status not in OPEN_STATUSES:
            raise BusinessRuleViolationError("This application is closed, so files can no longer be added.")
        existing = (await self.session.execute(select(func.count()).select_from(ApplicationAttachment).where(ApplicationAttachment.application_id == app.id))).scalar_one()
        if not files or existing + len(files) > MAX_FILES:
            raise BusinessRuleViolationError(f"An application can have at most {MAX_FILES} files.")
        self._check_files(files)
        saved: list[str] = []
        try:
            for name, content, ctype in files:
                saved.append(await self._store(app, user_id, name, content, ctype))
        except Exception:
            for key in saved:
                delete_file(key)
            raise
        if app.status == ApplicationStatus.MORE_INFO_NEEDED:
            app.status = ApplicationStatus.SUBMITTED
        who = await self.applicant(user_id)
        await self._notify_admins(app, f"New file on {reference(app)}", f"{who['name']} added {len(files)} file{'s' if len(files) > 1 else ''}.")
        return app

    async def get_attachment(self, attachment_id: uuid.UUID, *, user_id: uuid.UUID, is_admin: bool, roles: list[str] | None = None) -> ApplicationAttachment:
        att = await self.session.get(ApplicationAttachment, attachment_id)
        if att is None:
            raise ResourceNotFoundError("Attachment not found.")
        if roles:
            from app.applications.forwarding import ForwardingService

            if await ForwardingService(self.session).can_see(att.application_id, roles):
                return att                          # the department it was forwarded to may open its documents
        await self._visible(att.application_id, user_id, is_admin)
        return att

    async def withdraw(self, application_id: uuid.UUID, *, user_id: uuid.UUID) -> Application:
        app = await self._visible(application_id, user_id, False)
        if app.status not in OPEN_STATUSES:
            raise BusinessRuleViolationError("Only an application that is still waiting can be withdrawn.")
        app.status, app.decided_at, app.decision_note = ApplicationStatus.WITHDRAWN, _now(), "Withdrawn by the applicant."
        await self.audit.log(actor_user_id=user_id, action="application.withdrawn", entity_type="application", entity_id=app.id)
        who = await self.applicant(user_id)
        await self._notify_admins(app, f"Withdrawn: {reference(app)}", f"{who['name']} withdrew \u201c{app.subject}\u201d.")
        return app

    # ------------------------------------------------------------------ the administration decides
    async def review(self, application_id: uuid.UUID, *, admin_id: uuid.UUID, action: str, note: str | None) -> Application:
        app = await self.get(application_id)
        from app.core.controls import refuse_self

        refuse_self(admin_id, app.applicant_user_id, "This is your own application, so another administrator must review it.")
        if app.status == ApplicationStatus.WITHDRAWN:
            raise BusinessRuleViolationError("The applicant withdrew this application.")
        note = (note or "").strip() or None
        ref = reference(app)
        if action == "START_REVIEW":
            if app.status not in (ApplicationStatus.SUBMITTED, ApplicationStatus.MORE_INFO_NEEDED):
                raise BusinessRuleViolationError("This application is already being reviewed or has been decided.")
            app.status = ApplicationStatus.UNDER_REVIEW
            app.reviewed_at = app.reviewed_at or _now()
            await self._notify_applicant(app, "Application under review", f"{ref}: \u201c{app.subject}\u201d is being looked at.")
        elif action == "REQUEST_INFO":
            if app.status not in OPEN_STATUSES:
                raise BusinessRuleViolationError("This application has already been decided.")
            app.status = ApplicationStatus.MORE_INFO_NEEDED
            app.reviewed_at = app.reviewed_at or _now()
            self.session.add(ApplicationComment(application_id=app.id, author_user_id=admin_id, side="ADMIN", body=note))
            await self._notify_applicant(app, "More information needed", f"{ref}: {note}")
        elif action in ("APPROVE", "REJECT"):
            approved = action == "APPROVE"
            app.status = ApplicationStatus.APPROVED if approved else ApplicationStatus.REJECTED
            app.reviewed_at = app.reviewed_at or _now()
            app.decided_at, app.decided_by, app.decision_note = _now(), admin_id, note
            if approved:
                await self._excuse_attendance(app, admin_id)
            await self._notify_applicant(app, "Application approved" if approved else "Application rejected",
                                         f"{ref}: \u201c{app.subject}\u201d" + (f" \u2014 {note}" if note else ""))
        else:
            raise BusinessRuleViolationError("Unknown action.")
        await self.audit.log(actor_user_id=admin_id, action=f"application.{action.lower()}", entity_type="application", entity_id=app.id)
        return app

    async def _excuse_attendance(self, app: Application, admin_id: uuid.UUID) -> None:
        """Approved leave reaches attendance by itself: no separate correction for the office to forget."""
        t = await self.get_type(app.type_id)
        if not t.excuses_attendance or not app.from_date:
            return
        to = app.to_date or app.from_date
        why = f"{t.name} ({reference(app)})"
        if app.applicant_kind == ApplicantKind.EMPLOYEE:
            from app.timekeeping.service import TimekeepingService

            await TimekeepingService(self.session).excuse_range(app.applicant_ref_id, app.from_date, to, why, actor_id=admin_id)
        else:
            from app.attendance.service import AttendanceService

            await AttendanceService(self.session).grant_leave(app.applicant_ref_id, from_date=app.from_date, to_date=to, reason=why, actor_id=admin_id)

    # ------------------------------------------------------------------ the charts
    async def overview(self) -> dict:
        rows = (await self.session.execute(select(Application.status, Application.type_id, Application.applicant_kind, Application.created_at, Application.decided_at).limit(50000))).all()
        names = {t.id: t.name for t in (await self.session.execute(select(ApplicationType))).scalars()}
        tz = ZoneInfo(settings.institute_timezone)
        today = institute_today()
        local = lambda dt: dt.astimezone(tz).date()          # noqa: E731
        status_counts = {s: 0 for s in ApplicationStatus}
        by_type: dict = {}
        by_kind: dict = {}
        hours, oldest = [], None
        monday = today - timedelta(days=today.weekday())
        weeks = {monday - timedelta(weeks=i): [0, 0] for i in range(11, -1, -1)}
        for status, type_id, kind, created_at, decided_at in rows:
            status_counts[status] += 1
            by_type[type_id] = by_type.get(type_id, 0) + 1
            by_kind[kind] = by_kind.get(kind, 0) + 1
            created = local(created_at)
            if status == ApplicationStatus.SUBMITTED:
                oldest = max(oldest or 0, (today - created).days)
            if decided_at is not None and status in (ApplicationStatus.APPROVED, ApplicationStatus.REJECTED):
                hours.append((decided_at - created_at).total_seconds() / 3600)
            wk = created - timedelta(days=created.weekday())
            if wk in weeks:
                weeks[wk][0] += 1
            if decided_at is not None and status in (ApplicationStatus.APPROVED, ApplicationStatus.REJECTED):
                dk = local(decided_at)
                dk = dk - timedelta(days=dk.weekday())
                if dk in weeks:
                    weeks[dk][1] += 1
        decided = status_counts[ApplicationStatus.APPROVED] + status_counts[ApplicationStatus.REJECTED]
        labels = {ApplicationStatus.SUBMITTED: "Waiting", ApplicationStatus.UNDER_REVIEW: "Under review", ApplicationStatus.MORE_INFO_NEEDED: "Needs more information",
                  ApplicationStatus.APPROVED: "Approved", ApplicationStatus.REJECTED: "Rejected", ApplicationStatus.WITHDRAWN: "Withdrawn"}
        return {
            "kpis": {"total": len(rows), "needs_action": status_counts[ApplicationStatus.SUBMITTED], "under_review": status_counts[ApplicationStatus.UNDER_REVIEW],
                     "more_info": status_counts[ApplicationStatus.MORE_INFO_NEEDED], "approved": status_counts[ApplicationStatus.APPROVED],
                     "rejected": status_counts[ApplicationStatus.REJECTED], "withdrawn": status_counts[ApplicationStatus.WITHDRAWN],
                     "approval_rate": round(status_counts[ApplicationStatus.APPROVED] / decided * 100, 1) if decided else None,
                     "avg_decision_hours": round(sum(hours) / len(hours), 1) if hours else None, "oldest_waiting_days": oldest},
            "by_status": [{"key": s.value, "label": labels[s], "count": n} for s, n in status_counts.items()],
            "by_type": sorted(({"key": str(t), "label": names.get(t, "?"), "count": n} for t, n in by_type.items()), key=lambda x: -x["count"]),
            "by_kind": [{"key": k.value, "label": "Students" if k == ApplicantKind.STUDENT else "Employees", "count": n} for k, n in by_kind.items()],
            "by_week": [{"week_start": wk, "submitted": v[0], "decided": v[1]} for wk, v in weeks.items()],
        }