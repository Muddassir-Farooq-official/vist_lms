"""Committees, meetings, minutes and action items (models.py)."""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.committees.models import ActionItem, Committee, CommitteeMeeting, CommitteeMember
from app.core.clock import institute_today
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError

ROLES = {"CHAIR": "Chair", "SECRETARY": "Secretary", "MEMBER": "Member"}
REMIND_FROM_HOUR = 9


def _now():
    return datetime.now(timezone.utc)


async def _names(session: AsyncSession, user_ids) -> dict:
    """Display names for users: the employee's name when there is one, else the username."""
    from app.auth.models import User
    from app.employees.models import Employee

    ids = list({u for u in user_ids if u})
    if not ids:
        return {}
    out = {u.id: u.username for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()}
    for e in (await session.execute(select(Employee).where(Employee.user_id.in_(ids)))).scalars():
        out[e.user_id] = f"{e.first_name} {e.last_name}"
    return out


async def _notify(session: AsyncSession, user_ids, *, title: str, message: str, link: str) -> None:
    """Never raises."""
    try:
        async with session.begin_nested():
            from app.notifications.models import NotificationCategory
            from app.notifications.service import create_notification

            for uid in set(user_ids):
                await create_notification(session, user_id=uid, category=NotificationCategory.GENERAL, title=title, message=message, link=link)
    except Exception:                         # noqa: BLE001
        pass


class CommitteeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ who may do what
    async def _role(self, committee_id: uuid.UUID, user) -> str | None:
        m = await self.session.get(CommitteeMember, (committee_id, user.id))
        return m.role if m else None

    async def _must_see(self, committee_id: uuid.UUID, user, *, admin: bool) -> str | None:
        role = await self._role(committee_id, user)
        if role is None and not admin:
            raise AuthorizationError("Only the committee's members can see this.")
        return role

    async def _must_run(self, committee_id: uuid.UUID, user, *, admin: bool) -> None:
        role = await self._role(committee_id, user)
        if not admin and role not in ("CHAIR", "SECRETARY"):
            raise AuthorizationError("Only the chair or secretary can do this.")

    # ------------------------------------------------------------------ committees (administration)
    async def save(self, data: dict, *, committee_id: uuid.UUID | None, actor_id: uuid.UUID) -> dict:
        name = " ".join((data.get("name") or "").split())
        if len(name) < 3:
            raise ValidationAppError("Give the committee a name.")
        clash = (await self.session.execute(select(Committee).where(func.lower(Committee.name) == name.lower()))).scalar_one_or_none()
        if clash and clash.id != committee_id:
            raise DuplicateResourceError("A committee with that name exists.")
        c = await self.session.get(Committee, committee_id) if committee_id else None
        if committee_id and c is None:
            raise ResourceNotFoundError("Committee not found.")
        members = data.get("members")
        checked = await self._check_members(members) if members is not None else None
        if c is None and not checked:
            raise ValidationAppError("Add the members, with one chair.")
        if c is None:
            c = Committee(name=name, created_by=actor_id)
            self.session.add(c)
        c.name, c.purpose = name, (data.get("purpose") or "").strip()[:1000] or None
        if "is_active" in data and data["is_active"] is not None:
            c.is_active = bool(data["is_active"])
        await self.session.flush()
        if checked is not None:
            await self.session.execute(delete(CommitteeMember).where(CommitteeMember.committee_id == c.id))
            for uid, role in checked.items():
                self.session.add(CommitteeMember(committee_id=c.id, user_id=uid, role=role))
            await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="committee.saved", entity_type="committee", entity_id=c.id,
                                             metadata={"name": c.name, "members": len(members) if members is not None else None})
        return {"id": c.id}

    async def _check_members(self, members: list[dict]) -> dict:
        """{user_id: role}, checked: exactly one chair, active staff logins only."""
        from app.auth.models import User

        seen = {}
        for m in members:
            uid, role = uuid.UUID(str(m["user_id"])), str(m.get("role") or "MEMBER").upper()
            if role not in ROLES:
                raise ValidationAppError("Chair, secretary or member.")
            seen[uid] = role
        if list(seen.values()).count("CHAIR") != 1:
            raise ValidationAppError("A committee has exactly one chair.")
        users = set((await self.session.execute(select(User.id).where(User.id.in_(list(seen) or [uuid.uuid4()]), User.is_active.is_(True)))).scalars())
        if set(seen) - users:
            raise ValidationAppError("A chosen member has no active login.")
        from app.parents.models import ParentStudentLink as Parent
        from app.students.models import Student

        outsiders = set((await self.session.execute(select(Student.user_id).where(Student.user_id.in_(list(seen))))).scalars()) | \
            set((await self.session.execute(select(Parent.user_id).where(Parent.user_id.in_(list(seen))))).scalars())
        if outsiders:
            raise ValidationAppError("Committee members are staff; a student or parent login cannot be added.")
        return seen

    async def committees(self, user, *, admin: bool) -> list[dict]:
        q = select(Committee).order_by(Committee.is_active.desc(), Committee.name)
        if not admin:
            q = q.join(CommitteeMember, CommitteeMember.committee_id == Committee.id).where(CommitteeMember.user_id == user.id)
        rows = (await self.session.execute(q)).scalars().all()
        ids = [c.id for c in rows]
        members = (await self.session.execute(select(CommitteeMember).where(CommitteeMember.committee_id.in_(ids or [uuid.uuid4()])))).scalars().all()
        names = await _names(self.session, [m.user_id for m in members])
        from app.employees.models import Employee

        codes = dict((await self.session.execute(select(Employee.user_id, Employee.employee_code).where(Employee.user_id.in_([m.user_id for m in members] or [uuid.uuid4()]))))
                     .all()) if admin else {}
        by_c: dict = {}
        for m in members:
            by_c.setdefault(m.committee_id, []).append({"user_id": m.user_id, "name": names.get(m.user_id), "role": m.role, "role_name": ROLES[m.role],
                                                        **({"employee_code": codes.get(m.user_id)} if admin else {})})
        nxt = dict((await self.session.execute(select(CommitteeMeeting.committee_id, func.min(CommitteeMeeting.held_on)).where(
            CommitteeMeeting.committee_id.in_(ids or [uuid.uuid4()]), CommitteeMeeting.held_on >= institute_today(), CommitteeMeeting.status == "SCHEDULED")
            .group_by(CommitteeMeeting.committee_id))).all())
        out = []
        for c in rows:
            ms = sorted(by_c.get(c.id, []), key=lambda x: (list(ROLES).index(x["role"]), x["name"] or ""))
            mine = next((m["role"] for m in ms if m["user_id"] == user.id), None)
            out.append({"id": c.id, "name": c.name, "purpose": c.purpose, "is_active": c.is_active, "members": ms, "my_role": mine,
                        "can_run": admin or mine in ("CHAIR", "SECRETARY"), "next_meeting": nxt.get(c.id)})
        return out

    # ------------------------------------------------------------------ meetings
    async def schedule(self, committee_id: uuid.UUID, data: dict, *, user, admin: bool) -> dict:
        await self._must_run(committee_id, user, admin=admin)
        c = await self.session.get(Committee, committee_id)
        if c is None or not c.is_active:
            raise BusinessRuleViolationError("This committee is closed.")
        agenda = (data.get("agenda") or "").strip()
        if len(agenda) < 5:
            raise ValidationAppError("Write the agenda.")
        m = CommitteeMeeting(committee_id=c.id, held_on=data["held_on"], start_time=data.get("start_time"), place=(data.get("place") or "").strip()[:120] or None,
                             agenda=agenda[:20000], created_by=user.id)
        self.session.add(m)
        await self.session.flush()
        if m.held_on >= institute_today():
            members = (await self.session.execute(select(CommitteeMember.user_id).where(CommitteeMember.committee_id == c.id))).scalars().all()
            when = f"{m.held_on:%a %d %b}" + (f" at {m.start_time:%H:%M}" if m.start_time else "") + (f", {m.place}" if m.place else "")
            await _notify(self.session, [u for u in members if u != user.id], title=f"{c.name}: meeting on {m.held_on:%d %b}",
                          message=when, link="/dashboard/my-committees")
        return {"id": m.id}

    async def meetings(self, committee_id: uuid.UUID, user, *, admin: bool) -> list[dict]:
        await self._must_see(committee_id, user, admin=admin)
        rows = (await self.session.execute(select(CommitteeMeeting).where(CommitteeMeeting.committee_id == committee_id)
                                           .order_by(CommitteeMeeting.held_on.desc()))).scalars().all()
        counts = dict((await self.session.execute(select(ActionItem.meeting_id, func.count()).where(ActionItem.meeting_id.in_([m.id for m in rows] or [uuid.uuid4()]),
                                                                                                    ActionItem.status == "OPEN").group_by(ActionItem.meeting_id))).all())
        return [{"id": m.id, "held_on": m.held_on, "start_time": m.start_time.strftime("%H:%M") if m.start_time else None, "place": m.place, "status": m.status,
                 "open_actions": int(counts.get(m.id, 0))} for m in rows]

    async def meeting(self, meeting_id: uuid.UUID, user, *, admin: bool) -> dict:
        m = await self.session.get(CommitteeMeeting, meeting_id)
        if m is None:
            raise ResourceNotFoundError("Meeting not found.")
        role = await self._must_see(m.committee_id, user, admin=admin)
        c = await self.session.get(Committee, m.committee_id)
        actions = (await self.session.execute(select(ActionItem).where(ActionItem.meeting_id == m.id).order_by(ActionItem.due_on))).scalars().all()
        names = await _names(self.session, [a.owner_id for a in actions] + [m.finalised_by])
        return {"id": m.id, "committee_id": c.id, "committee": c.name, "held_on": m.held_on, "start_time": m.start_time.strftime("%H:%M") if m.start_time else None,
                "place": m.place, "agenda": m.agenda, "minutes": m.minutes, "decisions": m.decisions, "attended": m.attended, "status": m.status,
                "finalised_by": names.get(m.finalised_by), "finalised_at": m.finalised_at,
                "can_edit": m.status == "SCHEDULED" and (admin or role in ("CHAIR", "SECRETARY")), "can_finalise": m.status == "SCHEDULED" and (admin or role == "CHAIR"),
                "actions": [self._a(a, names) for a in actions]}

    @staticmethod
    def _a(a: ActionItem, names: dict) -> dict:
        today = institute_today()
        return {"id": a.id, "title": a.title, "owner_id": a.owner_id, "owner": names.get(a.owner_id), "due_on": a.due_on, "status": a.status,
                "overdue": a.status == "OPEN" and a.due_on < today, "done_note": a.done_note, "done_at": a.done_at}

    async def write_minutes(self, meeting_id: uuid.UUID, data: dict, *, user, admin: bool) -> dict:
        m = await self.session.get(CommitteeMeeting, meeting_id)
        if m is None:
            raise ResourceNotFoundError("Meeting not found.")
        await self._must_run(m.committee_id, user, admin=admin)
        if m.status != "SCHEDULED":
            raise BusinessRuleViolationError("Final minutes can no longer change.")
        for k in ("agenda", "minutes", "decisions", "attended", "place"):
            if k in data and data[k] is not None:
                setattr(m, k, data[k].strip()[:20000] or (m.agenda if k == "agenda" else None))
        if data.get("held_on"):
            m.held_on = data["held_on"]
        if "start_time" in data:
            m.start_time = data["start_time"]
        await self.session.flush()
        return {"id": m.id}

    async def finalise(self, meeting_id: uuid.UUID, *, user, admin: bool) -> dict:
        m = await self.session.get(CommitteeMeeting, meeting_id)
        if m is None:
            raise ResourceNotFoundError("Meeting not found.")
        role = await self._role(m.committee_id, user)
        if not admin and role != "CHAIR":
            raise AuthorizationError("Only the chair makes the minutes final.")
        if m.status != "SCHEDULED":
            raise BusinessRuleViolationError("These minutes are already final.")
        if len((m.minutes or "").strip()) < 10:
            raise BusinessRuleViolationError("Write the minutes first.")
        if m.held_on > institute_today():
            raise BusinessRuleViolationError("The meeting has not happened yet.")
        m.status, m.finalised_by, m.finalised_at = "FINAL", user.id, _now()
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=user.id, action="committee.minutes_final", entity_type="committee_meeting", entity_id=m.id)
        c = await self.session.get(Committee, m.committee_id)
        members = (await self.session.execute(select(CommitteeMember.user_id).where(CommitteeMember.committee_id == c.id))).scalars().all()
        await _notify(self.session, [u for u in members if u != user.id], title=f"{c.name}: minutes of {m.held_on:%d %b} are final",
                      message="Read them under Committees.", link="/dashboard/my-committees")
        return {"id": m.id, "status": m.status}

    async def cancel(self, meeting_id: uuid.UUID, *, user, admin: bool) -> dict:
        m = await self.session.get(CommitteeMeeting, meeting_id)
        if m is None:
            raise ResourceNotFoundError("Meeting not found.")
        await self._must_run(m.committee_id, user, admin=admin)
        if m.status != "SCHEDULED":
            raise BusinessRuleViolationError("Only a scheduled meeting can be cancelled.")
        if (await self.session.execute(select(ActionItem.id).where(ActionItem.meeting_id == m.id))).first():
            raise BusinessRuleViolationError("This meeting has action items; finish its minutes instead.")
        m.status = "CANCELLED"
        await self.session.flush()
        return {"id": m.id, "status": m.status}

    # ------------------------------------------------------------------ action items
    async def add_action(self, meeting_id: uuid.UUID, *, title: str, owner_id: uuid.UUID, due_on: date, user, admin: bool) -> dict:
        from app.auth.models import User

        m = await self.session.get(CommitteeMeeting, meeting_id)
        if m is None:
            raise ResourceNotFoundError("Meeting not found.")
        await self._must_run(m.committee_id, user, admin=admin)
        if m.status == "CANCELLED":
            raise BusinessRuleViolationError("This meeting was cancelled.")
        title = " ".join((title or "").split())
        if len(title) < 3:
            raise ValidationAppError("Say what must be done.")
        owner = await self.session.get(User, owner_id)
        if owner is None or not owner.is_active:
            raise ValidationAppError("Give it to someone with an active login.")
        a = ActionItem(meeting_id=m.id, title=title[:300], owner_id=owner.id, due_on=due_on)
        self.session.add(a)
        await self.session.flush()
        c = await self.session.get(Committee, m.committee_id)
        await _notify(self.session, [owner.id], title=f"{c.name}: an action for you", message=f"{a.title} — by {a.due_on:%d %b}",
                      link="/dashboard/my-committees")
        return {"id": a.id}

    async def update_action(self, action_id: uuid.UUID, *, status: str, note: str | None, due_on: date | None, user, admin: bool) -> dict:
        a = await self.session.get(ActionItem, action_id)
        if a is None:
            raise ResourceNotFoundError("Action not found.")
        m = await self.session.get(CommitteeMeeting, a.meeting_id)
        runner = admin or (await self._role(m.committee_id, user)) in ("CHAIR", "SECRETARY")
        if a.owner_id != user.id and not runner:
            raise AuthorizationError("Only its owner, the chair or secretary can change this action.")
        if status not in ("OPEN", "DONE", "DROPPED"):
            raise ValidationAppError("Open, done or dropped.")
        if status == "DROPPED" and not runner:
            raise AuthorizationError("Only the chair or secretary can drop an action.")
        if due_on is not None:
            if not runner:
                raise AuthorizationError("Only the chair or secretary can change the due date.")
            a.due_on = due_on
        a.status = status
        a.done_note = (note or "").strip()[:500] or a.done_note
        a.done_at = _now() if status == "DONE" else None
        await self.session.flush()
        return self._a(a, await _names(self.session, [a.owner_id]))

    async def my_actions(self, user) -> list[dict]:
        rows = (await self.session.execute(select(ActionItem, CommitteeMeeting, Committee).join(CommitteeMeeting, CommitteeMeeting.id == ActionItem.meeting_id)
                                           .join(Committee, Committee.id == CommitteeMeeting.committee_id)
                                           .where(ActionItem.owner_id == user.id).order_by(ActionItem.status != "OPEN", ActionItem.due_on).limit(200))).all()
        names = {user.id: None}
        return [{**self._a(a, names), "committee": c.name, "meeting_on": m.held_on} for a, m, c in rows]

    async def all_open_actions(self) -> list[dict]:
        rows = (await self.session.execute(select(ActionItem, CommitteeMeeting, Committee).join(CommitteeMeeting, CommitteeMeeting.id == ActionItem.meeting_id)
                                           .join(Committee, Committee.id == CommitteeMeeting.committee_id).where(ActionItem.status == "OPEN")
                                           .order_by(ActionItem.due_on).limit(500))).all()
        names = await _names(self.session, [a.owner_id for a, _m, _c in rows])
        return [{**self._a(a, names), "committee": c.name, "meeting_on": m.held_on} for a, m, c in rows]

    # ------------------------------------------------------------------ the minutes as a PDF
    async def minutes_pdf(self, meeting_id: uuid.UUID, user, *, admin: bool) -> bytes:
        from xml.sax.saxutils import escape

        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        from app.core import brand

        d = await self.meeting(meeting_id, user, admin=admin)
        buf = io.BytesIO()
        width = A4[0] - 36 * mm
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                                title=f"{d['committee']} minutes {d['held_on']}", author=brand.name())
        base = getSampleStyleSheet()
        body = ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica", fontSize=10.5, leading=14.5)
        head = ParagraphStyle("h", parent=body, fontName="Helvetica-Bold", fontSize=11.5, spaceBefore=8, spaceAfter=3)

        def para(text):
            return Paragraph(escape(text or "—").replace("\n", "<br/>"), body)

        story = brand.platypus_header(width) + [Spacer(1, 4), Paragraph(f"<b>{escape(d['committee'])}</b> — minutes of the meeting of {d['held_on']:%d %B %Y}"
                                                                        + (f" at {d['start_time']}" if d["start_time"] else "")
                                                                        + (f", {escape(d['place'])}" if d["place"] else "")
                                                                        + ("" if d["status"] == "FINAL" else " (DRAFT)"), head)]
        story += [Paragraph("Present", head), para(d["attended"]), Paragraph("Agenda", head), para(d["agenda"]), Paragraph("Minutes", head), para(d["minutes"]),
                  Paragraph("Decisions", head), para(d["decisions"])]
        if d["actions"]:
            story.append(Paragraph("Actions", head))
            for a in d["actions"]:
                story.append(para(f"• {a['title']} — {a['owner']}, by {a['due_on']:%d %b %Y} ({a['status'].lower()})"))
        if d["status"] == "FINAL":
            story += [Spacer(1, 14), Paragraph(f"Made final by {escape(d['finalised_by'] or '')} on {d['finalised_at']:%d %b %Y}.", body)]
        doc.build(story)
        return buf.getvalue()


