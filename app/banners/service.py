"""Top bars (see models.py)."""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.banners.models import TONES, TopBanner, TopBannerSeen
from app.core.exceptions import ResourceNotFoundError, ValidationAppError

ROLE_LABELS = {
    "STUDENT": "All students", "PARENT": "All parents / guardians", "TEACHER": "All teachers", "HOD": "Heads of department",
    "PRINCIPAL": "Principal", "ACCOUNTS_OFFICER": "Accounts office", "LIBRARIAN": "Library", "EXAM_CONTROLLER": "Exam department",
    "HOSTEL_WARDEN": "Hostel wardens", "SECURITY_GUARD": "Gate / security", "STAFF": "General staff", "ADMIN": "Administrators",
}
STAFF_ROLES = ["TEACHER", "HOD", "PRINCIPAL", "ACCOUNTS_OFFICER", "LIBRARIAN", "EXAM_CONTROLLER", "HOSTEL_WARDEN", "SECURITY_GUARD", "STAFF"]
MAX_PEOPLE = 3000
MAX_SHOWN = 3


def clean_url(url: str | None) -> str | None:
    """A web address (https:// or http://) or a page of this site (/dashboard/...). Never javascript:, data: and the like."""
    url = (url or "").strip()
    if not url:
        return None
    if re.match(r"^/(?!/)[^\s]*$", url):
        return url[:500]
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url                     # "forms.google.com/..." typed without the scheme
    if not re.match(r"^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(:\d+)?([/?#][^\s]*)?$", url) or len(url) > 500:
        raise ValidationAppError("The button link must be a web address (https://...) or a page of this site (/dashboard/...).")
    return url


def clean_audience(raw: dict | None) -> dict:
    raw = raw or {}
    roles = [r for r in (raw.get("roles") or []) if r in ROLE_LABELS]
    program_ids = []
    for p in raw.get("program_ids") or []:
        try:
            program_ids.append(str(uuid.UUID(str(p))))
        except ValueError:
            continue
    semesters = sorted({int(s) for s in (raw.get("semesters") or []) if str(s).isdigit() and 1 <= int(s) <= 12})
    user_ids = []
    for u in raw.get("user_ids") or []:
        try:
            user_ids.append(str(uuid.UUID(str(u))))
        except ValueError:
            continue
    user_ids = list(dict.fromkeys(user_ids))
    if len(user_ids) > MAX_PEOPLE:
        raise ValidationAppError(f"At most {MAX_PEOPLE} named people per bar; use roles or programs for larger groups.")
    out = {"everyone": bool(raw.get("everyone")), "roles": roles, "program_ids": program_ids, "semesters": semesters, "user_ids": user_ids}
    if not (out["everyone"] or roles or program_ids or user_ids):
        raise ValidationAppError("Choose who sees this bar: everyone, a group, a program or named people.")
    return out


class BannerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ who sees it
    async def _student_of(self, user_id: uuid.UUID):
        from app.students.models import Student

        return (await self.session.execute(select(Student.program_id, Student.current_semester).where(Student.user_id == user_id))).first()

    @staticmethod
    def _live(b: TopBanner, now: datetime) -> bool:
        return b.is_active and (b.starts_at is None or b.starts_at <= now) and (b.ends_at is None or b.ends_at > now)

    def _matches(self, b: TopBanner, user_id: uuid.UUID, roles: list[str], student) -> bool:
        a = b.audience or {}
        if a.get("everyone"):
            return True
        if set(a.get("roles") or []) & set(roles):
            return True
        if str(user_id) in (a.get("user_ids") or []):
            return True
        if student and a.get("program_ids") and student[0] and str(student[0]) in a["program_ids"]:
            return not a.get("semesters") or (student[1] in a["semesters"])
        if student and not a.get("program_ids") and a.get("semesters") and student[1] in a["semesters"]:
            return True
        return False

    async def mine(self, user_id: uuid.UUID, roles: list[str]) -> list[dict]:
        now = datetime.now(timezone.utc)
        bars = (await self.session.execute(select(TopBanner).where(TopBanner.is_active.is_(True)).order_by(TopBanner.position, TopBanner.created_at.desc()))).scalars().all()
        bars = [b for b in bars if self._live(b, now)]
        if not bars:
            return []
        closed = set((await self.session.execute(select(TopBannerSeen.banner_id).where(
            TopBannerSeen.user_id == user_id, TopBannerSeen.dismissed_at.is_not(None), TopBannerSeen.banner_id.in_([b.id for b in bars])))).scalars().all())
        student = await self._student_of(user_id) if "STUDENT" in roles else None
        out = []
        for b in bars:
            if b.id in closed and b.dismissible:
                continue
            if self._matches(b, user_id, roles, student):
                out.append({"id": str(b.id), "message": b.message, "button_label": b.button_label if b.button_url else None, "button_url": b.button_url,
                            "new_tab": b.new_tab, "tone": b.tone, "dismissible": b.dismissible})
            if len(out) >= MAX_SHOWN:
                break
        return out

    async def _mark(self, banner_id: uuid.UUID, user_id: uuid.UUID, *, click: bool) -> None:
        if await self.session.get(TopBanner, banner_id) is None:
            raise ResourceNotFoundError("That bar no longer exists.")
        row = await self.session.get(TopBannerSeen, (banner_id, user_id))
        if row is None:
            row = TopBannerSeen(banner_id=banner_id, user_id=user_id, clicks=0)
            self.session.add(row)
        now = datetime.now(timezone.utc)
        if click:
            row.clicks = (row.clicks or 0) + 1
            row.clicked_at = row.clicked_at or now
        else:
            row.dismissed_at = now
        await self.session.flush()

    async def clicked(self, banner_id, user_id) -> None:
        await self._mark(banner_id, user_id, click=True)

    async def dismissed(self, banner_id, user_id) -> None:
        await self._mark(banner_id, user_id, click=False)

    # ------------------------------------------------------------------ administration
    def _status(self, b: TopBanner, now: datetime) -> str:
        if not b.is_active:
            return "HIDDEN"
        if b.starts_at and b.starts_at > now:
            return "SCHEDULED"
        if b.ends_at and b.ends_at <= now:
            return "ENDED"
        return "SHOWING"

    async def _audience_size(self, a: dict) -> int:
        """How many accounts the audience reaches (active accounts only)."""
        from app.auth.models import Role, User, UserRole
        from app.students.models import Student

        if a.get("everyone"):
            return (await self.session.execute(select(func.count()).select_from(User).where(User.is_active.is_(True)))).scalar_one()
        conds = []
        if a.get("roles"):
            conds.append(User.id.in_(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.code.in_(a["roles"]))))
        if a.get("user_ids"):
            conds.append(User.id.in_([uuid.UUID(u) for u in a["user_ids"]]))
        if a.get("program_ids") or a.get("semesters"):
            s = select(Student.user_id).where(Student.user_id.is_not(None))
            if a.get("program_ids"):
                s = s.where(Student.program_id.in_([uuid.UUID(p) for p in a["program_ids"]]))
            if a.get("semesters"):
                s = s.where(Student.current_semester.in_(a["semesters"]))
            conds.append(User.id.in_(s))
        if not conds:
            return 0
        return (await self.session.execute(select(func.count()).select_from(User).where(User.is_active.is_(True), or_(*conds)))).scalar_one()

    async def _out(self, b: TopBanner, now: datetime, stats: dict) -> dict:
        clicked, closed = stats.get(b.id, (0, 0))
        return {"id": str(b.id), "name": b.name, "message": b.message, "button_label": b.button_label, "button_url": b.button_url,
                "new_tab": b.new_tab, "tone": b.tone, "audience": b.audience, "audience_label": await self._audience_label(b.audience),
                "reach": await self._audience_size(b.audience), "starts_at": b.starts_at.isoformat() if b.starts_at else None,
                "ends_at": b.ends_at.isoformat() if b.ends_at else None, "is_active": b.is_active, "dismissible": b.dismissible,
                "position": b.position, "status": self._status(b, now), "clicked": clicked, "closed": closed,
                "updated_at": b.updated_at.isoformat() if b.updated_at else None}

    async def _audience_label(self, a: dict) -> str:
        from app.programs.models import Program

        if a.get("everyone"):
            return "Everyone"
        parts = [ROLE_LABELS[r] for r in a.get("roles") or [] if r in ROLE_LABELS]
        if a.get("program_ids"):
            names = (await self.session.execute(select(Program.code).where(Program.id.in_([uuid.UUID(p) for p in a["program_ids"]])))).scalars().all()
            sem = (" semester " + ", ".join(str(s) for s in a["semesters"])) if a.get("semesters") else ""
            parts.append("Students of " + ", ".join(sorted(names)) + sem)
        elif a.get("semesters"):
            parts.append("Students in semester " + ", ".join(str(s) for s in a["semesters"]))
        if a.get("user_ids"):
            parts.append(f"{len(a['user_ids'])} named {'person' if len(a['user_ids']) == 1 else 'people'}")
        return " · ".join(parts) or "Nobody"

    async def _stats(self, ids: list) -> dict:
        if not ids:
            return {}
        rows = (await self.session.execute(select(TopBannerSeen.banner_id, func.count(TopBannerSeen.clicked_at), func.count(TopBannerSeen.dismissed_at))
                                           .where(TopBannerSeen.banner_id.in_(ids)).group_by(TopBannerSeen.banner_id))).all()
        return {r[0]: (r[1], r[2]) for r in rows}

    async def listing(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        bars = (await self.session.execute(select(TopBanner).order_by(TopBanner.is_active.desc(), TopBanner.position, TopBanner.created_at.desc()))).scalars().all()
        stats = await self._stats([b.id for b in bars])
        return [await self._out(b, now, stats) for b in bars]

    async def get(self, banner_id: uuid.UUID) -> TopBanner:
        b = await self.session.get(TopBanner, banner_id)
        if b is None:
            raise ResourceNotFoundError("That bar was not found.")
        return b

    def _apply(self, b: TopBanner, data: dict) -> None:
        if "name" in data:
            b.name = " ".join((data["name"] or "").split())[:120] or (data.get("message") or b.message or "Top bar")[:60]
        if "message" in data:
            msg = " ".join((data["message"] or "").split())
            if len(msg) < 3:
                raise ValidationAppError("Write the message people will see.")
            b.message = msg[:300]
        if "button_url" in data:
            b.button_url = clean_url(data["button_url"])
        if "button_label" in data:
            b.button_label = " ".join((data["button_label"] or "").split())[:40] or None
        if b.button_url and not b.button_label:
            b.button_label = "Open"
        if b.button_label and not b.button_url:
            raise ValidationAppError("Add the link the button opens (or leave the button text empty for no button).")
        if "tone" in data:
            if data["tone"] not in TONES:
                raise ValidationAppError("Choose one of the colours.")
            b.tone = data["tone"]
        if "audience" in data:
            b.audience = clean_audience(data["audience"])
        for k in ("new_tab", "dismissible", "is_active"):
            if k in data and data[k] is not None:
                setattr(b, k, bool(data[k]))
        if "position" in data and data["position"] is not None:
            b.position = max(0, min(int(data["position"]), 999))
        if "starts_at" in data:
            b.starts_at = data["starts_at"]
        if "ends_at" in data:
            b.ends_at = data["ends_at"]
        if b.starts_at and b.ends_at and b.ends_at <= b.starts_at:
            raise ValidationAppError("The bar must end after it starts.")

    async def create(self, data: dict, *, actor_id) -> dict:
        from app.audit.service import AuditService

        b = TopBanner(name="", message="", audience={}, created_by=actor_id, updated_by=actor_id)
        data = {**data}
        data.setdefault("name", "")
        self._apply(b, data)
        if not b.message:
            raise ValidationAppError("Write the message people will see.")
        if not b.audience:
            raise ValidationAppError("Choose who sees this bar.")
        self.session.add(b)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="banner.created", entity_type="top_banner", entity_id=b.id,
                                             after={"name": b.name, "message": b.message, "button_url": b.button_url, "audience": b.audience, "is_active": b.is_active})
        return await self._out(b, datetime.now(timezone.utc), {})

    async def update(self, banner_id: uuid.UUID, data: dict, *, actor_id) -> dict:
        from app.audit.service import AuditService

        b = await self.get(banner_id)
        before = {"message": b.message, "button_url": b.button_url, "audience": b.audience, "is_active": b.is_active}
        self._apply(b, data)
        b.updated_by = actor_id
        await self.session.flush()
        after = {"message": b.message, "button_url": b.button_url, "audience": b.audience, "is_active": b.is_active}
        action = "banner.shown" if data.keys() == {"is_active"} and b.is_active else "banner.hidden" if data.keys() == {"is_active"} else "banner.updated"
        await AuditService(self.session).log(actor_user_id=actor_id, action=action, entity_type="top_banner", entity_id=b.id, before=before, after=after)
        return await self._out(b, datetime.now(timezone.utc), await self._stats([b.id]))

    async def delete(self, banner_id: uuid.UUID, *, actor_id) -> None:
        from app.audit.service import AuditService

        b = await self.get(banner_id)
        await AuditService(self.session).log(actor_user_id=actor_id, action="banner.deleted", entity_type="top_banner", entity_id=b.id,
                                             before={"name": b.name, "message": b.message, "button_url": b.button_url})
        await self.session.delete(b)
        await self.session.flush()

    async def responses(self, banner_id: uuid.UUID) -> list[dict]:
        from app.notifications.email_service import _person_label

        await self.get(banner_id)
        rows = (await self.session.execute(select(TopBannerSeen).where(TopBannerSeen.banner_id == banner_id)
                                           .order_by(TopBannerSeen.clicked_at.desc().nulls_last(), TopBannerSeen.dismissed_at.desc()))).scalars().all()
        return [{"person": await _person_label(self.session, r.user_id), "user_id": str(r.user_id), "clicks": r.clicks,
                 "clicked_at": r.clicked_at.isoformat() if r.clicked_at else None, "dismissed_at": r.dismissed_at.isoformat() if r.dismissed_at else None}
                for r in rows]

    async def responses_csv(self, banner_id: uuid.UUID) -> tuple[str, str]:
        from app.core.clock import institute_now
        from app.employees.router import _csv_safe

        b = await self.get(banner_id)
        tz = institute_now().tzinfo
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Person", "Clicked the button", "Times clicked", "First clicked at", "Closed the bar at"])
        for r in await self.responses(banner_id):
            fmt = lambda v: datetime.fromisoformat(v).astimezone(tz).strftime("%Y-%m-%d %H:%M") if v else ""  # noqa: E731
            w.writerow([_csv_safe(r["person"]), "Yes" if r["clicked_at"] else "No", r["clicks"], fmt(r["clicked_at"]), fmt(r["dismissed_at"])])
        slug = re.sub(r"[^a-z0-9]+", "-", b.name.lower()).strip("-")[:40] or "top-bar"
        return "﻿" + out.getvalue(), f"top-bar-{slug}.csv"

    async def people(self, q: str) -> list[dict]:
        from app.employees.models import Employee
        from app.students.models import Student

        q = (q or "").strip().lower()
        if len(q) < 2:
            return []
        like = f"%{q}%"
        out = [{"user_id": str(s.user_id), "name": f"{s.first_name} {s.last_name}", "detail": f"Student {s.roll_number}"} for s in (await self.session.execute(
            select(Student).where(Student.user_id.is_not(None), or_(func.lower(Student.first_name + " " + Student.last_name).like(like),
                                                                    func.lower(Student.roll_number).like(like))).order_by(Student.roll_number).limit(15))).scalars()]
        out += [{"user_id": str(e.user_id), "name": f"{e.first_name} {e.last_name}", "detail": f"Staff {e.employee_code}"} for e in (await self.session.execute(
            select(Employee).where(Employee.user_id.is_not(None), or_(func.lower(Employee.first_name + " " + Employee.last_name).like(like),
                                                                      func.lower(Employee.employee_code).like(like))).order_by(Employee.employee_code).limit(15))).scalars()]
        return out

    async def names(self, user_ids: list[str]) -> list[dict]:
        from app.notifications.email_service import _person_label

        out = []
        for u in user_ids[:MAX_PEOPLE]:
            try:
                out.append({"user_id": u, "name": await _person_label(self.session, uuid.UUID(u))})
            except ValueError:
                continue
        return out