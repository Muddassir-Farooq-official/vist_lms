"""Messages to students and their families, in each person's language (English or Urdu).

* Fee reminders: 7 days and 3 days before an unpaid challan is due, and on the due date (from 9 AM, once a day).
  App message to the student and to each linked parent account; SMS to the student and guardian (Settings -> SMS).
* Monday note to parents: when a child needs attention (attendance below 75%, less than half of the marks so far,
  2+ missed assignments) and it changed since the last note. App message + SMS to the guardian.
Both never raise: a problem is logged and tried again the next day.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
REMINDER_DAYS = (7, 3, 0)
UR_KIND = {"attendance": "حاضری", "marks": "نمبر", "assignments": "اسائنمنٹس"}


async def family(session: AsyncSession, student_ids: set) -> dict:
    """{student_id: {"name", "user_id", "lang", "parents": [(user_id, lang)]}}"""
    from app.auth.models import User
    from app.parents.models import ParentStudentLink
    from app.students.models import Student

    if not student_ids:
        return {}
    out = {}
    rows = (await session.execute(select(Student, User.language).outerjoin(User, User.id == Student.user_id).where(Student.id.in_(student_ids)))).all()
    for s, lang in rows:
        out[s.id] = {"name": f"{s.first_name} {s.last_name}".strip(), "first": s.first_name, "user_id": s.user_id, "lang": lang or "en", "parents": []}
    for link, lang in (await session.execute(select(ParentStudentLink, User.language).join(User, User.id == ParentStudentLink.user_id)
                                             .where(ParentStudentLink.student_id.in_(student_ids)))).all():
        if link.student_id in out:
            out[link.student_id]["parents"].append((link.user_id, lang or "en"))
    return out


def _guardian_lang(f: dict) -> str:
    return f["parents"][0][1] if f["parents"] else f["lang"]


async def _notify(session: AsyncSession, user_id, title: str, message: str, link: str) -> None:
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    if user_id:
        await create_notification(session, user_id=user_id, category=NotificationCategory.GENERAL, title=title, message=message, link=link)


async def _sms(session: AsyncSession, student_id, *, event: str, body: str, guardian_body: str, attachment: str | None = None) -> None:
    try:
        from app.sms.service import SmsService

        async with session.begin_nested():
            await SmsService(session).queue_for_student(student_id, event=event, body=body, guardian_body=guardian_body, attachment=attachment)
    except Exception as exc:  # noqa: BLE001 - the app message is already there
        logger.warning("family_sms_failed", event=event, error=str(exc)[:200])


async def _once_a_day(session: AsyncSession, name: str, *, hour: int, weekday: int | None = None) -> str | None:
    """Today's date (iso) when the job should run now, else None."""
    from app.core.clock import institute_now
    from app.notifications.email_models import ServiceHeartbeat

    now = institute_now()
    if now.hour < hour or (weekday is not None and now.weekday() != weekday):
        return None
    hb = await session.get(ServiceHeartbeat, name)
    if hb is not None and (hb.detail or "").startswith(now.date().isoformat()):
        return None
    return now.date().isoformat()


# ---------------------------------------------------------------- fee reminders
def fee_text(lang: str, *, who: str, amount: float, fee: str, due, days: int, consumer: str | None, for_parent: bool) -> tuple[str, str]:
    amt = f"Rs. {amount:,.0f}"
    cn = consumer or ""
    if lang == "ur":
        title = "فیس کی یاد دہانی" if days else "آج فیس کی آخری تاریخ ہے"
        whose = f"{who} کی " if for_parent else ""
        when = (f"{days} دن بعد ({due:%d-%m-%Y}) واجب ہے" if days else "آج لیٹ فیس کے بغیر ادا کرنے کا آخری دن ہے")
        msg = f"{whose}{fee} کی فیس {amt} {when}۔" + (f" کنزیومر نمبر: {cn}۔" if cn else "")
        return title, msg
    title = "Fee reminder" if days else "Last day to pay the fee"
    whose = f"{who}'s " if for_parent else "Your "
    when = (f"is due in {days} days ({due:%d %b})" if days else "is due today: pay today to avoid the late fee")
    msg = f"{whose}{fee} fee of {amt} {when}." + (f" Consumer no. {cn}." if cn else "")
    return title, msg


