"""Settings -> Access control: what each role can do, custom roles, one person's own rights, history and undo.

Safety rules (all enforced here, not only on the screen):
  - The Administrator role is never edited, always holds everything, and the last active Administrator can never lose
    it (app/employees/access_roles.py). Rights given to or taken from one person never apply to an Administrator.
  - Access control itself and "View as" (LOCKED) stay with the Administrator role.
  - Rights that only mean something for the person themselves (OWN_USE) are not handed out; a built-in role keeps the
    ones it comes with.
  - Nobody changes their own access.
  - Giving money or control rights (SENSITIVE), or the Administrator role, waits for a second administrator to confirm.
    When there is no other administrator it goes ahead and is logged as a sole-administrator decision.
  - Every change is kept with what it was before (access_changes) and can be undone.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import catalog
from app.access.models import AccessChange, RolePermissionRemoval, UserAccessOverride
from app.access.resolve import combine, effective, users_with_permission
from app.audit.service import AuditService
from app.auth.models import Permission, Role, RolePermission, User, UserRole
from app.auth.permissions import ROLE_PERMISSION_SEED, Permissions, Roles
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

KIND = "ACCESS_CHANGE"
NOT_STAFF = {Roles.STUDENT, Roles.PARENT, Roles.ALUMNUS}


def _forget(user_id=None) -> None:
    from app.core import dependencies

    if user_id is None:
        dependencies.forget_all_sessions()
    else:
        dependencies.forget_session(user_id)


async def _tell(session: AsyncSession, user_ids, title: str, message: str, link: str | None = None) -> None:
    """In-app notification (and phone push, where switched on). Never stops the change itself."""
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    category = getattr(NotificationCategory, "GENERAL", None) or list(NotificationCategory)[0]
    for uid in list(user_ids)[:300]:
        try:
            async with session.begin_nested():
                await create_notification(session, user_id=uid, category=category, title=title[:200], message=message[:1000], link=link)
        except Exception:  # noqa: BLE001
            pass


def _words(codes) -> str:
    return ", ".join(catalog.label(c) for c in sorted(codes))


async def _after_change(session: AsyncSession, before: dict, *, why: str) -> None:
    """For each person whose rights changed: tell them what changed, and if they now hold money or control rights given
    on Access control, end their signed-in sessions so their next sign-in asks for the two-step code."""
    from app.auth.repository import AuthRepository

    for uid, (old_rights, old_extra) in before.items():
        roles, rights, extra = await effective(session, uid)
        gained, lost = set(rights) - set(old_rights), set(old_rights) - set(rights)
        if not gained and not lost:
            continue
        parts = []
        if gained:
            parts.append("You can now: " + _words(gained) + ".")
        if lost:
            parts.append("You can no longer: " + _words(lost) + ".")
        await _tell(session, [uid], "Your access changed", f"{why} " + " ".join(parts))
        if Roles.ADMIN not in roles and (set(extra) & catalog.SENSITIVE) - set(old_extra):
            await AuthRepository(session).revoke_all_for_user(uid)
        _forget(uid)


async def _snapshot(session: AsyncSession, user_ids) -> dict:
    out = {}
    for uid in user_ids:
        _r, rights, extra = await effective(session, uid)
        out[uid] = (rights, extra)
    return out


class AccessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ helpers
    async def _role(self, role_id) -> Role:
        role = await self.session.get(Role, uuid.UUID(str(role_id)))
        if role is None:
            raise ResourceNotFoundError("Role not found.")
        return role

    async def _role_rights(self, role_id) -> set[str]:
        return set((await self.session.execute(select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
                                               .where(RolePermission.role_id == role_id))).scalars().all())

    async def _members(self, role_id) -> list[uuid.UUID]:
        return list((await self.session.execute(select(UserRole.user_id).join(User, User.id == UserRole.user_id)
                                                .where(UserRole.role_id == role_id, User.is_active.is_(True)))).scalars().all())

    async def _names(self, ids) -> dict:
        from app.employees.models import Employee

        ids = {i for i in ids if i is not None}
        if not ids:
            return {}
        out = {u.id: u.username for u in (await self.session.execute(select(User).where(User.id.in_(ids)))).scalars()}
        for e in (await self.session.execute(select(Employee).where(Employee.user_id.in_(ids)))).scalars():
            out[e.user_id] = f"{e.first_name} {e.last_name}".strip()
        return out

    async def _second_admins(self, actor_id) -> list[uuid.UUID]:
        return await users_with_permission(self.session, Permissions.ROLE_MANAGE, exclude=actor_id)

    async def _record(self, *, kind, target_type, target_id, label, summary, before, after, actor_id, approval_id=None, undo_of=None) -> AccessChange:
        ch = AccessChange(kind=kind, target_type=target_type, target_id=target_id, target_label=label[:200], summary=summary[:500],
                          before=before, after=after, actor_id=actor_id, approval_request_id=approval_id, undo_of=undo_of)
        self.session.add(ch)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action=f"access.{kind.lower()}", entity_type="access_change", entity_id=ch.id,
                             before=before, after=after, metadata={"summary": ch.summary, "target": ch.target_label})
        return ch

    async def _wait_or_go(self, *, actor_id, payload: dict, summary: str, sensitive: list[str]):
        """None = go ahead now. Otherwise the waiting request (a second administrator confirms on Access control)."""
        from app.approvals.models import ApprovalRequest
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        if not sensitive:
            return None
        others = await self._second_admins(actor_id)
        if not others:
            await self.audit.log(actor_user_id=actor_id, action="approval.sole_approver", entity_type="access", entity_id=None,
                                 metadata={"kind": KIND, "summary": summary[:300]})
            return None
        r = ApprovalRequest(kind=KIND, target_id=None, summary=summary[:300], payload=payload, status="REQUESTED", requested_by=actor_id,
                            reason="Gives: " + ", ".join(catalog.label(c) for c in sensitive)[:280])
        self.session.add(r)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="approval.requested", entity_type="approval_request", entity_id=r.id,
                             metadata={"kind": KIND, "summary": r.summary})
        category = getattr(NotificationCategory, "TASK", None) or list(NotificationCategory)[0]
        for uid in others:
            await create_notification(self.session, user_id=uid, category=category, title="Access change to confirm",
                                      message=r.summary, link="/dashboard/admin/access#waiting")
        return r

    # ------------------------------------------------------------------ catalogue and roles
    async def overview(self) -> dict:
        roles = (await self.session.execute(select(Role).order_by(Role.is_builtin.desc(), Role.name))).scalars().all()
        counts = dict((await self.session.execute(select(UserRole.role_id, func.count()).join(User, User.id == UserRole.user_id)
                                                  .where(User.is_active.is_(True)).group_by(UserRole.role_id))).all())
        out = []
        for r in roles:
            rights = await self._role_rights(r.id)
            seeded = set(ROLE_PERMISSION_SEED.get(r.code, ())) if r.is_builtin else set()
            out.append({"id": r.id, "code": r.code, "name": r.name, "description": r.description, "builtin": r.is_builtin,
                        "locked": r.code == Roles.ADMIN, "own_portal": r.code in NOT_STAFF, "people": counts.get(r.id, 0), "rights": sorted(rights),
                        "added": sorted(rights - seeded) if r.is_builtin else [], "removed": sorted(seeded - rights) if r.is_builtin else [],
                        "own_use": sorted(rights & catalog.OWN_USE)})
        return {"modules": catalog.describe(), "roles": out, "locked": sorted(catalog.LOCKED), "sensitive": sorted(catalog.SENSITIVE),
                "labels": catalog.LABELS, "page_rights": catalog.page_rights()}

    async def create_role(self, *, name: str, description: str | None, copy_from=None, rights: list[str] | None, actor_id) -> dict:
        name = " ".join((name or "").split())
        if not (3 <= len(name) <= 100):
            raise ValidationAppError("Give the role a name (3 to 100 characters).")
        if (await self.session.execute(select(Role.id).where(func.lower(Role.name) == name.lower()))).first():
            raise BusinessRuleViolationError("A role with this name already exists.")
        base = "C_" + (re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_") or "ROLE")[:24]
        code, n = base, 2
        while (await self.session.execute(select(Role.id).where(Role.code == code))).first():
            code, n = f"{base[:27]}_{n}", n + 1
        wanted = set(rights or [])
        if copy_from:
            src = await self._role(copy_from)
            wanted |= (await self._role_rights(src.id)) & catalog.GRANTABLE
        role = Role(code=code, name=name, description=(description or "").strip()[:300] or None, is_builtin=False)
        self.session.add(role)
        await self.session.flush()
        await self._record(kind="ROLE_CREATED", target_type="ROLE", target_id=role.id, label=name, summary=f"Made the role {name}",
                           before=None, after={"name": name, "code": code}, actor_id=actor_id)
        result = await self.set_role_rights(role.id, sorted(wanted), actor_id=actor_id) if wanted else {"done": True}
        return {"id": role.id, "code": code, **result}

    async def update_role(self, role_id, *, name: str | None, description: str | None, actor_id) -> dict:
        role = await self._role(role_id)
        if role.is_builtin:
            raise BusinessRuleViolationError("A built-in role keeps its name; you can change what it can do.")
        before = {"name": role.name, "description": role.description}
        if name is not None:
            name = " ".join(name.split())
            if not (3 <= len(name) <= 100):
                raise ValidationAppError("Give the role a name (3 to 100 characters).")
            if (await self.session.execute(select(Role.id).where(func.lower(Role.name) == name.lower(), Role.id != role.id))).first():
                raise BusinessRuleViolationError("A role with this name already exists.")
            role.name = name
        if description is not None:
            role.description = description.strip()[:300] or None
        await self._record(kind="ROLE_RENAMED", target_type="ROLE", target_id=role.id, label=role.name, summary=f"Renamed / described {role.name}",
                           before=before, after={"name": role.name, "description": role.description}, actor_id=actor_id)
        return {"done": True}

    async def delete_role(self, role_id, *, actor_id) -> dict:
        role = await self._role(role_id)
        if role.is_builtin:
            raise BusinessRuleViolationError("Built-in roles cannot be deleted.")
        people = await self._members(role.id)
        if people:
            raise BusinessRuleViolationError(f"{len(people)} person(s) still have this role. Take it from them first (People tab).")
        rights = sorted(await self._role_rights(role.id))
        await self._record(kind="ROLE_DELETED", target_type="ROLE", target_id=role.id, label=role.name, summary=f"Deleted the role {role.name}",
                           before={"name": role.name, "description": role.description, "rights": rights}, after=None, actor_id=actor_id)
        await self.session.delete(role)
        await self.session.flush()
        return {"done": True}

    async def set_role_rights(self, role_id, rights: list[str], *, actor_id, undo_of=None) -> dict:
        role = await self._role(role_id)
        if role.code == Roles.ADMIN:
            raise BusinessRuleViolationError("The Administrator role always has everything and cannot be changed.")
        if role.code in NOT_STAFF:
            raise BusinessRuleViolationError("Students', parents' and alumni's roles keep their own portal; office rights are given to staff only.")
        wanted = {c.strip() for c in rights if c and c.strip()}
        current = await self._role_rights(role.id)
        kept = current - catalog.GRANTABLE                       # own-use rights a built-in role comes with stay as they are
        bad = sorted(wanted - catalog.GRANTABLE - kept)
        if bad:
            raise ValidationAppError("These cannot be given to a role here: " + ", ".join(catalog.label(c) for c in bad) + ".")
        target = (wanted & catalog.GRANTABLE) | kept
        target -= catalog.LOCKED
        added, removed = sorted(target - current), sorted(current - target)
        if not added and not removed:
            return {"done": True, "changed": False}
        summary = f"{role.name}: " + "; ".join(x for x in (
            ("gave " + ", ".join(catalog.label(c) for c in added)) if added else "",
            ("took " + ", ".join(catalog.label(c) for c in removed)) if removed else "") if x)
        payload = {"op": "role_rights", "role_id": str(role.id), "rights": sorted(target), "undo_of": str(undo_of) if undo_of else None}
        waiting = await self._wait_or_go(actor_id=actor_id, payload=payload, summary=summary, sensitive=[c for c in added if c in catalog.SENSITIVE])
        if waiting is not None:
            return {"done": False, "waiting": True, "approval_request_id": waiting.id, "message": "Saved for a second administrator to confirm."}
        await self._apply_role_rights(role, target, actor_id=actor_id, undo_of=undo_of)
        return {"done": True, "changed": True}

    async def _apply_role_rights(self, role: Role, target: set[str], *, actor_id, approval_id=None, undo_of=None) -> AccessChange:
        before = await _snapshot(self.session, await self._members(role.id))
        current = await self._role_rights(role.id)
        perms = {p.code: p for p in (await self.session.execute(select(Permission).where(Permission.code.in_(target | current)))).scalars()}
        missing = sorted(target - set(perms))
        if missing:
            raise BusinessRuleViolationError("Unknown right(s): " + ", ".join(missing) + ". Run the permission seed (deploy).")
        seeded = set(ROLE_PERMISSION_SEED.get(role.code, ())) if role.is_builtin else set()
        now = datetime.now(timezone.utc)
        for code in sorted(target - current):
            self.session.add(RolePermission(role_id=role.id, permission_id=perms[code].id))
            gone = await self.session.get(RolePermissionRemoval, (role.id, perms[code].id))
            if gone is not None:
                await self.session.delete(gone)
        for code in sorted(current - target):
            link = await self.session.get(RolePermission, (role.id, perms[code].id))
            if link is not None:
                await self.session.delete(link)
            if code in seeded and await self.session.get(RolePermissionRemoval, (role.id, perms[code].id)) is None:
                self.session.add(RolePermissionRemoval(role_id=role.id, permission_id=perms[code].id, removed_by=actor_id, removed_at=now))
        await self.session.flush()
        added, removed = sorted(target - current), sorted(current - target)
        ch = await self._record(kind="ROLE_RIGHTS", target_type="ROLE", target_id=role.id, label=role.name,
                                summary=f"{role.name}: " + "; ".join(x for x in (
                                    ("gave " + ", ".join(catalog.label(c) for c in added)) if added else "",
                                    ("took " + ", ".join(catalog.label(c) for c in removed)) if removed else "") if x),
                                before={"rights": sorted(current)}, after={"rights": sorted(target)}, actor_id=actor_id,
                                approval_id=approval_id, undo_of=undo_of)
        _forget()
        await _after_change(self.session, before, why=f"The {role.name} role was changed.")
        return ch

    # ------------------------------------------------------------------ one person
    async def _staff_user(self, user_id) -> User:
        user = await self.session.get(User, uuid.UUID(str(user_id)))
        if user is None:
            raise ResourceNotFoundError("Person not found.")
        roles, _r, _e = await effective(self.session, user.id)
        if not roles or set(roles) <= NOT_STAFF:
            raise BusinessRuleViolationError("Rights can be given to staff accounts only (not students, parents or alumni).")
        return user

    async def _overrides(self, user_id) -> list[UserAccessOverride]:
        return list((await self.session.execute(select(UserAccessOverride).where(UserAccessOverride.user_id == user_id)
                                                .order_by(UserAccessOverride.permission_code))).scalars().all())

    @staticmethod
    def _ov_dict(o) -> dict:
        return {"code": o.permission_code, "effect": o.effect, "until": o.until.isoformat() if o.until else None, "reason": o.reason}

    async def person(self, user_id) -> dict:
        from app.employees.models import Employee

        user = await self._staff_user(user_id)
        roles, rights, extra = await effective(self.session, user.id)
        emp = (await self.session.execute(select(Employee).where(Employee.user_id == user.id))).scalar_one_or_none()
        today = institute_today()
        ovs = await self._overrides(user.id)
        role_rows = (await self.session.execute(select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id))).scalars().all()
        from_roles: dict[str, list[str]] = {}
        for r in role_rows:
            for c in await self._role_rights(r.id):
                from_roles.setdefault(c, []).append(r.name)
        pages = self._pages(roles, extra)
        return {"user_id": user.id, "username": user.username, "name": f"{emp.first_name} {emp.last_name}".strip() if emp else user.username,
                "employee_id": emp.id if emp else None, "department": emp.department if emp else None,
                "roles": [{"code": r.code, "name": r.name, "builtin": r.is_builtin} for r in role_rows],
                "is_admin": Roles.ADMIN in roles, "rights": rights, "extra": extra, "from_roles": from_roles,
                "overrides": [{**self._ov_dict(o), "expired": bool(o.until and o.until < today)} for o in ovs], "pages": pages}

    @staticmethod
    def _pages(roles, extra) -> list[str]:
        if Roles.ADMIN in roles:
            return ["(every page)"]
        held = set(extra)
        return sorted(p for p, rights in catalog.page_rights().items() if held & set(rights))

    def _clean_overrides(self, items: list[dict]) -> list[dict]:
        today = institute_today()
        out: dict[str, dict] = {}
        for it in items or []:
            code = (it.get("code") or "").strip()
            effect = (it.get("effect") or "").strip().upper()
            if effect not in ("GRANT", "DENY"):
                raise ValidationAppError("Each right is either given (GRANT) or taken away (DENY).")
            if code in catalog.LOCKED:
                raise BusinessRuleViolationError(f"{catalog.label(code)} stays with the Administrator role and cannot be given to one person.")
            if effect == "GRANT" and code not in catalog.GRANTABLE:
                raise ValidationAppError(f"{catalog.label(code)} cannot be given to one person.")
            until = it.get("until") or None
            if isinstance(until, str):
                try:
                    until = date.fromisoformat(until)
                except ValueError as exc:
                    raise ValidationAppError("The end date is not a date.") from exc
            if until is not None and until < today:
                raise ValidationAppError("The end date is in the past.")
            reason = (it.get("reason") or "").strip()[:300] or None
            out[code] = {"code": code, "effect": effect, "until": until.isoformat() if until else None, "reason": reason}
        return [out[k] for k in sorted(out)]

    async def set_person(self, user_id, *, overrides: list[dict], actor_id, undo_of=None) -> dict:
        user = await self._staff_user(user_id)
        if str(user.id) == str(actor_id):
            raise BusinessRuleViolationError("You cannot change your own access. Ask another administrator.")
        roles, _rights, _extra = await effective(self.session, user.id)
        if Roles.ADMIN in roles:
            raise BusinessRuleViolationError("Administrators already have everything; rights for one person do not apply to them.")
        wanted = self._clean_overrides(overrides)
        current = [self._ov_dict(o) for o in await self._overrides(user.id)]
        if wanted == current:
            return {"done": True, "changed": False}
        old_grants = {o["code"] for o in current if o["effect"] == "GRANT"}
        new_sensitive = sorted(o["code"] for o in wanted if o["effect"] == "GRANT" and o["code"] in catalog.SENSITIVE and o["code"] not in old_grants)
        names = await self._names([user.id])
        who = names.get(user.id) or user.username
        summary = f"{who}: own rights " + (", ".join(("+" if o["effect"] == "GRANT" else "-") + catalog.label(o["code"])
                                                     + (f" until {o['until']}" if o["until"] else "") for o in wanted) or "cleared")
        payload = {"op": "person", "user_id": str(user.id), "overrides": wanted, "undo_of": str(undo_of) if undo_of else None}
        waiting = await self._wait_or_go(actor_id=actor_id, payload=payload, summary=summary, sensitive=new_sensitive)
        if waiting is not None:
            return {"done": False, "waiting": True, "approval_request_id": waiting.id, "message": "Saved for a second administrator to confirm."}
        await self._apply_person(user, wanted, actor_id=actor_id, undo_of=undo_of)
        return {"done": True, "changed": True}

    async def _apply_person(self, user: User, wanted: list[dict], *, actor_id, approval_id=None, undo_of=None) -> AccessChange:
        snap = await _snapshot(self.session, [user.id])
        existing = {o.permission_code: o for o in await self._overrides(user.id)}
        before = [self._ov_dict(o) for o in existing.values()]
        keep = {o["code"] for o in wanted}
        for code, o in existing.items():
            if code not in keep:
                await self.session.delete(o)
        for w in wanted:
            o = existing.get(w["code"])
            until = date.fromisoformat(w["until"]) if w["until"] else None
            if o is None:
                self.session.add(UserAccessOverride(user_id=user.id, permission_code=w["code"], effect=w["effect"], until=until,
                                                    reason=w["reason"], created_by=actor_id))
            else:
                if o.until != until:
                    o.reminded_on = None
                o.effect, o.until, o.reason = w["effect"], until, w["reason"]
        await self.session.flush()
        names = await self._names([user.id])
        who = names.get(user.id) or user.username
        ch = await self._record(kind="PERSON_RIGHTS", target_type="USER", target_id=user.id, label=who,
                                summary=f"{who}: own rights " + (", ".join(("+" if o["effect"] == "GRANT" else "-") + catalog.label(o["code"])
                                                                          + (f" until {o['until']}" if o["until"] else "") for o in wanted) or "cleared"),
                                before={"overrides": before}, after={"overrides": wanted}, actor_id=actor_id, approval_id=approval_id, undo_of=undo_of)
        _forget(user.id)
        ends = [o for o in wanted if o["until"] and o["effect"] == "GRANT"]
        await _after_change(self.session, snap, why="An administrator changed your rights." + (
            " Until: " + "; ".join(f"{catalog.label(o['code'])} {o['until']}" for o in ends) + "." if ends else ""))
        return ch

    async def set_person_roles(self, employee_id, extra_roles: list[str], *, actor_id, undo_of=None) -> dict:
        """Built-in extra roles and custom roles for a staff member (through Employees -> Extra access's rules).
        Adding the Administrator role waits for a second administrator."""
        from app.employees.access_roles import AccessRoleService
        from app.employees.models import Employee

        emp = await self.session.get(Employee, uuid.UUID(str(employee_id)))
        if emp is None or emp.user_id is None:
            raise ResourceNotFoundError("Employee (with a login) not found.")
        if str(emp.user_id) == str(actor_id):
            raise BusinessRuleViolationError("You cannot change your own access (or remove your own Administrator role). Ask another administrator.")
        svc = AccessRoleService(self.session)
        now = await svc.get(emp.id)
        wanted = sorted({c.strip().upper() for c in extra_roles if c and c.strip()})
        if wanted == sorted(now["extra_roles"]):
            return {"done": True, "changed": False}
        gives_admin = Roles.ADMIN in wanted and Roles.ADMIN not in now["roles"]
        added_rights: set[str] = set()
        for code in set(wanted) - set(now["roles"]):
            r = (await self.session.execute(select(Role).where(Role.code == code))).scalar_one_or_none()
            if r is not None:
                added_rights |= await self._role_rights(r.id)
        sensitive = (["role:ADMIN"] if gives_admin else []) + sorted(added_rights & catalog.SENSITIVE)
        who = f"{emp.first_name} {emp.last_name}".strip()
        summary = f"{who}: roles " + (", ".join(wanted) or "only their job title's")
        payload = {"op": "person_roles", "employee_id": str(emp.id), "extra_roles": wanted, "undo_of": str(undo_of) if undo_of else None}
        waiting = await self._wait_or_go(actor_id=actor_id, payload=payload, summary=summary,
                                         sensitive=[s if not s.startswith("role:") else "Administrator role" for s in sensitive])
        if waiting is not None:
            return {"done": False, "waiting": True, "approval_request_id": waiting.id, "message": "Saved for a second administrator to confirm."}
        await self._apply_person_roles(emp, wanted, actor_id=actor_id, undo_of=undo_of)
        return {"done": True, "changed": True}

    async def _apply_person_roles(self, emp, wanted: list[str], *, actor_id, approval_id=None, undo_of=None) -> AccessChange:
        from app.employees.access_roles import AccessRoleService

        svc = AccessRoleService(self.session)
        before = await svc.get(emp.id)
        snap = await _snapshot(self.session, [emp.user_id])
        after = await svc.set_extra(emp.id, wanted, actor_user_id=actor_id)
        who = f"{emp.first_name} {emp.last_name}".strip()
        ch = await self._record(kind="PERSON_ROLES", target_type="USER", target_id=emp.user_id, label=who,
                                summary=f"{who}: roles " + (", ".join(after["extra_roles"]) or "only their job title's"),
                                before={"employee_id": str(emp.id), "extra_roles": before["extra_roles"]},
                                after={"employee_id": str(emp.id), "extra_roles": after["extra_roles"]}, actor_id=actor_id,
                                approval_id=approval_id, undo_of=undo_of)
        _forget(emp.user_id)
        await _after_change(self.session, snap, why="An administrator changed your roles.")
        return ch

    # ------------------------------------------------------------------ preview
    async def preview_person(self, user_id, *, overrides: list[dict] | None = None, extra_roles: list[str] | None = None) -> dict:
        from app.employees.access_roles import AccessRoleService
        from app.employees.models import Employee

        user = await self._staff_user(user_id)
        roles, rights, extra = await effective(self.session, user.id)
        new_roles = list(roles)
        if extra_roles is not None:
            emp = (await self.session.execute(select(Employee).where(Employee.user_id == user.id))).scalar_one_or_none()
            main = (await AccessRoleService(self.session).get(emp.id))["main_role"] if emp else None
            new_roles = sorted({c.upper() for c in extra_roles} | ({main} if main else set()) | (set(roles) & NOT_STAFF))
        role_ids = {r.code: r.id for r in (await self.session.execute(select(Role).where(Role.code.in_(new_roles)))).scalars()}
        from_roles: set[str] = set()
        for rid in role_ids.values():
            from_roles |= await self._role_rights(rid)
        ovs = self._clean_overrides(overrides) if overrides is not None else [self._ov_dict(o) for o in await self._overrides(user.id)
                                                                                 if not (o.until and o.until < institute_today())]
        _r, new_rights, new_extra = combine(new_roles, from_roles, [(o["code"], o["effect"]) for o in ovs])
        return self._diff(rights, new_rights, self._pages(roles, extra), self._pages(new_roles, new_extra))

    async def preview_role(self, role_id, rights: list[str]) -> dict:
        role = await self._role(role_id)
        current = await self._role_rights(role.id)
        target = ({c for c in rights} & catalog.GRANTABLE) | (current - catalog.GRANTABLE)
        people = await self._members(role.id)
        names = await self._names(people)
        d = self._diff(sorted(current), sorted(target), [], [])
        d["people"] = len(people)
        d["names"] = sorted(names.get(p, "?") for p in people)[:30]
        return d

    @staticmethod
    def _diff(before, after, pages_before, pages_after) -> dict:
        b, a = set(before), set(after)
        return {"gained": [{"code": c, "label": catalog.label(c), "sensitive": c in catalog.SENSITIVE} for c in sorted(a - b)],
                "lost": [{"code": c, "label": catalog.label(c)} for c in sorted(b - a)],
                "pages_gained": sorted(set(pages_after) - set(pages_before)), "pages_lost": sorted(set(pages_before) - set(pages_after)),
                "rights_after": sorted(a)}

    # ------------------------------------------------------------------ history, undo, confirmations
    async def history(self, *, limit: int = 200) -> list[dict]:
        rows = (await self.session.execute(select(AccessChange).order_by(AccessChange.created_at.desc()).limit(limit))).scalars().all()
        names = await self._names({r.actor_id for r in rows} | {r.undone_by for r in rows})
        return [{"id": r.id, "kind": r.kind, "target": r.target_label, "target_type": r.target_type, "summary": r.summary,
                 "by": names.get(r.actor_id), "at": r.created_at, "undone_at": r.undone_at, "undone_by": names.get(r.undone_by),
                 "undo_of": r.undo_of, "confirmed_by_second_admin": r.approval_request_id is not None,
                 "can_undo": r.undone_at is None and r.kind in ("ROLE_RIGHTS", "PERSON_RIGHTS", "PERSON_ROLES", "ROLE_CREATED", "APPROVAL_RULES")} for r in rows]

    async def undo(self, change_id, *, actor_id) -> dict:
        ch = await self.session.get(AccessChange, uuid.UUID(str(change_id)))
        if ch is None:
            raise ResourceNotFoundError("Change not found.")
        if ch.undone_at is not None:
            raise BusinessRuleViolationError("This change was already undone.")
        if ch.kind == "ROLE_RIGHTS":
            result = await self.set_role_rights(ch.target_id, (ch.before or {}).get("rights", []), actor_id=actor_id, undo_of=ch.id)
        elif ch.kind == "PERSON_RIGHTS":
            result = await self.set_person(ch.target_id, overrides=(ch.before or {}).get("overrides", []), actor_id=actor_id, undo_of=ch.id)
        elif ch.kind == "PERSON_ROLES":
            result = await self.set_person_roles((ch.before or {})["employee_id"], (ch.before or {}).get("extra_roles", []),
                                                 actor_id=actor_id, undo_of=ch.id)
        elif ch.kind == "APPROVAL_RULES":
            result = await self.set_rules((ch.before or {})["action"], (ch.before or {}).get("rules", []), actor_id=actor_id, undo_of=ch.id)
        elif ch.kind == "ROLE_CREATED":
            role = await self.session.get(Role, ch.target_id)
            if role is None:
                raise BusinessRuleViolationError("That role was already deleted.")
            result = await self.delete_role(role.id, actor_id=actor_id)
        else:
            raise BusinessRuleViolationError("This change cannot be undone here.")
        if result.get("done"):
            ch.undone_at, ch.undone_by = datetime.now(timezone.utc), actor_id
            await self.session.flush()
        return result

    async def waiting(self) -> list[dict]:
        from app.approvals.models import ApprovalRequest

        rows = (await self.session.execute(select(ApprovalRequest).where(ApprovalRequest.kind == KIND, ApprovalRequest.status == "REQUESTED")
                                           .order_by(ApprovalRequest.created_at))).scalars().all()
        names = await self._names({r.requested_by for r in rows})
        return [{"id": r.id, "summary": r.summary, "reason": r.reason, "asked_by": names.get(r.requested_by), "asked_by_id": r.requested_by,
                 "at": r.created_at} for r in rows]

    async def decide(self, request_id, *, approve: bool, note: str | None, actor_id) -> dict:
        from app.approvals.service import ApprovalService

        r = await ApprovalService(self.session).decide(request_id, approve=approve, note=note, actor_id=actor_id)
        return {"id": r.id, "status": r.status}

    async def apply_confirmed(self, payload: dict, *, actor_id, approval_id, requested_by) -> dict:
        """Carry out a change a second administrator confirmed (as the person who asked)."""
        op = payload.get("op")
        undo_of = uuid.UUID(payload["undo_of"]) if payload.get("undo_of") else None
        who = requested_by or actor_id
        if op == "role_rights":
            role = await self._role(payload["role_id"])
            if role.code == Roles.ADMIN:
                raise BusinessRuleViolationError("The Administrator role cannot be changed.")
            ch = await self._apply_role_rights(role, set(payload["rights"]) - catalog.LOCKED, actor_id=who, approval_id=approval_id, undo_of=undo_of)
        elif op == "person":
            user = await self._staff_user(payload["user_id"])
            ch = await self._apply_person(user, self._clean_overrides(payload["overrides"]), actor_id=who, approval_id=approval_id, undo_of=undo_of)
        elif op == "approval_rules":
            ch = await self._apply_rules(payload["action"], payload["rules"], actor_id=who, approval_id=approval_id, undo_of=undo_of)
        elif op == "person_roles":
            from app.employees.models import Employee

            emp = await self.session.get(Employee, uuid.UUID(payload["employee_id"]))
            if emp is None:
                raise ResourceNotFoundError("Employee not found.")
            ch = await self._apply_person_roles(emp, payload["extra_roles"], actor_id=who, approval_id=approval_id, undo_of=undo_of)
        else:
            raise BusinessRuleViolationError("Unknown access change.")
        if undo_of is not None:
            old = await self.session.get(AccessChange, undo_of)
            if old is not None and old.undone_at is None:
                old.undone_at, old.undone_by = datetime.now(timezone.utc), actor_id
        return {"access_change_id": str(ch.id)}

    # ------------------------------------------------------------------ people list
    async def people(self, q: str | None) -> list[dict]:
        from app.employees.models import Employee

        stmt = select(Employee).where(Employee.user_id.is_not(None)).order_by(Employee.first_name, Employee.last_name).limit(60)
        if q and q.strip():
            like = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(Employee.first_name + " " + Employee.last_name).like(like) | func.lower(Employee.employee_code).like(like)
                              | func.lower(func.coalesce(Employee.department, "")).like(like))
        emps = (await self.session.execute(stmt)).scalars().all()
        ids = [e.user_id for e in emps]
        role_rows = (await self.session.execute(select(UserRole.user_id, Role.name, Role.code).join(Role, Role.id == UserRole.role_id)
                                                .where(UserRole.user_id.in_(ids)))).all() if ids else []
        by_user: dict = {}
        for uid, name, code in role_rows:
            by_user.setdefault(uid, []).append({"name": name, "code": code})
        ov_users = set((await self.session.execute(select(UserAccessOverride.user_id).where(UserAccessOverride.user_id.in_(ids)))).scalars().all()) if ids else set()
        return [{"user_id": e.user_id, "employee_id": e.id, "name": f"{e.first_name} {e.last_name}".strip(), "code": e.employee_code,
                 "department": e.department, "roles": by_user.get(e.user_id, []), "own_rights": e.user_id in ov_users} for e in emps]

    # ------------------------------------------------------------------ who can do this?
    async def who_can(self, code: str) -> dict:
        from app.employees.models import Employee

        code = (code or "").strip()
        if code not in catalog.LABELS:
            raise ValidationAppError("Unknown right.")
        today = institute_today()
        holders = await users_with_permission(self.session, code)
        via_role: dict = {}
        for uid, name in (await self.session.execute(
                select(UserRole.user_id, Role.name).join(Role, Role.id == UserRole.role_id).join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id).where(Permission.code == code))).all():
            via_role.setdefault(uid, []).append(name)
        ovs = {o.user_id: o for o in (await self.session.execute(select(UserAccessOverride).where(
            UserAccessOverride.permission_code == code, (UserAccessOverride.until.is_(None)) | (UserAccessOverride.until >= today)))).scalars()}
        ids = set(holders) | {u for u, o in ovs.items() if o.effect == "DENY"}
        names = await self._names(ids)
        emps = {e.user_id: e for e in (await self.session.execute(select(Employee).where(Employee.user_id.in_(ids)))).scalars()} if ids else {}
        rows = []
        for uid in ids:
            o = ovs.get(uid)
            e = emps.get(uid)
            rows.append({"user_id": uid, "name": names.get(uid, "?"), "department": e.department if e else None,
                         "has_it": uid in holders, "roles": sorted(via_role.get(uid, [])),
                         "own": ({"effect": o.effect, "until": o.until.isoformat() if o.until else None, "reason": o.reason} if o else None)})
        rows.sort(key=lambda r: (not r["has_it"], r["name"].lower()))
        return {"code": code, "label": catalog.label(code), "sensitive": code in catalog.SENSITIVE, "count": len(holders), "people": rows}

    # ------------------------------------------------------------------ a role's people, and giving it to many at once
    async def role_members(self, role_id) -> list[dict]:
        from app.employees.models import Employee, JobRole

        role = await self._role(role_id)
        rows = (await self.session.execute(select(Employee, JobRole.default_role_code).join(UserRole, UserRole.user_id == Employee.user_id)
                                           .outerjoin(JobRole, JobRole.id == Employee.job_role_id)
                                           .where(UserRole.role_id == role.id).order_by(Employee.first_name, Employee.last_name))).all()
        return [{"employee_id": e.id, "user_id": e.user_id, "name": f"{e.first_name} {e.last_name}".strip(), "code": e.employee_code,
                 "department": e.department, "from_job_title": main == role.code} for e, main in rows]

    async def set_members(self, role_id, *, add: list, remove: list, actor_id) -> dict:
        """Give this role to several staff members, or take it from them, one by one through the usual rules (a person's
        own access, the Administrator role and money rights are handled exactly as on their own page)."""
        from app.employees.access_roles import AccessRoleService

        role = await self._role(role_id)
        if role.code in NOT_STAFF:
            raise BusinessRuleViolationError("Students', parents' and alumni's roles are not given here.")
        results = []
        svc = AccessRoleService(self.session)
        for emp_id, giving in [(x, True) for x in add or []] + [(x, False) for x in remove or []]:
            name = str(emp_id)
            try:
                async with self.session.begin_nested():
                    now = await svc.get(uuid.UUID(str(emp_id)))
                    from app.employees.models import Employee

                    emp = await self.session.get(Employee, uuid.UUID(str(emp_id)))
                    name = f"{emp.first_name} {emp.last_name}".strip() if emp else name
                    if not giving and now["main_role"] == role.code:
                        raise BusinessRuleViolationError("it comes with their job title (change it on Employees)")
                    extra = set(now["extra_roles"])
                    extra = extra | {role.code} if giving else extra - {role.code}
                    r = await self.set_person_roles(emp_id, sorted(extra), actor_id=actor_id)
                results.append({"employee_id": str(emp_id), "name": name, "ok": True, "waiting": bool(r.get("waiting"))})
            except Exception as exc:  # noqa: BLE001 - one person's problem must not stop the others
                msg = getattr(exc, "message", None) or str(exc)
                results.append({"employee_id": str(emp_id), "name": name, "ok": False, "message": msg[:200]})
        return {"results": results, "done": sum(1 for r in results if r["ok"] and not r.get("waiting")),
                "waiting": sum(1 for r in results if r.get("waiting")), "failed": sum(1 for r in results if not r["ok"])}

    # ------------------------------------------------------------------ approval rules (who approves what)
    async def rules_overview(self) -> dict:
        from app.access.models import ApprovalRule
        from app.approvals import rules as R

        rows = (await self.session.execute(select(ApprovalRule).order_by(ApprovalRule.action, ApprovalRule.requester_role, ApprovalRule.min_amount))).scalars().all()
        role_names = dict((await self.session.execute(select(Role.code, Role.name))).all())
        names = await self._names({uuid.UUID(r.approver_value) for r in rows if r.approver_type == "USER" and r.approver_value})
        by: dict = {}
        for r in rows:
            by.setdefault(r.action, []).append({
                "id": r.id, "requester_role": r.requester_role, "requester_name": role_names.get(r.requester_role) if r.requester_role else "Anyone",
                "min_amount": float(r.min_amount) if r.min_amount is not None else None, "mode": r.mode, "approver_type": r.approver_type,
                "approver_value": r.approver_value, "note": r.note, "text": await R.describe(self.session, r),
                "approver_name": (role_names.get(r.approver_value) if r.approver_type == "ROLE" else
                                  names.get(uuid.UUID(r.approver_value)) if r.approver_type == "USER" and r.approver_value else None)})
        return {"actions": [{"key": a.key, "label": a.label, "group": a.group, "about": a.about, "amount": a.amount, "approvers": list(a.approvers),
                             "allow_none": a.allow_none, "default_text": a.default_text, "rules": by.get(a.key, [])} for a in R.ACTIONS],
                "approver_labels": R.APPROVER_LABELS,
                "roles": [{"code": c, "name": n} for c, n in sorted(role_names.items(), key=lambda x: x[1]) if c not in NOT_STAFF]}

    async def _clean_rules(self, action: str, items: list[dict]) -> list[dict]:
        from decimal import Decimal, InvalidOperation

        from app.approvals import rules as R

        a = R.BY_KEY.get(action)
        if a is None:
            raise ValidationAppError("Unknown kind of action.")
        roles = set((await self.session.execute(select(Role.code))).scalars())
        out, seen = [], set()
        for it in items or []:
            role = (it.get("requester_role") or None)
            if role and (role not in roles or role in NOT_STAFF):
                raise ValidationAppError(f"Unknown or non-staff role: {role}.")
            amt = it.get("min_amount")
            if amt in ("", None):
                amt = None
            else:
                if not a.amount:
                    raise ValidationAppError(f"{a.label} has no amount, so a rule cannot depend on one.")
                try:
                    amt = Decimal(str(amt)).quantize(Decimal("0.01"))
                except (InvalidOperation, ValueError) as exc:
                    raise ValidationAppError("The amount is not a number.") from exc
                if amt < 0:
                    raise ValidationAppError("The amount cannot be negative.")
            mode = (it.get("mode") or "APPROVE").upper()
            if mode not in ("NONE", "APPROVE"):
                raise ValidationAppError("A rule either needs no approval or names who approves.")
            if mode == "NONE" and not a.allow_none:
                raise ValidationAppError(f"{a.label} always needs approval.")
            atype = None if mode == "NONE" else (it.get("approver_type") or "DEFAULT").upper()
            value = None
            if atype is not None:
                if atype not in a.approvers:
                    raise ValidationAppError(f"{a.label} cannot be approved by: {R.APPROVER_LABELS.get(atype, atype)}.")
                if atype == "ROLE":
                    value = (it.get("approver_value") or "").strip().upper()
                    if value not in roles or value in NOT_STAFF:
                        raise ValidationAppError("Choose the role that approves.")
                elif atype == "USER":
                    value = str(it.get("approver_value") or "").strip()
                    await self._staff_user(value)                         # a staff account
            key = (role, str(amt) if amt is not None else None)
            if key in seen:
                raise ValidationAppError("Two rules are for the same people and amount; keep one.")
            seen.add(key)
            out.append({"requester_role": role, "min_amount": str(amt) if amt is not None else None, "mode": mode, "approver_type": atype,
                        "approver_value": value, "note": (it.get("note") or "").strip()[:200] or None})
        return sorted(out, key=lambda r: (r["requester_role"] or "", float(r["min_amount"] or 0)))

    async def _current_rules(self, action: str) -> list[dict]:
        from app.access.models import ApprovalRule

        rows = (await self.session.execute(select(ApprovalRule).where(ApprovalRule.action == action))).scalars().all()
        return sorted([{"requester_role": r.requester_role, "min_amount": str(r.min_amount) if r.min_amount is not None else None, "mode": r.mode,
                        "approver_type": r.approver_type, "approver_value": r.approver_value, "note": r.note} for r in rows],
                      key=lambda r: (r["requester_role"] or "", float(r["min_amount"] or 0)))

    async def set_rules(self, action: str, items: list[dict], *, actor_id, undo_of=None) -> dict:
        from app.approvals import rules as R

        wanted = await self._clean_rules(action, items)
        current = await self._current_rules(action)
        if wanted == current:
            return {"done": True, "changed": False}
        a = R.BY_KEY[action]
        summary = f"Approvals for {a.label}: " + (f"{len(wanted)} rule(s)" if wanted else "back to the standard")
        sensitive = [a.label] if a.amount else []                 # who approves money waits for a second administrator
        payload = {"op": "approval_rules", "action": action, "rules": wanted, "undo_of": str(undo_of) if undo_of else None}
        waiting = await self._wait_or_go(actor_id=actor_id, payload=payload, summary=summary, sensitive=sensitive)
        if waiting is not None:
            return {"done": False, "waiting": True, "approval_request_id": waiting.id, "message": "Saved for a second administrator to confirm."}
        await self._apply_rules(action, wanted, actor_id=actor_id, undo_of=undo_of)
        return {"done": True, "changed": True}

    async def _apply_rules(self, action: str, wanted: list[dict], *, actor_id, approval_id=None, undo_of=None) -> AccessChange:
        from decimal import Decimal

        from app.access.models import ApprovalRule
        from app.approvals import rules as R

        before = await self._current_rules(action)
        for r in (await self.session.execute(select(ApprovalRule).where(ApprovalRule.action == action))).scalars().all():
            await self.session.delete(r)
        await self.session.flush()
        for w in wanted:
            self.session.add(ApprovalRule(action=action, requester_role=w["requester_role"],
                                          min_amount=Decimal(w["min_amount"]) if w["min_amount"] is not None else None, mode=w["mode"],
                                          approver_type=w["approver_type"], approver_value=w["approver_value"], note=w["note"], created_by=actor_id))
        await self.session.flush()
        R.forget()
        a = R.BY_KEY[action]
        lines = []
        for r in (await self.session.execute(select(ApprovalRule).where(ApprovalRule.action == action))).scalars().all():
            who = "anyone" if not r.requester_role else r.requester_role
            lines.append(f"{who}{' from Rs. ' + format(float(r.min_amount), ',.0f') if r.min_amount is not None else ''}: {await R.describe(self.session, r)}")
        return await self._record(kind="APPROVAL_RULES", target_type="RULES", target_id=None, label=a.label,
                                  summary=f"Approvals for {a.label}: " + ("; ".join(lines) if lines else "back to the standard"),
                                  before={"action": action, "rules": before}, after={"action": action, "rules": wanted}, actor_id=actor_id,
                                  approval_id=approval_id, undo_of=undo_of)

    async def try_rule(self, action: str, *, user_id, amount=None) -> dict:
        from app.approvals import rules as R

        if action not in R.BY_KEY:
            raise ValidationAppError("Unknown kind of action.")
        uid = uuid.UUID(str(user_id))
        res = await R.effective(self.session, action, requester_id=uid, amount=amount,
                                ctx={"department": await R.department_of(self.session, uid)})
        ids = await R.approvers(self.session, res, requester_id=uid, ctx={"department": await R.department_of(self.session, uid)})
        names = await self._names(ids)
        return {"no_approval": res.no_approval, "text": res.text, "people": sorted(names.get(i, "?") for i in ids),
                "note": "Course-teacher rules depend on the class, so they are shown when a real request is made." if res.approver_type == "COURSE_TEACHER" else None}



async def end_date_round(session: AsyncSession) -> int:
    """Daily: a right given (or taken) until a date ends in 2 days -> tell the person and the administrators, once.
    Never raises."""
    from datetime import timedelta

    try:
        today = institute_today()
        rows = (await session.execute(select(UserAccessOverride).where(
            UserAccessOverride.until.is_not(None), UserAccessOverride.until >= today, UserAccessOverride.until <= today + timedelta(days=2),
            UserAccessOverride.reminded_on.is_(None)))).scalars().all()
        if not rows:
            return 0
        admins = await users_with_permission(session, Permissions.ROLE_MANAGE)
        svc = AccessService(session)
        names = await svc._names({o.user_id for o in rows})
        for o in rows:
            what = catalog.label(o.permission_code)
            kind = "given to" if o.effect == "GRANT" else "taken from"
            when = o.until.strftime("%d %b %Y")
            await _tell(session, [o.user_id], "Access ending soon",
                        f"{what} ({'given to you' if o.effect == 'GRANT' else 'taken away from you'}) ends after {when}.")
            await _tell(session, admins, "Access ending soon", f"{what} {kind} {names.get(o.user_id, 'someone')} ends after {when}. "
                        "Extend it on Access control if it is still needed.", link=f"/dashboard/admin/access?user={o.user_id}")
            o.reminded_on = today
        await session.flush()
        return len(rows)
    except Exception:  # noqa: BLE001
        return 0

