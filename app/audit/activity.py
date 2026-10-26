"""The activity log as people read it: who did what, when, from where, with filters and a CSV export.

Built on the existing audit_logs table (nothing is changed or deleted here). Each entry is shown with the person's real name,
what kind of person it was (administrator / staff / student / parent / system), the record it touched and, when opened, the
before / after values. Holders of only audit.view.financial still see financial actions only (as in app/audit/router.py).
"""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import String, and_, cast, exists, func, or_, select

from app.audit.models import AuditLog
from app.audit.router import FINANCIAL_ACTION_PREFIXES, _require_audit_access
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.core.csvsafe import safe_writer
from app.core.database import get_db
from app.core.dependencies import require_permission

router = APIRouter(prefix="/audit", tags=["audit"])
can_export = require_permission(Permissions.AUDIT_EXPORT)
MAX_EXPORT = 50_000

KINDS = ("admin", "staff", "student", "parent", "system")
SPECIAL = {"view_as.started": "started viewing someone's screens (read-only)", "view_as.ended": "stopped viewing someone's screens",
           "audit.exported": "downloaded the activity log"}
VERBS = {"created": "created", "updated": "updated", "deleted": "deleted", "login_success": "signed in", "login_failed": "failed to sign in",
         "logout": "signed out", "password_changed": "changed their password", "started": "started", "ended": "ended"}


def _has_role(code: str):
    return exists().where(and_(UserRole.user_id == AuditLog.actor_user_id, UserRole.role_id == Role.id, Role.code == code))


