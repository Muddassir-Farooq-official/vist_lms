"""Editable institute policy.

The values live on the global `settings` object (app/core/config.py), which every
module already reads. Defaults come from .env; an administrator's saved value
(table policy_settings) overrides it. Each worker re-reads the table at most
every REFRESH_SECONDS (see refresh_if_stale, called by a middleware), so a change
reaches every worker within a minute and the one that saved it immediately.
Only keys this module has itself overridden are ever restored, so nothing else
that adjusts `settings` is disturbed.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationAppError

REFRESH_SECONDS = 60


@dataclass(frozen=True)
class Policy:
    key: str
    label: str
    group: str
    kind: type
    minimum: float
    maximum: float
    help: str


POLICY: tuple[Policy, ...] = (
    Policy("attendance_eligibility_threshold_percent", "Minimum attendance to pass (%)", "Attendance", float, 0, 100,
           "Below this, a student cannot pass the course even with good marks."),
    Policy("attendance_fine_threshold_percent", "Fine at or below (%)", "Attendance", float, 0, 100,
           "At attendance lock, a student at or below this is fined."),
    Policy("attendance_drop_threshold_percent", "Drop from course at or below (%)", "Attendance", float, 0, 100,
           "At attendance lock, a student at or below this is withdrawn from the course."),
    Policy("attendance_fine_amount", "Attendance fine (Rs.)", "Attendance", float, 0, 1_000_000, "Amount of each attendance fine."),
    Policy("attendance_marking_window_minutes", "Teacher marking window (minutes)", "Attendance", int, 5, 1440,
           "How long after a class starts the teacher can still mark it; after that it needs a correction request."),
    Policy("missed_class_alerts", "Alert when a teacher on campus misses a class", "Attendance", int, 0, 1,
           "When a class's marking window closes with no attendance although its teacher had checked in, every administrator gets a notification (Attendance -> Teachers)."),
    Policy("withdrawal_deadline_days", "Course withdrawal deadline (days after the term starts)", "Attendance", int, 0, 200,
           "Before it a withdrawal leaves no mark; after it the course shows as W on the transcript and only the office can withdraw a student."),
    Policy("promotion_max_courses_to_clear", "Promotion: max failed courses still to clear", "Promotion", int, 0, 20,
           "Advance Semester holds back a student with more failed (not yet passed) courses than this."),
    Policy("promotion_min_cgpa", "Promotion: minimum CGPA", "Promotion", float, 0, 4,
           "Advance Semester holds back a student whose CGPA is below this."),
    Policy("admit_card_requires_fees_cleared", "Withhold admit card while fees are overdue (1 = yes, 0 = no)", "Exams", int, 0, 1,
           "With 1, a student with any overdue fee instalment gets an admit card marked WITHHELD until it is paid."),
    Policy("supplementary_max_grade_points", "Supplementary: highest grade points a pass can earn (0 = no cap)", "Exams", float, 0, 4,
           "Many universities cap a supplementary pass, e.g. 2.0 = at most a C. 0 means the normal grade applies."),
    Policy("sms_on_attendance_fine", "SMS when an attendance fine is issued (1 = yes, 0 = no)", "SMS", int, 0, 1, "Each SMS costs money."),
    Policy("sms_on_datesheet", "SMS when a datesheet is published (1 = yes, 0 = no)", "SMS", int, 0, 1, "Each SMS costs money."),
    Policy("sms_on_results", "SMS when results are published (1 = yes, 0 = no)", "SMS", int, 0, 1, "One SMS per student per course published."),
    Policy("sms_on_fee_overdue", "SMS weekly reminder for overdue fees (1 = yes, 0 = no)", "SMS", int, 0, 1, "At most once a week per student."),
    Policy("sms_on_admission", "SMS applicants selected in a merit list (1 = yes, 0 = no)", "SMS", int, 0, 1, "One SMS per offered applicant."),
    Policy("sms_on_security_alert", "SMS administrators on a security alert (1 = yes, 0 = no)", "SMS", int, 0, 1,
           "Sent to administrators' own mobile numbers (their employee record) and the institute phone if it is a mobile. "
           "At most once per half hour for the same kind of attack."),
    Policy("sms_to_guardian", "Also send each SMS to the guardian (1 = yes, 0 = no)", "SMS", int, 0, 1,
           "Uses the guardian's contact number on the student record."),
    Policy("fee_backdated_grace_days", "Late fee grace for backdated instalments (days)", "Fees", int, 0, 90,
           "An instalment created after its due date accrues a late fee only this many days after creation."),
    Policy("purchase_over_estimate_percent", "Purchases: re-approve the price when it is more than this % over the estimate", "Accounts", float, 0, 1000,
           "The goods are received, but cannot be paid until an administrator approves the higher price."),
    Policy("fee_late_fee_cap", "Late fee: most one instalment can build up (Rs., 0 = no cap)", "Fees", float, 0, 1_000_000,
           "Stops a forgotten instalment from growing a late fee without limit."),
    Policy("fee_late_fee_grace_days", "Late fee: grace days after the due date", "Fees", int, 0, 60,
           "No late fee is charged for this many days after an instalment falls due; after that it counts from the end of the grace days. "
           "It never runs while a student is suspended or after they leave."),
    Policy("fee_late_fee_double_every_days", "Late fee: double the daily rate every ... days (0 = never)", "Fees", int, 0, 365,
           "E.g. 7 with a Rs. 50 daily late fee: Rs. 50 a day in the first week late, Rs. 100 a day in the second, Rs. 200 in the third, and so on. "
           "0 keeps one flat daily rate. It applies to every overdue instalment, counting its late days from the due date."),
    Policy("fee_late_fee_max_per_day", "Late fee: highest daily rate (Rs., 0 = no limit)", "Fees", float, 0, 1_000_000,
           "The doubling stops at this daily rate. Together with the cap on one instalment's total late fee, it keeps a forgotten fee from growing without limit."),
    Policy("purchase_second_approval_above", "Purchases: a second administrator approves above (Rs., 0 = never)", "Accounts", float, 0, 100_000_000,
           "A purchase above this amount needs approval from an administrator other than the one who asked for it or recorded it."),
    Policy("fee_payment_max_backdate_days", "Payments may be dated back at most (days)", "Fees", int, 1, 365,
           "Staff cannot record a payment dated earlier than this."),
    Policy("payroll_default_working_days", "Working days in a month (0 = each person's own working days)", "Payroll", int, 0, 31,
           "A day's pay = basic salary / working days. 0: each person's own working days in the month, from their timing in Staff attendance "
           "(someone who works 8 days a month earns basic / 8 a day). Or one number for everyone, e.g. 26. Can also be changed per payroll and per person."),
    Policy("payroll_attendance_deductions", "Deduct absences and late arrivals from pay (1 = yes, 0 = no)", "Payroll", int, 0, 1,
           "Uses Staff attendance. Someone with no attendance recorded in a month is never treated as absent."),
    Policy("payroll_casual_leaves_staff", "Casual leaves per month: non-teaching staff", "Payroll", int, 0, 10,
           "Absences up to this many in a month are not deducted."),
    Policy("payroll_casual_leaves_teacher", "Casual leaves per month: teachers", "Payroll", int, 0, 10,
           "0 = every absence is deducted."),
    Policy("payroll_late_marks_per_day", "Late arrivals / early leavings that equal one day's pay", "Payroll", int, 1, 31,
           "E.g. 4: four late arrivals or early check-outs in a month cut one day's pay (eight cut two)."),
    Policy("payroll_missing_checkout_counts", "A day with no check-out counts as leaving early (1 = yes, 0 = no)", "Payroll", int, 0, 1,
           "Stops people from simply not marking out."),
    Policy("payroll_security_percent", "Security deposit: % of basic held each month", "Payroll", float, 0, 100,
           "Held from each payslip until the total equals one basic salary. 0 = off. Can be changed per employee."),
    Policy("payroll_advance_max_salary_multiple", "Salary advance: most one employee may owe (x basic salary)", "Payroll", float, 0.1, 6,
           "1 = at most one month's basic salary outstanding in advances, including the one being asked for. Every advance needs administrator approval."),
    Policy("todo_reminder_hour", "Remind staff with no to-do list today, from this hour (0 = never)", "Tasks", int, 0, 23,
           "E.g. 15: at 3 pm, staff who have not written today's list get a notification (once a day, not on Sundays). "
           "Their list then shows in the monthly to-do report."),
    Policy("amount_display", "Amounts on dashboard cards", "Display", int, 0, 2,
           "Full (Rs. 4,045,000), K / M (Rs. 4.05M) or lakh / crore (Rs. 40.45 lakh). Only the summary cards change; tables, receipts, "
           "challans, reports and downloads always show the full amount. Hover or tap a card to see the exact figure."),
    Policy("graduation_min_cgpa", "Lowest CGPA that can graduate", "Exams", float, 0, 4,
           "The degree audit asks for every curriculum course passed and at least this CGPA before a student is marked graduated."),
)
BY_KEY = {p.key: p for p in POLICY}

_defaults: dict[str, object] = {}
_applied: set[str] = set()
_last_refresh = 0.0


def _default(key: str):
    if key not in _defaults:
        _defaults[key] = getattr(settings, key)
    return _defaults[key]


def current() -> list[dict]:
    return [{"key": p.key, "label": p.label, "group": p.group, "type": p.kind.__name__, "min": p.minimum, "max": p.maximum,
             "help": p.help, "value": getattr(settings, p.key), "default": _default(p.key), "overridden": p.key in _applied}
            for p in POLICY]


def _coerce(p: Policy, raw) -> float | int:
    try:
        v = p.kind(raw)
        if p.kind is int and float(raw) != int(float(raw)):
            raise ValueError
    except (TypeError, ValueError):
        raise ValidationAppError(f"{p.label}: must be a {'whole number' if p.kind is int else 'number'}.")
    if not (p.minimum <= v <= p.maximum):
        raise ValidationAppError(f"{p.label}: must be between {p.minimum:g} and {p.maximum:g}.")
    return v


def _check_consistency(values: dict) -> None:
    drop, fine, elig = (values["attendance_drop_threshold_percent"], values["attendance_fine_threshold_percent"],
                        values["attendance_eligibility_threshold_percent"])
    if not (drop < fine <= elig):
        raise ValidationAppError(
            f"Attendance thresholds must satisfy drop ({drop:g}%) < fine ({fine:g}%) <= minimum to pass ({elig:g}%).")


async def apply_from_db(session: AsyncSession) -> None:
    global _last_refresh
    from app.policy.models import PolicySetting

    rows = {r.key: r.value for r in (await session.execute(select(PolicySetting))).scalars()}
    for p in POLICY:
        _default(p.key)
        if p.key in rows:
            setattr(settings, p.key, p.kind(float(rows[p.key])) if p.kind is int else p.kind(rows[p.key]))
            _applied.add(p.key)
        elif p.key in _applied:
            setattr(settings, p.key, _default(p.key))
            _applied.discard(p.key)
    # The institute's name, logos, colour and bank details (Settings -> Institute) are refreshed the same way.
    try:
        from app.branding.service import apply_from_db as apply_branding

        async with session.begin_nested():
            await apply_branding(session)
    except Exception:  # noqa: BLE001 - before its migration the table does not exist; keep the current values
        pass
    _last_refresh = time.monotonic()


async def refresh_if_stale() -> None:
    if time.monotonic() - _last_refresh < REFRESH_SECONDS:
        return
    from app.core.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await apply_from_db(session)
    except Exception:  # the table may not exist yet (before migrations); keep the current values
        globals()["_last_refresh"] = time.monotonic()


async def update(session: AsyncSession, changes: dict, *, actor_id: uuid.UUID) -> list[dict]:
    from app.audit.service import AuditService
    from app.policy.models import PolicySetting

    unknown = [k for k in changes if k not in BY_KEY]
    if unknown:
        raise ValidationAppError(f"Unknown setting(s): {', '.join(unknown)}.")
    await apply_from_db(session)
    new = {p.key: getattr(settings, p.key) for p in POLICY}
    reset = [k for k, v in changes.items() if v is None]
    for k, v in changes.items():
        new[k] = _default(k) if v is None else _coerce(BY_KEY[k], v)
    _check_consistency(new)

    before = {k: getattr(settings, k) for k in changes}
    now = datetime.now(timezone.utc)
    for k, v in changes.items():
        row = await session.get(PolicySetting, k)
        if k in reset:
            if row is not None:
                await session.delete(row)
            continue
        if row is None:
            session.add(PolicySetting(key=k, value=str(new[k]), updated_by=actor_id, updated_at=now))
        else:
            row.value, row.updated_by, row.updated_at = str(new[k]), actor_id, now
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action="policy.updated", entity_type="policy", entity_id=None,
                                    before={k: before[k] for k in changes}, after={k: new[k] for k in changes})
    await apply_from_db(session)
    return current()


def reset_for_tests() -> None:
    """Put back every value this module overrode and force a re-read on the next request."""
    global _last_refresh
    for k in list(_applied):
        setattr(settings, k, _defaults[k])
    _applied.clear()
    from app.branding.service import reset_for_tests as reset_branding

    reset_branding()
    _last_refresh = 0.0