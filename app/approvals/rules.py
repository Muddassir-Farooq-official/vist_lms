"""Approval rules: for each kind of action, whether it needs approval and who approves it (Access control -> Approvals).

Without any rule every action works exactly as it always has (DEFAULT). A rule can say, for one kind of action and
optionally only for people holding one role and/or from an amount:
  - NONE      no approval needed: it is carried out at once (recorded as "no approval needed by rule");
  - APPROVE   approved by: DEFAULT (as without rules), ROLE (anyone holding that role), USER (that person),
              HOD (the head of the department concerned) or COURSE_TEACHER (the course's own teacher).
The most specific rule wins: a rule for the asker's role before a rule for anyone; then the highest "from amount"
not above the amount.

Safety:
  - when a rule names who decides, nobody decides their own request (without a rule, each module's own rule applies);
  - an Administrator can always decide (a request is never stuck because the named approver left);
  - if the rule names nobody who could decide (e.g. no HOD for that department), the request falls back to DEFAULT.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    group: str
    about: str
    amount: bool = False                         # rules can depend on an amount (Rs.)
    approvers: tuple = ("DEFAULT", "ROLE", "USER", "HOD")
    default_text: str = "As the system does now"
    allow_none: bool = True


ACTIONS: list[Action] = [
    Action("ATTENDANCE_CORRECTION", "Attendance after the marking window", "Attendance",
           "A teacher records or corrects a class's attendance after the window closed (or someone else does it for them).",
           approvers=("DEFAULT", "ROLE", "USER", "HOD", "COURSE_TEACHER"),
           default_text="The head of the teacher's department, or the attendance office"),
    Action("STAFF_LEAVE", "Staff leave", "Staff", "A staff member's leave application.",
           default_text="The staff attendance office"),
    Action("RESULT_APPROVE", "Course results", "Exams", "A course's result sheet, submitted by its teacher, before it is published.",
           default_text="The exam department (approve results)"),
    Action("TIMETABLE_PUBLISH", "Publishing a timetable", "Timetable", "Putting a new timetable (draft) into use.",
           default_text="No approval (whoever edits the timetable publishes it)"),
    Action("FEE_STATUS", "Waive or cancel a fee", "Fees", "Waiving or cancelling a student's fee.", amount=True,
           default_text="Another administrator (Approvals page)"),
    Action("SCHOLARSHIP", "Scholarship or discount", "Fees", "A new scholarship or discount.", amount=True,
           default_text="Another administrator (Approvals page)"),
    Action("SCHOLARSHIP_EDIT", "Change a scholarship or discount", "Fees", "Changing a scholarship or discount.", amount=True,
           default_text="Another administrator (Approvals page)"),
    Action("PREVIOUS_DUES_CANCEL", "Write off previous dues", "Fees", "Cancelling dues brought from before.", amount=True,
           default_text="Another administrator (Approvals page)"),
    Action("PAYMENT_MOVE", "Move a payment to the right fee", "Fees", "Moving a recorded payment to another fee.", amount=True,
           default_text="Another administrator (Approvals page)"),
    Action("FINE_WAIVE", "Waive a fine", "Fees", "Waiving a student's fine.", amount=True,
           default_text="Another administrator (Approvals page)"),
    Action("FINE_REQUEST", "A fine asked for by staff", "Fees", "A fine the accounts office or library asks for.", amount=True,
           default_text="Whoever may issue fines"),
    Action("REFUND", "Fee refund", "Fees", "Paying money back to a student.", amount=True,
           default_text="Whoever may approve refunds (never the one who asked)"),
    Action("BOOKS_REOPEN", "Reopen closed books", "Accounts", "Reopening an accounting period that was closed.",
           default_text="Another administrator (Approvals page)"),
    Action("EXPENSE", "Expense voucher", "Accounts", "An expense recorded by the accounts office.", amount=True,
           default_text="Whoever may approve vouchers"),
    Action("CLASS_REQUEST", "Make-up or extra class", "Attendance", "A teacher asks for a make-up or extra class.",
           default_text="Another administrator (Approvals page)"),
]
BY_KEY = {a.key: a for a in ACTIONS}
APPROVER_LABELS = {"DEFAULT": "As the system does now", "ROLE": "Anyone with the role", "USER": "This person",
                   "HOD": "The head of the department concerned", "COURSE_TEACHER": "The course's own teacher"}


@dataclass
class Resolution:
    """What a rule says for one request (None rule = DEFAULT)."""
    rule_id: uuid.UUID | None = None
    mode: str = "APPROVE"                         # NONE / APPROVE
    approver_type: str = "DEFAULT"
    approver_value: str | None = None
    text: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def default(self) -> bool:
        return self.mode == "APPROVE" and self.approver_type == "DEFAULT"

    @property
    def no_approval(self) -> bool:
        return self.mode == "NONE"


async def _roles_of(session: AsyncSession, user_id) -> set[str]:
    if user_id is None:
        return set()
    return set((await session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id))).scalars())


@dataclass(frozen=True)
class _R:
    id: uuid.UUID
    action: str
    requester_role: str | None
    min_amount: Decimal | None
    mode: str
    approver_type: str | None
    approver_value: str | None


_CACHE: dict = {"at": 0.0, "rules": None}
CACHE_SECONDS = 10           # every server process re-reads the rules at least this often


def forget() -> None:
    _CACHE["rules"] = None


async def _all_rules(session: AsyncSession) -> dict[str, list[_R]]:
    import time

    from app.access.models import ApprovalRule

    now = time.monotonic()
    if _CACHE["rules"] is None or now - _CACHE["at"] > CACHE_SECONDS:
        rows = (await session.execute(select(ApprovalRule))).scalars().all()
        by: dict[str, list[_R]] = {}
        for r in rows:
            by.setdefault(r.action, []).append(_R(r.id, r.action, r.requester_role, r.min_amount, r.mode, r.approver_type, r.approver_value))
        _CACHE["rules"], _CACHE["at"] = by, now
    return _CACHE["rules"]


async def resolve(session: AsyncSession, action: str, *, requester_id, amount=None) -> Resolution:
    """The rule that applies to this request (or DEFAULT)."""
    rules = (await _all_rules(session)).get(action, [])
    if not rules:
        return Resolution(text=BY_KEY[action].default_text if action in BY_KEY else "")
    roles = await _roles_of(session, requester_id)
    amt = Decimal(str(amount)) if amount is not None else None

    def fits(r) -> bool:
        if r.requester_role and r.requester_role not in roles:
            return False
        if r.min_amount is not None and (amt is None or amt < r.min_amount):
            return False
        return True

    matching = [r for r in rules if fits(r)]
    if not matching:
        return Resolution(text=BY_KEY[action].default_text if action in BY_KEY else "")
    best = sorted(matching, key=lambda r: (r.requester_role is not None, r.min_amount or Decimal(0)), reverse=True)[0]
    return Resolution(rule_id=best.id, mode=best.mode, approver_type=best.approver_type or "DEFAULT", approver_value=best.approver_value,
                      text=await describe(session, best))


async def describe(session: AsyncSession, r) -> str:
    if r.mode == "NONE":
        return "No approval needed"
    t = r.approver_type or "DEFAULT"
    if t == "ROLE":
        name = (await session.execute(select(Role.name).where(Role.code == r.approver_value))).scalar_one_or_none()
        return f"Anyone with the role {name or r.approver_value}"
    if t == "USER":
        return f"Approved by {await _person_name(session, r.approver_value)}"
    return APPROVER_LABELS.get(t, t)


async def _person_name(session: AsyncSession, user_id) -> str:
    from app.employees.models import Employee

    try:
        uid = uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        return "?"
    e = (await session.execute(select(Employee).where(Employee.user_id == uid))).scalar_one_or_none()
    if e is not None:
        return f"{e.first_name} {e.last_name}".strip()
    u = await session.get(User, uid)
    return u.username if u else "?"


def _norm(v: str | None) -> str | None:
    s = " ".join((v or "").split()).lower()
    return s or None


async def department_of(session: AsyncSession, user_id) -> str | None:
    from app.employees.models import Employee

    if user_id is None:
        return None
    return _norm((await session.execute(select(Employee.department).where(Employee.user_id == user_id))).scalar_one_or_none())


async def approvers(session: AsyncSession, res: Resolution, *, requester_id, ctx: dict | None = None) -> list[uuid.UUID]:
    """Who a rule names (active accounts, never the one who asked). Empty for DEFAULT and NONE."""
    ctx = ctx or {}
    ids: set[uuid.UUID] = set()
    if res.no_approval or res.default:
        return []
    if res.approver_type == "ROLE" and res.approver_value:
        ids = set((await session.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id)
                                         .where(Role.code == res.approver_value))).scalars())
    elif res.approver_type == "USER" and res.approver_value:
        try:
            ids = {uuid.UUID(res.approver_value)}
        except ValueError:
            ids = set()
    elif res.approver_type == "HOD":
        from app.employees.models import Employee

        dept = _norm(ctx.get("department"))
        if dept:
            hods = set((await session.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.HOD))).scalars())
            for uid, d in (await session.execute(select(Employee.user_id, Employee.department).where(Employee.user_id.in_(hods)))).all() if hods else []:
                if _norm(d) == dept:
                    ids.add(uid)
    elif res.approver_type == "COURSE_TEACHER":
        if ctx.get("course_teacher_user_id"):
            ids = {ctx["course_teacher_user_id"]}
    if requester_id is not None:
        ids.discard(requester_id)
    if ids:
        ids = set((await session.execute(select(User.id).where(User.id.in_(ids), User.is_active.is_(True)))).scalars())
    return sorted(ids, key=str)


async def effective(session: AsyncSession, action: str, *, requester_id, amount=None, ctx: dict | None = None) -> Resolution:
    """The resolution, with a fallback to DEFAULT when a rule names nobody who could decide."""
    res = await resolve(session, action, requester_id=requester_id, amount=amount)
    if res.no_approval or res.default:
        return res
    named = await approvers(session, res, requester_id=requester_id, ctx=ctx)
    if not named:
        return Resolution(text=(BY_KEY[action].default_text if action in BY_KEY else "") + " (the rule names nobody who could decide)",
                          extra={"fallback": True})
    return res


async def may_decide(session: AsyncSession, action: str, user, *, requester_id, amount=None, ctx: dict | None = None,
                     default_ok: bool) -> bool:
    """Whether this signed-in person may approve or reject this request now. Without a rule naming someone, exactly the
    module's own check (which keeps its own rules about deciding your own request)."""
    res = await effective(session, action, requester_id=requester_id, amount=amount, ctx=ctx)
    if res.default or res.no_approval:
        return default_ok
    if requester_id is not None and str(user.id) == str(requester_id):
        return False
    if Roles.ADMIN in (user.roles or []):
        return True
    return uuid.UUID(str(user.id)) in set(await approvers(session, res, requester_id=requester_id, ctx=ctx))