async def fee_reminders(session: AsyncSession) -> int:
    """Never raises. Returns how many challans were reminded."""
    try:
        day = await _once_a_day(session, "fee-reminders", hour=9)
        if day is None:
            return 0
        from datetime import date

        from app.core.background import beat
        from app.fees.models import AssignmentStatus, FeeAssignment, FeeStructure, PayerType
        from app.fees.payment_models import ChallanStatus, PaymentChallan

        today = date.fromisoformat(day)
        targets = {today + timedelta(days=d): d for d in REMINDER_DAYS}
        rows = (await session.execute(
            select(PaymentChallan, FeeAssignment, FeeStructure.name)
            .join(FeeAssignment, FeeAssignment.id == PaymentChallan.fee_assignment_id)
            .join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id)
            .where(PaymentChallan.status == ChallanStatus.PENDING, PaymentChallan.due_date.in_(list(targets)),
                   FeeAssignment.status == AssignmentStatus.ACTIVE, FeeAssignment.payer_type == PayerType.STUDENT))).all()
        fam = await family(session, {a.payer_id for _, a, _ in rows})
        sent = 0
        for ch, a, fee_name in rows:
            f = fam.get(a.payer_id)
            if f is None or float(ch.amount) <= 0:
                continue
            days = targets[ch.due_date]
            kw = dict(amount=float(ch.amount), fee=fee_name, due=ch.due_date, days=days, consumer=ch.consumer_number)
            t, m = fee_text(f["lang"], who=f["first"], for_parent=False, **kw)
            await _notify(session, f["user_id"], t, m, "/dashboard/student/fees")
            for uid, lang in f["parents"]:
                t2, m2 = fee_text(lang, who=f["first"], for_parent=True, **kw)
                await _notify(session, uid, t2, m2, "/dashboard/parent/fees")
            _, sms_student = fee_text(f["lang"], who=f["first"], for_parent=False, **kw)
            _, sms_guardian = fee_text(_guardian_lang(f), who=f["first"], for_parent=True, **kw)
            await _sms(session, a.payer_id, event="FEE_REMINDER", body=f"{settings.app_name}: {sms_student}", guardian_body=f"{settings.app_name}: {sms_guardian}",
                       attachment=f"CHALLAN:{ch.id}")                     # on WhatsApp the challan itself goes with it
            sent += 1
        await beat(session, "fee-reminders", f"{day} reminded {sent}")
        return sent
    except Exception as exc:  # noqa: BLE001
        logger.warning("fee_reminders_failed", error=str(exc)[:300])
        return 0


# ---------------------------------------------------------------- Monday note to parents
def attention_text(lang: str, name: str, items: list[dict]) -> tuple[str, str]:
    if lang == "ur":
        parts = []
        for i in items:
            v = f"{i['value']}%" if i["kind"] in ("attendance", "marks") else f"{i['value']}"
            extra = " چھوٹ گئیں" if i["kind"] == "assignments" else ""
            parts.append(f"{i['course_code']} ({UR_KIND[i['kind']]} {v}{extra})")
        return f"{name} کو توجہ کی ضرورت ہے", f"{name} کو ان مضامین میں توجہ کی ضرورت ہے: " + "، ".join(parts) + "۔ مزید تفصیل پورٹل پر دیکھیں۔"
    labels = {"attendance": "attendance {v}%", "marks": "marks so far {v}%", "assignments": "{v} assignments missed"}
    parts = [f"{i['course_code']} ({labels[i['kind']].format(v=i['value'])})" for i in items]
    return f"{name} needs attention", f"{name} needs attention in: " + "; ".join(parts) + ". See the parent portal for details."