async def remind_actions(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Once a day from 9 AM: each open action due tomorrow, today or already overdue reminds its owner (once a day). Never raises."""
    try:
        async with session.begin_nested():
            from zoneinfo import ZoneInfo

            from app.core.config import settings

            local = (now or _now()).astimezone(ZoneInfo(getattr(settings, "institute_timezone", "Asia/Karachi") or "Asia/Karachi"))
            if local.hour < REMIND_FROM_HOUR:
                return 0
            today = local.date()
            rows = (await session.execute(select(ActionItem, CommitteeMeeting, Committee).join(CommitteeMeeting, CommitteeMeeting.id == ActionItem.meeting_id)
                                          .join(Committee, Committee.id == CommitteeMeeting.committee_id)
                                          .where(ActionItem.status == "OPEN", ActionItem.due_on <= today + timedelta(days=1),
                                                 (ActionItem.reminded_on.is_(None)) | (ActionItem.reminded_on < today)).limit(500))).all()
            for a, _m, c in rows:
                late = a.due_on < today
                when = "overdue since" if late else ("due today" if a.due_on == today else "due tomorrow")
                await _notify(session, [a.owner_id], title=f"{c.name}: action {when}" + (f" {a.due_on:%d %b}" if late else ""), message=a.title,
                              link="/dashboard/my-committees")
                a.reminded_on = today
            await session.flush()
            return len(rows)
    except Exception:                         # noqa: BLE001
        return 0