async def check_decider(session: AsyncSession, action: str, user, *, requester_id, amount=None, ctx: dict | None = None, default_ok: bool) -> None:
    from app.core.exceptions import AuthorizationError

    if not await may_decide(session, action, user, requester_id=requester_id, amount=amount, ctx=ctx, default_ok=default_ok):
        res = await effective(session, action, requester_id=requester_id, amount=amount, ctx=ctx)
        if not (res.default or res.no_approval) and requester_id is not None and str(user.id) == str(requester_id):
            raise AuthorizationError("You asked for this; someone else must decide it.")
        if res.default or res.no_approval:
            raise AuthorizationError("You do not have the right to decide this.")
        raise AuthorizationError(f"This is decided by: {res.text}.")


async def tell_approvers(session: AsyncSession, res: Resolution, *, requester_id, ctx: dict | None, title: str, message: str,
                         link: str = "/dashboard/decisions") -> int:
    """Notify the people a rule names (DEFAULT approvers are told by each module, as before). Never raises."""
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    try:
        ids = await approvers(session, res, requester_id=requester_id, ctx=ctx)
        category = getattr(NotificationCategory, "TASK", None) or list(NotificationCategory)[0]
        for uid in ids:
            async with session.begin_nested():
                await create_notification(session, user_id=uid, category=category, title=title[:200], message=message[:1000], link=link)
        return len(ids)
    except Exception:  # noqa: BLE001
        return 0


async def log_no_approval(session: AsyncSession, action: str, *, actor_id, entity_type: str, entity_id, summary: str) -> None:
    from app.audit.service import AuditService

    await AuditService(session).log(actor_user_id=actor_id, action="approval.not_needed_by_rule", entity_type=entity_type, entity_id=entity_id,
                                    metadata={"kind": action, "summary": summary[:300]})


async def rule_count(session: AsyncSession) -> int:
    from app.access.models import ApprovalRule

    return (await session.execute(select(func.count()).select_from(ApprovalRule))).scalar_one()
