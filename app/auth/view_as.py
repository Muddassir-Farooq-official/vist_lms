"""Administrator "view as": open a teacher's, student's or parent's own screens, read-only.

How it works
- POST /view-as/start gives the administrator's browser a short-lived access token for the chosen person. The token carries
  `va` (the administrator's user id) and `va_name`; app.core.dependencies marks such a request `viewing_as_by`.
- The session is READ-ONLY: a guard in app/main.py refuses every request that could change something (anything but
  GET / HEAD / OPTIONS) while such a token is in use, except leaving the view, refreshing and signing out.
- POST /view-as/stop puts the administrator's own session back. If the 30 minutes run out, the browser's normal
  refresh simply brings the administrator's own session back too (their refresh cookie was never touched).
- Starting and ending a view are kept in the activity log (who looked at whose screens, when, from where).
Administrators cannot be viewed, and a view cannot be started from inside another view.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Permissions, Roles
from app.auth.redirects import home_for_roles
from app.auth.schemas import CurrentUser
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import _fresh_account, get_current_user_allow_pending_password_change, require_permission
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.core.net import client_ip
from app.core.security import create_access_token

router = APIRouter(prefix="/view-as", tags=["view as (administration)"])
can_view_as = require_permission(Permissions.VIEW_AS)

VIEW_MINUTES = 30
ACCESS_COOKIE = "access_token"
ROLE_LABEL = {Roles.TEACHER: "Teacher", Roles.STUDENT: "Student", Roles.PARENT: "Parent", Roles.ACCOUNTS_OFFICER: "Accounts",
              Roles.LIBRARIAN: "Librarian", Roles.HOSTEL_WARDEN: "Hostel warden", Roles.SECURITY_GUARD: "Gate", Roles.STAFF: "Staff",
              Roles.EXAM_CONTROLLER: "Exam department", Roles.PRINCIPAL: "Principal", Roles.HOD: "Head of department"}


class StartIn(BaseModel):
    user_id: uuid.UUID


def _cookie(response: Response, value: str, minutes: int) -> None:
    response.set_cookie(ACCESS_COOKIE, value, max_age=minutes * 60, httponly=True, secure=settings.cookie_secure,
                        samesite="strict", domain=settings.cookie_domain or None, path="/")


def _view_token(*, user: User, roles: list[str], perms: list[str], admin: CurrentUser) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user.id), "username": user.username, "roles": roles, "perms": perms, "must_change_password": False,
               "type": "access", "iat": now, "exp": now + timedelta(minutes=VIEW_MINUTES), "jti": str(uuid.uuid4()),
               "va": str(admin.id), "va_name": admin.username}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@router.get("/people", summary="Find a teacher, student, parent or staff member to view as (administration)")
async def people(q: str = "", current_user: CurrentUser = Depends(can_view_as), db=Depends(get_db)) -> list[dict]:
    from app.employees.models import Employee
    from app.parents.models import ParentStudentLink
    from app.students.models import Student

    needle = f"%{q.strip().lower()}%"
    if len(q.strip()) < 2:
        return []
    out: list[dict] = []
    students = (await db.execute(
        select(Student).where(Student.user_id.is_not(None), or_(
            func.lower(Student.first_name + " " + Student.last_name).like(needle), func.lower(Student.roll_number).like(needle),
            func.lower(func.coalesce(Student.cnic, "")).like(needle))).order_by(Student.roll_number).limit(15))).scalars().all()
    out += [{"user_id": s.user_id, "name": f"{s.first_name} {s.last_name}".strip(), "detail": f"Roll {s.roll_number}", "kind": "Student"} for s in students]
    employees = (await db.execute(
        select(Employee).where(Employee.user_id.is_not(None), or_(
            func.lower(Employee.first_name + " " + Employee.last_name).like(needle), func.lower(Employee.employee_code).like(needle),
            func.lower(func.coalesce(Employee.cnic, "")).like(needle))).order_by(Employee.employee_code).limit(15))).scalars().all()
    for e in employees:
        out.append({"user_id": e.user_id, "name": f"{e.first_name} {e.last_name}".strip(), "detail": e.employee_code, "kind": "Staff"})
    # Parents: matched by their own login (the guardian's CNIC) or by a child's name / roll number.
    parent_rows = (await db.execute(
        select(User.id, User.username, Student.first_name, Student.last_name, Student.roll_number)
        .join(ParentStudentLink, ParentStudentLink.user_id == User.id).join(Student, Student.id == ParentStudentLink.student_id)
        .where(or_(func.lower(User.username).like(needle), func.lower(Student.first_name + " " + Student.last_name).like(needle),
                   func.lower(Student.roll_number).like(needle))).limit(30))).all()
    parents: dict[uuid.UUID, dict] = {}
    for uid, uname, fn, ln, roll in parent_rows:
        p = parents.setdefault(uid, {"user_id": uid, "name": "Guardian", "detail": uname, "kind": "Parent", "children": []})
        p["children"].append(f"{fn} {ln} ({roll})")
    for p in parents.values():
        p["detail"] = "Guardian of " + ", ".join(p.pop("children")[:3]) + " · " + p["detail"]
        out.append(p)
    # Roles of everyone found (administrators are listed but cannot be viewed).
    ids = [o["user_id"] for o in out]
    roles: dict[uuid.UUID, list[str]] = {}
    if ids:
        for uid, code in (await db.execute(select(UserRole.user_id, Role.code).join(Role, Role.id == UserRole.role_id).where(UserRole.user_id.in_(ids)))).all():
            roles.setdefault(uid, []).append(code)
    for o in out:
        r = roles.get(o["user_id"], [])
        o["roles"] = [ROLE_LABEL.get(x, x.title()) for x in r]
        o["can_view"] = bool(r) and Roles.ADMIN not in r and o["user_id"] != current_user.id
    return out[:40]


@router.post("/start", summary="Open this person's screens, read-only, for 30 minutes (administration)")
async def start(body: StartIn, request: Request, response: Response, current_user: CurrentUser = Depends(can_view_as), db=Depends(get_db)) -> dict:
    if current_user.viewing_as_by:
        raise BusinessRuleViolationError("You are already viewing someone. Exit that view first.")
    user = await db.get(User, body.user_id)
    if user is None:
        raise ResourceNotFoundError("Person not found.")
    fresh = await _fresh_account(db, str(user.id))
    if fresh is None or not fresh[0]:
        raise BusinessRuleViolationError("This account is not active, so there is nothing to view.")
    _, _, roles, perms, _ = fresh
    if Roles.ADMIN in roles:
        raise AuthorizationError("Administrators cannot be viewed as.")
    if not roles:
        raise BusinessRuleViolationError("This account has no role, so it has no screens to view.")
    home = home_for_roles(roles) or "/"
    await AuditService(db).log(actor_user_id=current_user.id, action="view_as.started", entity_type="user", entity_id=user.id,
                               metadata={"viewed_username": user.username, "viewed_roles": roles, "minutes": VIEW_MINUTES},
                               ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    await db.commit()
    _cookie(response, _view_token(user=user, roles=roles, perms=perms, admin=current_user), VIEW_MINUTES)
    return {"redirect": home, "minutes": VIEW_MINUTES}


@router.post("/stop", summary="Leave the view and return to the administration")
async def stop(request: Request, response: Response, current_user: CurrentUser = Depends(get_current_user_allow_pending_password_change),
               db=Depends(get_db)) -> dict:
    if not current_user.viewing_as_by_id:
        return {"redirect": "/dashboard/admin"}
    admin = await db.get(User, current_user.viewing_as_by_id)
    fresh = await _fresh_account(db, str(current_user.viewing_as_by_id)) if admin else None
    await AuditService(db).log(actor_user_id=current_user.viewing_as_by_id, action="view_as.ended", entity_type="user",
                               entity_id=current_user.id, metadata={"viewed_username": current_user.username},
                               ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    await db.commit()
    if admin is None or fresh is None or not fresh[0] or Permissions.VIEW_AS not in fresh[3]:
        response.delete_cookie(ACCESS_COOKIE, path="/", domain=settings.cookie_domain or None)
        return {"redirect": "/?reauth=1"}
    _, must_change, roles, perms, _ = fresh
    token = create_access_token(user_id=admin.id, username=admin.username, roles=roles, permissions=perms, must_change_password=must_change)
    _cookie(response, token, settings.jwt_access_token_expire_minutes)
    return {"redirect": "/dashboard/admin/view-as"}


def read_only_reason(access_token: str | None) -> str | None:
    """For the guard in app/main.py: the administrator's name when this browser is in a "view as" session, else None."""
    if not access_token:
        return None
    try:
        payload = jwt.decode(access_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    return payload.get("va_name") or ("an administrator" if payload.get("va") else None)


# Paths that stay usable during a view (leaving it, refreshing, signing out). Everything else that changes data is refused.
READ_ONLY_ALLOWED = ("/api/v1/view-as/stop", "/api/v1/auth/refresh", "/api/v1/auth/logout")