def _filters(*, current_user: CurrentUser, q: str | None, kind: str | None, actor_user_id: uuid.UUID | None, action: str | None,
             entity_type: str | None, entity_id: uuid.UUID | None, date_from: date | None, date_to: date | None) -> list:
    cond = []
    if Permissions.AUDIT_VIEW not in current_user.permissions:          # financial-only access stays financial-only
        cond.append(or_(*[AuditLog.action.like(f"{p}%") for p in FINANCIAL_ACTION_PREFIXES]))
    if kind == "system":
        cond.append(AuditLog.actor_user_id.is_(None))
    elif kind == "admin":
        cond.append(_has_role(Roles.ADMIN))
    elif kind == "student":
        cond.append(and_(_has_role(Roles.STUDENT), ~_has_role(Roles.ADMIN)))
    elif kind == "parent":
        cond.append(and_(_has_role(Roles.PARENT), ~_has_role(Roles.ADMIN)))
    elif kind == "staff":
        cond.append(and_(AuditLog.actor_user_id.is_not(None), ~_has_role(Roles.ADMIN), ~_has_role(Roles.STUDENT), ~_has_role(Roles.PARENT)))
    if actor_user_id:
        cond.append(AuditLog.actor_user_id == actor_user_id)
    if action:
        cond.append(AuditLog.action.like(f"{action}%"))
    if entity_type:
        cond.append(AuditLog.entity_type == entity_type)
    if entity_id:
        cond.append(AuditLog.entity_id == entity_id)
    if date_from:
        cond.append(AuditLog.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        cond.append(AuditLog.created_at < datetime.combine(date_to + timedelta(days=1), time.min))
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        matching_users = select(User.id).where(func.lower(User.username).like(needle))
        cond.append(or_(func.lower(AuditLog.action).like(needle), func.lower(AuditLog.entity_type).like(needle),
                        AuditLog.actor_user_id.in_(matching_users), cast(AuditLog.entity_id, String).like(needle)))
    return cond


async def _people(db, ids: set[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Username, real name and kind for each actor (one query per table, not one per row)."""
    from app.employees.models import Employee
    from app.students.models import Student

    out: dict[uuid.UUID, dict] = {}
    if not ids:
        return out
    for uid, uname in (await db.execute(select(User.id, User.username).where(User.id.in_(ids)))).all():
        out[uid] = {"id": uid, "username": uname, "name": None, "roles": []}
    for uid, code in (await db.execute(select(UserRole.user_id, Role.code).join(Role, Role.id == UserRole.role_id).where(UserRole.user_id.in_(ids)))).all():
        if uid in out:
            out[uid]["roles"].append(code)
    for uid, fn, ln in (await db.execute(select(Employee.user_id, Employee.first_name, Employee.last_name).where(Employee.user_id.in_(ids)))).all():
        if uid in out:
            out[uid]["name"] = f"{fn} {ln}".strip()
    for uid, fn, ln in (await db.execute(select(Student.user_id, Student.first_name, Student.last_name).where(Student.user_id.in_(ids)))).all():
        if uid in out and not out[uid]["name"]:
            out[uid]["name"] = f"{fn} {ln}".strip()
    for p in out.values():
        r = p["roles"]
        p["kind"] = "admin" if Roles.ADMIN in r else "student" if Roles.STUDENT in r else "parent" if Roles.PARENT in r else "staff"
        if not p["name"]:
            p["name"] = "Guardian" if p["kind"] == "parent" else p["username"]
    return out


def describe(action: str, entity_type: str) -> str:
    """'fee.payment_recorded' -> 'fee payment recorded'; a few common ones read as plain sentences."""
    head, _, tail = action.partition(".")
    if action.startswith("auth.") and tail in VERBS:
        return VERBS[tail]
    if action in SPECIAL:
        return SPECIAL[action]
    words = (tail or head).replace("_", " ").replace(".", " ")
    thing = head.replace("_", " ") if tail else entity_type.replace("_", " ")
    return f"{thing}: {words}"


def _row(log: AuditLog, people: dict) -> dict:
    who = people.get(log.actor_user_id) if log.actor_user_id else None
    meta = log.log_metadata or {}
    return {
        "id": log.id, "at": log.created_at, "action": log.action, "what": describe(log.action, log.entity_type),
        "entity_type": log.entity_type, "entity_id": log.entity_id,
        "actor": {"id": who["id"], "username": who["username"], "name": who["name"], "kind": who["kind"], "roles": who["roles"]} if who
        else {"id": None, "username": None, "name": "System", "kind": "system", "roles": []},
        "ip": str(log.ip_address) if log.ip_address else None, "user_agent": log.user_agent,
        "subject": meta.get("viewed_username") or meta.get("target_username") or meta.get("username"),
        "summary": meta.get("summary") or meta.get("reason") or meta.get("note"),
    }


@router.get("/activity", summary="The activity log: who did what, when, from where (administration; principal read-only)")
async def activity(
    q: str | None = None, kind: str | None = Query(default=None, pattern="^(admin|staff|student|parent|system)$"),
    actor_user_id: uuid.UUID | None = None, action: str | None = None, entity_type: str | None = None, entity_id: uuid.UUID | None = None,
    date_from: date | None = None, date_to: date | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(_require_audit_access), db=Depends(get_db),
) -> dict:
    cond = _filters(current_user=current_user, q=q, kind=kind, actor_user_id=actor_user_id, action=action, entity_type=entity_type,
                    entity_id=entity_id, date_from=date_from, date_to=date_to)
    total = (await db.execute(select(func.count()).select_from(AuditLog).where(*cond))).scalar_one()
    logs = (await db.execute(select(AuditLog).where(*cond).order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    people = await _people(db, {log.actor_user_id for log in logs if log.actor_user_id})
    return {"items": [_row(log, people) for log in logs], "total": total, "page": page, "page_size": page_size,
            "can_export": Permissions.AUDIT_EXPORT in current_user.permissions}


@router.get("/activity/{log_id}", summary="One activity entry with what it changed (before / after)")
async def activity_entry(log_id: uuid.UUID, current_user: CurrentUser = Depends(_require_audit_access), db=Depends(get_db)) -> dict:
    from app.core.exceptions import ResourceNotFoundError

    log = await db.get(AuditLog, log_id)
    if log is None or (Permissions.AUDIT_VIEW not in current_user.permissions and not any(log.action.startswith(p) for p in FINANCIAL_ACTION_PREFIXES)):
        raise ResourceNotFoundError("Activity entry not found.")
    people = await _people(db, {log.actor_user_id} if log.actor_user_id else set())
    return {**_row(log, people), "before": log.before, "after": log.after, "metadata": log.log_metadata}


@router.get("/activity-facets", summary="Kinds of records and actions present in the log (for the filters)")
async def facets(current_user: CurrentUser = Depends(_require_audit_access), db=Depends(get_db)) -> dict:
    cond = _filters(current_user=current_user, q=None, kind=None, actor_user_id=None, action=None, entity_type=None, entity_id=None,
                    date_from=None, date_to=None)
    entities = [r[0] for r in (await db.execute(select(AuditLog.entity_type).where(*cond).group_by(AuditLog.entity_type).order_by(AuditLog.entity_type))).all()]
    prefix = func.split_part(AuditLog.action, ".", 1)
    actions = [r[0] for r in (await db.execute(select(prefix).where(*cond).group_by(prefix).order_by(prefix))).all()]
    return {"entity_types": entities, "action_groups": actions}


@router.get("/activity.csv", summary="Download the filtered activity log as CSV (administration only)")
async def export_csv(
    q: str | None = None, kind: str | None = Query(default=None, pattern="^(admin|staff|student|parent|system)$"),
    actor_user_id: uuid.UUID | None = None, action: str | None = None, entity_type: str | None = None, entity_id: uuid.UUID | None = None,
    date_from: date | None = None, date_to: date | None = None, current_user: CurrentUser = Depends(can_export), db=Depends(get_db),
) -> Response:
    cond = _filters(current_user=current_user, q=q, kind=kind, actor_user_id=actor_user_id, action=action, entity_type=entity_type,
                    entity_id=entity_id, date_from=date_from, date_to=date_to)
    logs = (await db.execute(select(AuditLog).where(*cond).order_by(AuditLog.created_at.desc()).limit(MAX_EXPORT))).scalars().all()
    people = await _people(db, {log.actor_user_id for log in logs if log.actor_user_id})
    # Taking a copy of the log is itself recorded.
    from app.audit.service import AuditService

    await AuditService(db).log(actor_user_id=current_user.id, action="audit.exported", entity_type="audit_log", entity_id=None,
                               metadata={"rows": len(logs), "filters": {k: str(v) for k, v in dict(q=q, kind=kind, actor_user_id=actor_user_id, action=action,
                                         entity_type=entity_type, entity_id=entity_id, date_from=date_from, date_to=date_to).items() if v}})
    await db.commit()
    buf = io.StringIO()
    w = safe_writer(buf, lineterminator="\r\n")
    w.writerow(["When (UTC)", "Who", "Username", "Kind", "Roles", "What", "Action", "Record", "Record ID", "IP address", "Details"])
    for log in logs:
        r = _row(log, people)
        w.writerow([r["at"].strftime("%Y-%m-%d %H:%M:%S"), r["actor"]["name"], r["actor"]["username"] or "", r["actor"]["kind"],
                    " ".join(r["actor"]["roles"]), r["what"], r["action"], r["entity_type"], r["entity_id"] or "", r["ip"] or "", r["summary"] or ""])
    name = f"activity-log-{date.today().isoformat()}.csv"
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