async def parent_weekly(session: AsyncSession) -> int:
    """Mondays from 9 AM. Never raises. Returns how many families were told."""
    try:
        day = await _once_a_day(session, "parent-weekly", hour=9, weekday=0)
        if day is None:
            return 0
        from datetime import date

        from app.academics.watchlist import for_students
        from app.core.background import beat
        from app.parents.models import ParentAlertState, ParentStudentLink

        linked = set((await session.execute(select(ParentStudentLink.student_id))).scalars())
        from app.students.models import Student

        with_phone = set((await session.execute(select(Student.id))).scalars()) if int(settings.sms_to_guardian) else set()
        signals = await for_students(session, linked | with_phone)
        states = {s.student_id: s for s in (await session.execute(select(ParentAlertState))).scalars()}
        fam = await family(session, set(signals) | set(states))
        told = 0
        for sid in set(signals) | set(states):
            items = signals.get(sid, [])
            sig = "|".join(f"{i['course_code']}:{i['kind']}" for i in items)
            st = states.get(sid)
            if st is None:
                st = ParentAlertState(student_id=sid, signature="")
                session.add(st)
            if sig == st.signature:
                continue
            st.signature = sig[:500]
            if not items or sid not in fam:
                continue                                   # things got better: nothing to send, remember it
            f = fam[sid]
            for uid, lang in f["parents"]:
                t, m = attention_text(lang, f["first"], items)
                await _notify(session, uid, t, m, "/dashboard/parent")
            _, sms = attention_text(_guardian_lang(f), f["first"], items)
            try:
                from app.sms.service import SmsService

                async with session.begin_nested():
                    await SmsService(session).queue_to_guardian(sid, event="PARENT_WEEKLY", body=f"{settings.app_name}: {sms}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("parent_weekly_sms_failed", error=str(exc)[:200])
            st.sent_on = date.fromisoformat(day)
            told += 1
        await session.flush()
        await beat(session, "parent-weekly", f"{day} told {told}")
        return told
    except Exception as exc:  # noqa: BLE001
        logger.warning("parent_weekly_failed", error=str(exc)[:300])
        return 0


async def attention_for(session: AsyncSession, student_id: uuid.UUID, lang: str = "en") -> list[dict]:
    from app.academics.watchlist import for_students

    return (await for_students(session, {student_id})).get(student_id, [])


# ---------------------------------------------------------------- payment received (with the receipt on WhatsApp)
async def payment_messages(session: AsyncSession, *, limit: int = 300) -> int:
    """Each fee payment recorded since the last round -> a message to the student and guardian, with the receipt on WhatsApp.

    Works from the fee ledger itself (every way of paying ends there: counter, bank, 1Bill, online), so nothing is missed and
    nothing is sent twice: the time of the last payment handled is kept in service_heartbeats ("fee-paid-messages"). The first
    round only sets that mark, so payments from before this was switched on are never messaged. Never raises."""
    try:
        from datetime import datetime, timezone

        from app.core.background import beat
        from app.fees.models import FeeAssignment, FeeLedgerEntry, FeeStructure, LedgerEntryType, PayerType
        from app.notifications.email_models import ServiceHeartbeat

        name = "fee-paid-messages"
        hb = await session.get(ServiceHeartbeat, name)
        if hb is None or not hb.detail:
            await beat(session, name, datetime.now(timezone.utc).isoformat())
            return 0
        if not int(getattr(settings, "sms_on_fee_paid", 1)):
            await beat(session, name, datetime.now(timezone.utc).isoformat())        # switched off: do not save them up
            return 0
        mark = datetime.fromisoformat(hb.detail)
        rows = (await session.execute(
            select(FeeLedgerEntry, FeeAssignment.payer_id, FeeStructure.name)
            .join(FeeAssignment, FeeAssignment.id == FeeLedgerEntry.fee_assignment_id)
            .join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id)
            .where(FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT, FeeLedgerEntry.receipt_number.is_not(None),
                   FeeLedgerEntry.created_at > mark, FeeAssignment.payer_type == PayerType.STUDENT)
            .order_by(FeeLedgerEntry.created_at).limit(limit))).all()
        if not rows:
            return 0
        n = 0
        for e, student_id, fee_name in rows:
            body = (f"{settings.app_name}: payment of Rs. {float(e.amount):,.0f} received for {fee_name}. "
                    f"Receipt {e.receipt_number}. Thank you.")
            await _sms(session, student_id, event="FEE_PAID", body=body, guardian_body=body, attachment=f"RECEIPT:{e.id}")
            n += 1
        await beat(session, name, rows[-1][0].created_at.isoformat())
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("payment_messages_failed", error=str(exc)[:300])
        return 0
