"""1Bill (1LINK) bill payment: the bank network asks "what is due on consumer number X?" (bill inquiry) and then tells us
"X has been paid" (bill payment). Off until ONE_BILL_ENABLED=1 and the username/password from the bank are in .env.

  * The amount asked for is the challan (what is still due on that instalment), plus the late fee built up on it to the
    day of payment (when Settings -> Fees "add the late fee to Fines" is on), plus the convenience charge
    (Settings -> Fees -> Online payment). The bank shows the payer the amount for the day they pay.
  * A payment must be for exactly that amount, otherwise it is refused and the payer's bank does not take the money.
  * When it is accepted: the challan is paid with a receipt (the usual path, ChallanService), its late fee becomes a fine
    that is marked paid with its own receipt, the payer is told in the app and by SMS, and the whole transaction is kept
    in one_bill_transactions for the daily match with the bank. The convenience charge is not a fee: it is only listed
    there (it offsets what the bank charges).
  * The bank may send the same payment twice (time-outs): the second one is answered "OK" and changes nothing.

THE MESSAGE FORMAT below (field names, response codes, amount and date formats) follows the common 1Bill biller
specification. 1LINK gives each biller its own document during onboarding: everything that may need adjusting is in
the FORMAT section of this file, so the rest does not change.
"""
from __future__ import annotations

