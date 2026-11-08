"""Daily review of money changes (Activity -> Money changes; administrators only).

Lists the changes that move money outside the normal "student pays the fee" path, so a mistake (a discount on the
wrong fee, a waiver, a back-dated payment) is seen the next morning, not months later:
  discounts given, changed or ended; fines waived; payments reversed or marked unpaid; fee due dates moved;
  payments dated earlier than the day they were entered; refunds; late-fee rates changed; future instalments cancelled;
  credit moved between fees; income / expense entries voided.
Each comes from the activity log (nothing new is recorded); an administrator ticks it "checked" (money_review_checks).
Every morning from 8 AM the administrators get one notice with yesterday's count (once a day).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

KINDS: dict[str, tuple[str, str]] = {          # action -> (label, group)
    "scholarship.granted": ("Discount given", "discount"),
    "scholarship.changed": ("Discount changed", "discount"),
    "scholarship.ended": ("Discount ended", "discount"),
    "fine.waived": ("Fine waived", "waiver"),
    "fee.payment_reversed": ("Fee payment reversed", "reversal"),
    "fine.payment_reversed": ("Fine payment reversed", "reversal"),
    "fee.payment_marked_unpaid_by_staff": ("Online payment marked unpaid", "reversal"),
    "fee.due_date_changed": ("Due date moved", "due_date"),
    "fee.due_date_changed_for_all": ("Due date moved for everyone on a fee", "due_date"),
    "fee.payment_recorded": ("Back-dated payment", "backdated"),          # only those dated before the day of entry
    "fee_refund.requested": ("Refund requested", "refund"),
    "fee_refund.approved": ("Refund approved", "refund"),
    "fee_refund.paid": ("Refund paid out", "refund"),
    "fee.late_fee_rate_changed": ("Late-fee rate changed", "rule"),
    "fee.future_installments_cancelled": ("Future instalments cancelled", "rule"),
    "fee.credit_moved": ("Credit moved between fees", "rule"),
    "accounts.income_voided": ("Income entry voided", "voided"),
    "accounts.expense_voided": ("Expense entry voided", "voided"),
}
GROUPS = {"discount": "Discounts", "waiver": "Fines waived", "reversal": "Payments reversed", "due_date": "Due dates moved",
          "backdated": "Back-dated payments", "refund": "Refunds", "rule": "Fee rules", "voided": "Voided entries"}
LINKS = {"discount": "/dashboard/admin/fees#scholarships", "waiver": "/dashboard/admin/fines", "refund": "/dashboard/admin/refunds",
         "voided": "/dashboard/admin/finance"}


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    from app.core.clock import _tz

    tz = _tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _local_day(ts: datetime) -> date:
    from app.core.clock import _tz

    return ts.astimezone(_tz()).date()


def _is_backdated(row) -> bool:
    paid_on = (row.after or {}).get("paid_on")
    try:
        return bool(paid_on) and date.fromisoformat(str(paid_on)[:10]) < _local_day(row.created_at)
    except ValueError:
        return False


def _money(v) -> str:
    try:
        return f"Rs. {float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _detail(row) -> str:
    """One readable line from what the activity log kept."""
    a, b = row.after or {}, row.before or {}
    act = row.action
    if act == "scholarship.granted":
        val = a.get("value")
        what = f"{val:g}%" if a.get("kind") == "PERCENT" and val is not None else _money(val)
        scope = "every fee" if not a.get("fee_type_id") and not a.get("fee_structure_id") else "one kind of fee" if a.get("fee_type_id") else "one fee"
        return f"{what} off {scope}" + (f": {a.get('reason')}" if a.get("reason") else "")
    if act == "scholarship.changed":
        keys = ", ".join(k for k in a if k not in ("note",))
        return f"changed {keys}" + (f": {a.get('note')}" if a.get("note") else "")
    if act == "scholarship.ended":
        return f"ended from {a.get('valid_until')}; {_money(a.get('withdrawn', 0))} discount withdrawn from unpaid instalments"
    if act == "fee.payment_recorded":
        return f"{_money(a.get('amount'))} dated {a.get('paid_on')} (receipt {a.get('receipt_number')})"
    if act in ("fee.payment_reversed",):
        return f"receipt {b.get('receipt')}, {_money(b.get('amount'))}" + (f": {a.get('reason')}" if a.get("reason") else "")
    if act in ("fee.due_date_changed", "fee.due_date_changed_for_all"):
        return f"{b.get('due_date') or ''} → {a.get('due_date')}" + (f": {a.get('reason')}" if a.get("reason") else "")
    if act == "fee.late_fee_rate_changed":
        return f"{_money(b.get('late_fee_per_day'))} → {_money(a.get('late_fee_per_day'))} a day" + (f": {a.get('reason')}" if a.get("reason") else "")
    if act.startswith("fee_refund."):
        return ", ".join(f"{k} {v}" for k, v in a.items() if k in ("amount", "kind", "voucher", "method", "note") and v not in (None, ""))
    reason = a.get("reason") or a.get("note") or (row.log_metadata or {}).get("reason")
    return str(reason or "")


async def _students(session: AsyncSession, rows: list) -> dict:
    """{audit row id: "Name (roll)"} where the change belongs to a student (best effort, never fails the list)."""
    from app.fees.models import FeeAssignment, FeeLedgerEntry, Scholarship
    from app.students.models import Student

    out: dict = {}
    try:
        assignment_of: dict = {}
        by_type: dict = {}
        for r in rows:
            by_type.setdefault(r.entity_type, []).append(r)
        if by_type.get("scholarship"):
            sch = {s.id: s.student_id for s in (await session.execute(select(Scholarship).where(
                Scholarship.id.in_([r.entity_id for r in by_type["scholarship"] if r.entity_id])))).scalars()}
            for r in by_type["scholarship"]:
                assignment_of[r.id] = ("student", sch.get(r.entity_id))
        if by_type.get("fee_ledger_entry"):
            led = {e.id: e.fee_assignment_id for e in (await session.execute(select(FeeLedgerEntry).where(
                FeeLedgerEntry.id.in_([r.entity_id for r in by_type["fee_ledger_entry"] if r.entity_id])))).scalars()}
            for r in by_type["fee_ledger_entry"]:
                assignment_of[r.id] = ("assignment", led.get(r.entity_id))
        for r in by_type.get("fee_assignment", []):
            assignment_of[r.id] = ("assignment", r.entity_id)
        if by_type.get("fee_refund"):
            from app.fees.refund_models import FeeRefund

            ref = {f.id: f.fee_assignment_id for f in (await session.execute(select(FeeRefund).where(
                FeeRefund.id.in_([r.entity_id for r in by_type["fee_refund"] if r.entity_id])))).scalars()}
            for r in by_type["fee_refund"]:
                assignment_of[r.id] = ("assignment", ref.get(r.entity_id))
        if by_type.get("fine"):
            from app.fines.models import Fine

            fines = {f.id: f for f in (await session.execute(select(Fine).where(Fine.id.in_([r.entity_id for r in by_type["fine"] if r.entity_id])))).scalars()}
            for r in by_type["fine"]:
                f = fines.get(r.entity_id)
                if f is not None and f.payer_type.value == "STUDENT":
                    assignment_of[r.id] = ("student", f.payer_id)
        a_ids = [v for k, v in assignment_of.values() if k == "assignment" and v]
        payer = {a.id: a.payer_id for a in (await session.execute(select(FeeAssignment).where(FeeAssignment.id.in_(a_ids or [uuid.uuid4()])))).scalars()
                 if getattr(a.payer_type, "value", a.payer_type) == "STUDENT"}
        sids = {(payer.get(v) if k == "assignment" else v) for k, v in assignment_of.values()} - {None}
        studs = {s.id: f"{s.first_name} {s.last_name} ({s.roll_number})" for s in (await session.execute(select(Student).where(
            Student.id.in_(sids or {uuid.uuid4()})))).scalars()}
        for rid, (k, v) in assignment_of.items():
            sid = payer.get(v) if k == "assignment" else v
            if sid in studs:
                out[rid] = studs[sid]
    except Exception as exc:  # noqa: BLE001 - names are a convenience; the list must always show
        logger.warning("money_review_names_failed", error=str(exc)[:200])
    return out


async def listing(session: AsyncSession, *, day: date, unchecked_only: bool = False) -> dict:
    from app.audit.models import AuditLog, MoneyReviewCheck
    from app.auth.models import User

    start, end = _day_bounds(day)
    rows = (await session.execute(select(AuditLog).where(AuditLog.action.in_(list(KINDS)), AuditLog.created_at >= start, AuditLog.created_at < end)
                                  .order_by(AuditLog.created_at.desc()).limit(2000))).scalars().all()
    rows = [r for r in rows if r.action != "fee.payment_recorded" or _is_backdated(r)]
    checks = {c.audit_log_id: c for c in (await session.execute(select(MoneyReviewCheck).where(
        MoneyReviewCheck.audit_log_id.in_([r.id for r in rows] or [uuid.uuid4()])))).scalars()}
    users = {u.id: u.username for u in (await session.execute(select(User).where(
        User.id.in_({r.actor_user_id for r in rows} | {c.checked_by for c in checks.values()} - {None} or {uuid.uuid4()})))).scalars()}
    names = await _students(session, rows)
    items = []
    for r in rows:
        label, group = KINDS[r.action]
        c = checks.get(r.id)
        if unchecked_only and c is not None:
            continue
        items.append({"id": r.id, "at": r.created_at, "kind": r.action, "label": label, "group": group, "group_label": GROUPS[group],
                      "by": users.get(r.actor_user_id) or ("the system" if r.actor_user_id is None else "?"), "student": names.get(r.id),
                      "detail": _detail(r), "link": LINKS.get(group, "/dashboard/admin/fees"),
                      "checked": c is not None, "checked_by": users.get(c.checked_by) if c else None, "checked_at": c.checked_at if c else None,
                      "check_note": c.note if c else None})
    counts: dict = {}
    for i in items:
        counts[i["group_label"]] = counts.get(i["group_label"], 0) + 1
    return {"day": day, "items": items, "total": len(items), "unchecked": sum(1 for i in items if not i["checked"]), "by_group": counts}


async def mark_checked(session: AsyncSession, ids: list[uuid.UUID], *, actor_id: uuid.UUID, note: str | None) -> int:
    from app.audit.models import AuditLog, MoneyReviewCheck

    valid = set((await session.execute(select(AuditLog.id).where(AuditLog.id.in_(ids or [uuid.uuid4()]), AuditLog.action.in_(list(KINDS))))).scalars())
    done = set((await session.execute(select(MoneyReviewCheck.audit_log_id).where(MoneyReviewCheck.audit_log_id.in_(valid or {uuid.uuid4()})))).scalars())
    now = datetime.now(timezone.utc)
    for i in valid - done:
        session.add(MoneyReviewCheck(audit_log_id=i, checked_by=actor_id, checked_at=now, note=(note or "").strip()[:300] or None))
    await session.flush()
    return len(valid - done)


async def unchecked_days(session: AsyncSession, *, days: int = 14) -> list[dict]:
    """The last `days` days that still have unchecked changes (for the "days to review" list)."""
    from app.core.clock import institute_today

    out = []
    today = institute_today()
    for n in range(1, days + 1):
        d = today - timedelta(days=n)
        r = await listing(session, day=d, unchecked_only=True)
        if r["unchecked"]:
            out.append({"day": d, "unchecked": r["unchecked"]})
    return out


async def morning_notice(session: AsyncSession) -> int:
    """From 8 AM, once a day: tell administrators how many money changes yesterday has to review. Never raises."""
    from app.core.background import beat
    from app.core.clock import institute_now
    from app.notifications.email_models import ServiceHeartbeat

    try:
        now = institute_now()
        if now.hour < 8:
            return 0
        today = now.date().isoformat()
        hb = await session.get(ServiceHeartbeat, "money-review")
        if hb is not None and (hb.detail or "").startswith(today):
            return 0
        async with session.begin_nested():
            yesterday = now.date() - timedelta(days=1)
            r = await listing(session, day=yesterday, unchecked_only=True)
            sent = 0
            if r["unchecked"]:
                from app.approvals.service import _users_with_permission
                from app.auth.permissions import Permissions
                from app.notifications.models import NotificationCategory
                from app.notifications.service import create_notification

                parts = ", ".join(f"{n} {g.lower()}" for g, n in r["by_group"].items())
                for uid in await _users_with_permission(session, Permissions.ROLE_MANAGE):
                    await create_notification(session, user_id=uid, category=NotificationCategory.GENERAL,
                                              title=f"{r['unchecked']} money change(s) to review from yesterday",
                                              message=f"{yesterday:%d %b}: {parts}. Open the list and tick each one you have checked.",
                                              link="/dashboard/admin/activity#money")
                    sent += 1
            await beat(session, "money-review", f"{today}: {r['unchecked']} to review, {sent} notified")
        return sent
    except Exception as exc:  # noqa: BLE001 - the notice must never stop the background round
        logger.warning("money_review_notice_failed", error=str(exc)[:300])
        return 0