import hmac
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import institute_today
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------- FORMAT (adjust to the bank's document)
OK, NOT_FOUND, BLOCKED, ALREADY_PAID, INVALID, ERROR = "00", "01", "02", "03", "04", "05"
UNPAID, PAID, BLOCKED_STATUS = "U", "P", "B"


def money(value: float) -> str:
    """Rs. 1,500.00 -> "+0000000150000" (sign, then 13 digits with the last two after the decimal point)."""
    return "+" + f"{int(round(value * 100)):013d}"


def parse_money(text: str) -> float:
    t = (text or "").strip().replace(",", "")
    if not t:
        raise ValueError("empty amount")
    if "." in t:                                         # some banks send a plain decimal
        return round(float(t), 2)
    return round(int(t.lstrip("+")) / 100, 2)


def yyyymmdd(d: date | None) -> str:
    return d.strftime("%Y%m%d") if d else ""


def parse_yyyymmdd(text: str | None) -> date | None:
    t = (text or "").strip()
    try:
        return datetime.strptime(t, "%Y%m%d").date() if t else None
    except ValueError:
        return None


def inquiry_reply(code: str, **kw) -> dict:
    return {"response_Code": code, "consumer_Detail": kw.get("name", "")[:30], "bill_status": kw.get("status", ""),
            "due_date": yyyymmdd(kw.get("due_date")), "amount_within_dueDate": money(kw["within"]) if "within" in kw else "",
            "amount_after_dueDate": money(kw["after"]) if "after" in kw else "", "billing_month": kw.get("month", ""),
            "date_paid": yyyymmdd(kw.get("date_paid")), "amount_paid": money(kw["paid"])[1:] if kw.get("paid") else "",
            "tran_auth_Id": kw.get("auth", ""), "reserved": ""}


def payment_reply(code: str, reference: str = "") -> dict:
    return {"response_Code": code, "Identification_parameter": reference, "reserved": ""}


# ---------------------------------------------------------------- switching on, and who may call
def is_enabled() -> bool:
    return bool(int(settings.one_bill_enabled or 0) and settings.one_bill_username and settings.one_bill_password)


def credentials_ok(username: str | None, password: str | None) -> bool:
    if not is_enabled():
        return False
    return hmac.compare_digest((username or "").encode(), settings.one_bill_username.encode()) and \
        hmac.compare_digest((password or "").encode(), settings.one_bill_password.encode())


def address_ok(ip: str) -> bool:
    allowed = [a.strip() for a in (settings.one_bill_allowed_ips or "").split(",") if a.strip()]
    return not allowed or ip in allowed


# ---------------------------------------------------------------- amounts
def convenience_for(base: float) -> float:
    fixed = float(getattr(settings, "one_bill_convenience_fixed", 0) or 0)
    pct = float(getattr(settings, "one_bill_convenience_percent", 0) or 0)
    return round(max(fixed, 0) + max(pct, 0) * base / 100, 2)


async def _late_fee_today(session: AsyncSession, challan, assignment) -> float:
    """The late fee built up on this challan's instalment and not yet charged as a fine (0 when late fees are not
    added to Fines on payment: then nothing is collected for it here)."""
    if not int(getattr(settings, "fee_late_fee_auto_post", 1) or 0):
        return 0.0
    from app.fees.assignment_service import FeeAssignmentRepository, FeeStatusCalculator, _late_fee_per_day, late_pause

    per_day = await _late_fee_per_day(session, assignment)
    if not per_day:
        return 0.0
    entries = await FeeAssignmentRepository(session).get_ledger(assignment.id)
    for r in FeeStatusCalculator.installments(entries, per_day, pause=late_pause(assignment)):
        if r["ledger_entry_id"] == challan.fee_ledger_entry_id:
            return round(max(float(r["late_fee"]) - float(r.get("late_fee_posted") or 0), 0.0), 2)
    return 0.0


async def amounts(session: AsyncSession, challan, assignment) -> dict:
    base = round(float(challan.amount), 2)
    late = await _late_fee_today(session, challan, assignment)
    conv = convenience_for(base)
    return {"fee": base, "late": late, "convenience": conv, "within": round(base + conv, 2), "after": round(base + late + conv, 2)}


async def _payer_name(session: AsyncSession, assignment) -> str:
    from app.fees.models import PayerType

    if assignment.payer_type == PayerType.STUDENT:
        from app.students.models import Student

        s = await session.get(Student, assignment.payer_id)
        return f"{s.first_name} {s.last_name}".strip() if s else ""
    from app.employees.models import Employee

    e = await session.get(Employee, assignment.payer_id)
    return e.full_name if e else ""


async def _challan(session: AsyncSession, consumer_number: str):
    from app.fees.payment_models import PaymentChallan

    number = (consumer_number or "").strip()
    if not number:
        return None
    return (await session.execute(select(PaymentChallan).where(PaymentChallan.consumer_number == number))).scalar_one_or_none()


# ---------------------------------------------------------------- bill inquiry
async def inquiry(session: AsyncSession, consumer_number: str) -> dict:
    from app.fees.models import AssignmentStatus, FeeAssignment
    from app.fees.payment_models import ChallanStatus, OneBillTransaction

    ch = await _challan(session, consumer_number)
    if ch is None:
        return inquiry_reply(NOT_FOUND)
    a = await session.get(FeeAssignment, ch.fee_assignment_id)
    name = await _payer_name(session, a) if a else ""
    month = ch.due_date.strftime("%y%m") if ch.due_date else ""
    if ch.status == ChallanStatus.PAID:
        tx = (await session.execute(select(OneBillTransaction).where(OneBillTransaction.challan_id == ch.id)
                                    .order_by(OneBillTransaction.received_at.desc()))).scalars().first()
        return inquiry_reply(OK, name=name, status=PAID, due_date=ch.due_date, within=float(ch.amount), after=float(ch.amount), month=month,
                             date_paid=(tx.received_on if tx else (ch.paid_at.date() if ch.paid_at else None)),
                             paid=float(tx.amount) if tx else float(ch.amount), auth=tx.tran_auth_id if tx else "")
    if ch.status != ChallanStatus.PENDING or a is None or a.status != AssignmentStatus.ACTIVE:
        return inquiry_reply(BLOCKED, name=name, status=BLOCKED_STATUS, due_date=ch.due_date, month=month)
    amt = await amounts(session, ch, a)
    return inquiry_reply(OK, name=name, status=UNPAID, due_date=ch.due_date, within=amt["within"], after=amt["after"], month=month)


# ---------------------------------------------------------------- bill payment
async def payment(session: AsyncSession, *, consumer_number: str, tran_auth_id: str, amount_text: str, tran_date: str | None,
                  tran_time: str | None, bank_mnemonic: str | None) -> dict:
    from app.fees.assignment_service import ChallanService
    from app.fees.payment_models import ChallanStatus, OneBillTransaction

    auth = (tran_auth_id or "").strip()
    bank = (bank_mnemonic or "").strip()[:20]
    if not auth or len(auth) > 30:
        return payment_reply(INVALID)
    try:
        paid = parse_money(amount_text)
    except ValueError:
        return payment_reply(INVALID)
    ch = await _challan(session, consumer_number)
    if ch is None:
        return payment_reply(NOT_FOUND)

    svc = ChallanService(session)
    ch, a = await svc._locked(ch.id)                    # one payment at a time on this fee
    same = (await session.execute(select(OneBillTransaction).where(
        OneBillTransaction.bank_mnemonic == bank, OneBillTransaction.tran_auth_id == auth,
        OneBillTransaction.consumer_number == ch.consumer_number))).scalar_one_or_none()
    if same is not None:                                 # the bank sent the same payment again
        return payment_reply(OK, same.receipt_number or "") if abs(float(same.amount) - paid) < 0.005 else payment_reply(INVALID)
    if ch.status == ChallanStatus.PAID:
        return payment_reply(ALREADY_PAID)
    from app.fees.models import AssignmentStatus

    if ch.status != ChallanStatus.PENDING or a.status != AssignmentStatus.ACTIVE:
        return payment_reply(BLOCKED)
    amt = await amounts(session, ch, a)
    day = parse_yyyymmdd(tran_date) or institute_today()
    late_due = ch.due_date is not None and day > ch.due_date
    expected = amt["after"] if late_due else amt["within"]
    if abs(paid - expected) > 0.005:
        logger.warning("one_bill_amount_mismatch", consumer=ch.consumer_number, paid=paid, expected=expected)
        return payment_reply(INVALID)

    t0 = datetime.now(timezone.utc)
    reference = f"1BILL-{bank + '-' if bank else ''}{auth}"
    ch = await svc.confirm_from_webhook(consumer_number=ch.consumer_number, gateway_reference=reference)
    from app.fees.models import FeeLedgerEntry

    credit = await session.get(FeeLedgerEntry, ch.paid_entry_id) if ch.paid_entry_id else None
    fine_receipts: list[str] = []
    late_paid = 0.0
    if late_due and amt["late"] > 0.005:
        late_paid, fine_receipts = await _pay_late_fee_fines(session, a, reference, since=t0)
    tx = OneBillTransaction(consumer_number=ch.consumer_number, tran_auth_id=auth, bank_mnemonic=bank, amount=paid, fee_amount=amt["fee"],
                            late_fee_amount=late_paid, convenience_amount=amt["convenience"],
                            tran_date=parse_yyyymmdd(tran_date), tran_time=(tran_time or "").strip()[:6] or None,
                            received_at=t0, received_on=institute_today(), challan_id=ch.id,
                            receipt_number=credit.receipt_number if credit else None, fine_receipt_numbers=", ".join(fine_receipts) or None)
    session.add(tx)
    await session.flush()
    if abs(paid - amt["fee"] - late_paid - amt["convenience"]) > 0.005:       # shown as "to check" in the daily match
        logger.error("one_bill_late_fee_not_matched", consumer=ch.consumer_number, paid=paid, late_expected=amt["late"], late_paid=late_paid)
    await _tell_by_sms(session, a, paid, ch.consumer_number)
    return payment_reply(OK, tx.receipt_number or "")


async def _pay_late_fee_fines(session: AsyncSession, assignment, reference: str, *, since: datetime) -> tuple[float, list[str]]:
    """The late fee was just added to Fines by the payment (post_settled_late_fees): mark those fines paid by this 1Bill payment."""
    from app.fines.models import Fine, FinePayerType, FineSource, FineStatus
    from app.fines.service import FineService
    from app.fees.models import PayerType

    payer_type = FinePayerType.STUDENT if assignment.payer_type == PayerType.STUDENT else FinePayerType.EMPLOYEE
    fines = (await session.execute(select(Fine).where(Fine.payer_type == payer_type, Fine.payer_id == assignment.payer_id,
                                                      Fine.source == FineSource.FEE_LATE_PAYMENT, Fine.status == FineStatus.PENDING,
                                                      Fine.created_at >= since).order_by(Fine.created_at))).scalars().all()
    total, receipts = 0.0, []
    for i, f in enumerate(fines, start=1):
        paid = await FineService(session).mark_paid(f.id, actor_id=None, note="Paid with the fee through 1Bill", method="ONLINE",
                                                    reference=f"{reference}-LATE{i}")
        total += float(paid.amount)
        receipts.append(paid.receipt_number)
    return round(total, 2), receipts


async def _tell_by_sms(session: AsyncSession, assignment, amount: float, consumer_number: str) -> None:
    from app.fees.models import PayerType

    if assignment.payer_type != PayerType.STUDENT:
        return
    try:
        from app.sms.service import SmsService

        async with session.begin_nested():
            await SmsService(session).queue_for_student(
                assignment.payer_id, event="FEE_PAID_ONLINE",
                body=f"{settings.app_name}: Rs. {amount:,.0f} received through 1Bill for challan {consumer_number}. Thank you.")
    except Exception as exc:  # noqa: BLE001 - the payment is recorded whatever happens to the SMS
        logger.warning("one_bill_sms_failed", error=str(exc)[:200])


# ---------------------------------------------------------------- the daily match for accounts
async def day_report(session: AsyncSession, day: date) -> dict:
    from app.fees.payment_models import OneBillTransaction

    rows = (await session.execute(select(OneBillTransaction).where(OneBillTransaction.received_on == day)
                                  .order_by(OneBillTransaction.received_at))).scalars().all()
    items = [{"received_at": t.received_at, "consumer_number": t.consumer_number, "bank": t.bank_mnemonic, "tran_auth_id": t.tran_auth_id,
              "tran_date": t.tran_date, "amount": float(t.amount), "fee": float(t.fee_amount), "late_fee": float(t.late_fee_amount),
              "convenience": float(t.convenience_amount), "receipt_number": t.receipt_number, "fine_receipts": t.fine_receipt_numbers,
              "to_check": round(float(t.amount) - float(t.fee_amount) - float(t.late_fee_amount) - float(t.convenience_amount), 2)}
             for t in rows]
    by_bank: dict = {}
    for i in items:
        b = by_bank.setdefault(i["bank"] or "—", {"bank": i["bank"] or "—", "count": 0, "amount": 0.0})
        b["count"] += 1
        b["amount"] = round(b["amount"] + i["amount"], 2)
    totals = {k: round(sum(i[k] for i in items), 2) for k in ("amount", "fee", "late_fee", "convenience", "to_check")}
    days = [d for d in (await session.execute(select(OneBillTransaction.received_on, func.count()).group_by(OneBillTransaction.received_on)
                                              .order_by(OneBillTransaction.received_on.desc()).limit(31))).all()]
    return {"day": day, "enabled": is_enabled(), "count": len(items), "totals": totals, "by_bank": sorted(by_bank.values(), key=lambda b: b["bank"]),
            "items": items, "recent_days": [{"day": d, "count": n} for d, n in days]}
