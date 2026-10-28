"""Scholarships and concessions: a discount that reduces what a student owes, recorded as ledger entries so no history is rewritten."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today, local_date
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.fees.assignment_service import FeeStatusCalculator, late_pause, lock_assignment, sync_challans
from app.fees.models import (
    AssignmentStatus,
    FeeAssignment,
    FeeLedgerEntry,
    FeeStructure,
    FeeType,
    LedgerEntryType,
    PayerType,
    Scholarship,
    ScholarshipKind,
    ScholarshipStatus,
)
from app.fees.payment_models import ChallanStatus, PaymentChallan
from app.fees.repository import FeeAssignmentRepository
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.students.models import Student, StudentStatus

OPENING_CODE = "OPENING"          # the fee type of "previous dues": scholarships never reduce money owed from before
Q = Decimal("0.01")


def covers(s: "Scholarship", due: date | None) -> bool:
    """Whether a scholarship covers an installment due on `due` (inside valid_from .. valid_until, both included)."""
    if due is None:
        return True
    return due >= s.valid_from and (s.valid_until is None or due <= s.valid_until)


def _d(v) -> Decimal:
    return Decimal(str(v))


def _live_concessions(entries, scholarship_id) -> list:
    """This scholarship's discounts still standing on a fee: (CONCESSION row, amount not yet withdrawn)."""
    back: dict = {}
    for e in entries:
        if e.entry_type == LedgerEntryType.DEBIT and e.related_entry_id is not None:
            back[e.related_entry_id] = back.get(e.related_entry_id, Decimal(0)) + _d(e.amount)
    out = []
    for e in entries:
        if e.entry_type == LedgerEntryType.CONCESSION and e.scholarship_id == scholarship_id:
            left = (_d(e.amount) - back.get(e.id, Decimal(0))).quantize(Q)
            if left > 0:
                out.append((e, left))
    return out


def _jsonable(d: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else str(v) if isinstance(v, (uuid.UUID, Decimal)) else v.value if hasattr(v, "value") else v)
            for k, v in d.items()}


def _describe(k, old, new) -> str:
    label = {"name": "name", "kind": "type", "value": "amount", "fee_structure_id": "fee", "fee_type_id": "kind of fee", "auto_apply": "applies to later fees",
             "valid_from": "from", "valid_until": "until", "reason": "reason"}[k]
    if k in ("fee_structure_id", "fee_type_id", "reason"):
        return f"{label} changed"
    fmt = lambda v: "open-ended" if v is None else (v.value if hasattr(v, "value") else f"{v:%d %b %Y}" if isinstance(v, date) else f"{v}")
    return f"{label} {fmt(old)} \u2192 {fmt(new)}"


class ScholarshipService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FeeAssignmentRepository(session)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ granting and ending
    async def get(self, scholarship_id: uuid.UUID) -> Scholarship:
        s = await self.session.get(Scholarship, scholarship_id)
        if s is None:
            raise ResourceNotFoundError("Scholarship not found.")
        return s

    async def grant(self, *, student_id: uuid.UUID, name: str, kind: ScholarshipKind, value: float, fee_structure_id: uuid.UUID | None, auto_apply: bool,
                    valid_from: date | None, valid_until: date | None, reason: str, actor_id: uuid.UUID, approved: bool = False,
                    fee_type_id: uuid.UUID | None = None):
        """Give a scholarship or discount. Unless `approved`, an administrator other than the person asking must approve it
        first (owner decision 2/10): then the approval request is returned and nothing is applied yet."""
        student = await self.session.get(Student, student_id)
        if student is None or student.status != StudentStatus.ACTIVE:
            raise BusinessRuleViolationError("Choose an active student.")
        value = _d(value)
        if kind == ScholarshipKind.PERCENT and not (0 < value <= 100):
            raise BusinessRuleViolationError("A percentage scholarship must be more than 0 and at most 100.")
        if kind == ScholarshipKind.FIXED and value <= 0:
            raise BusinessRuleViolationError("The amount must be more than 0.")
        if len((reason or "").strip()) < 5:
            raise BusinessRuleViolationError("Give the reason for this scholarship (it is kept on record).")
        if fee_structure_id is not None and await self.session.get(FeeStructure, fee_structure_id) is None:
            raise BusinessRuleViolationError("That fee does not exist.")
        if fee_structure_id is not None and fee_type_id is not None:
            raise BusinessRuleViolationError("Choose either one fee, or one kind of fee for every semester, not both.")
        if fee_type_id is not None:
            ft = await self.session.get(FeeType, fee_type_id)
            if ft is None:
                raise BusinessRuleViolationError("That kind of fee does not exist.")
            if ft.code == OPENING_CODE:
                raise BusinessRuleViolationError("A scholarship never reduces previous dues.")
        start = valid_from or await self._default_start()
        if valid_until is not None and valid_until < start:
            raise BusinessRuleViolationError("The end date cannot be before the start date.")
        if not approved:
            from app.approvals.service import gate

            what = f"{float(value):g}%" if kind == ScholarshipKind.PERCENT else f"Rs. {float(value):,.0f}"
            req = await gate(self.session, kind="SCHOLARSHIP", actor_id=actor_id, target_id=None, reason=reason,
                             amount=None if kind == ScholarshipKind.PERCENT else float(value),
                             payload={"student_id": student_id, "name": name, "kind": kind.value, "value": float(value), "fee_structure_id": fee_structure_id,
                                      "fee_type_id": fee_type_id, "auto_apply": auto_apply, "valid_from": valid_from, "valid_until": valid_until, "reason": reason},
                             summary=f"{name.strip()} ({what}) for {student.first_name} {student.last_name} ({student.roll_number or 'no roll no.'})")
            if req is not None:
                return req, None
        s = Scholarship(student_id=student_id, name=name.strip(), kind=kind, value=float(value), fee_structure_id=fee_structure_id, fee_type_id=fee_type_id, auto_apply=auto_apply, valid_from=start,
                        valid_until=valid_until, status=ScholarshipStatus.ACTIVE, reason=reason.strip(), granted_by=actor_id)
        self.session.add(s)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="scholarship.granted", entity_type="scholarship", entity_id=s.id,
                             after={"student_id": str(student_id), "kind": kind.value, "value": float(value), "reason": reason.strip()})
        applied, total = 0, Decimal(0)
        for assignment in await self.repo.list_for_payer(PayerType.STUDENT, student_id):
            got = await self.apply_to_assignment(s, assignment, actor_id=actor_id, notify=False)
            if got > 0:
                applied += 1; total += got
        if applied and student.user_id:
            await create_notification(self.session, user_id=student.user_id, category=NotificationCategory.FEE, title=f"Scholarship applied: {s.name}",
                                      message=f"Rs. {total:,.2f} has been taken off what you owe.", link="/dashboard/student/fees")
        return s, {"assignments_updated": applied, "total_concession": float(total)}

    async def _default_start(self) -> date:
        """With no start date given, a scholarship covers the current term: from the start of the latest open academic
        term that has begun (so this term's installments already due are included, earlier terms' dues are not).
        Without registered terms, from 1 January of this year."""
        from app.academics.terms import AcademicTerm, TermStatus

        today = institute_today()
        started = (await self.session.execute(select(AcademicTerm.start_date).where(
            AcademicTerm.status == TermStatus.OPEN, AcademicTerm.start_date.is_not(None), AcademicTerm.start_date <= today)
            .order_by(AcademicTerm.start_date.desc()).limit(1))).scalar_one_or_none()
        return started or date(today.year, 1, 1)

    async def end(self, scholarship_id: uuid.UUID, *, actor_id: uuid.UUID, end_date: date | None = None, reason: str | None = None) -> dict:
        """Stop a scholarship from `end_date` (default today). Installments due on or before that day keep their discount;
        the discount on installments due AFTER it is withdrawn, so the student owes the full amount for them, unless the
        installment was already paid in full at the discounted amount (money taken on those terms stays as it was).
        The ledger is append-only: each withdrawal is a new row linked to the discount it undoes."""
        s = await self.get(scholarship_id)
        today = institute_today()
        finishing = s.status == ScholarshipStatus.ENDED
        if finishing:
            # Ended before this release, when ending left the discount on later installments: finish it now.
            ended_day = s.valid_until or (local_date(s.ended_at) if s.ended_at else today)
            end_date = end_date or ended_day
        end_date = end_date or today
        if end_date > today:
            raise BusinessRuleViolationError("To end it on a future date, edit its \u201cUntil\u201d date instead.")
        before = {"valid_until": s.valid_until, "status": s.status.value}
        fees, total = await self._withdraw(s, after=end_date, actor_id=actor_id, label=f"Scholarship ended: {s.name}")
        if finishing and total <= 0:
            raise BusinessRuleViolationError("This scholarship has already ended, and no unpaid installment after that day still has its discount.")
        s.valid_until = end_date if s.valid_until is None else min(s.valid_until, end_date)
        if not finishing:
            s.status, s.ended_at, s.ended_by = ScholarshipStatus.ENDED, datetime.now(timezone.utc), actor_id
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="scholarship.ended", entity_type="scholarship", entity_id=s.id,
                             before=_jsonable(before), after={"valid_until": end_date.isoformat(), "withdrawn": float(total), "fees_updated": fees,
                                                              "reason": (reason or "").strip() or None})
        await self._tell_student(s, f"Scholarship ended: {s.name}",
                                 f"It covered installments due up to {end_date:%d %b %Y}. Rs. {total:,.2f} was added back to later unpaid installments." if total > 0
                                 else f"It covered installments due up to {end_date:%d %b %Y}.")
        return {"id": s.id, "status": s.status, "valid_until": s.valid_until, "withdrawn": float(total), "fees_updated": fees}

    async def reset_all(self, *, actor_id: uuid.UUID, note: str) -> list[dict]:
        """One-off clean start: end EVERY scholarship / discount and take its discount off every installment that is not
        yet paid in full (whatever its due date). Installments already paid in full keep the discount they were paid at,
        so nobody owes extra on money already accepted. Nothing is deleted: each removal is a new ledger row linked to the
        discount it undoes, and each scholarship stays on record as ended. Write-offs and cancelled previous dues are not
        scholarships and are not touched. Students are not notified (the office re-grants straight after).
        Returns one row per scholarship for the report."""
        today = institute_today()
        rows = []
        stmt = select(Scholarship, Student).join(Student, Student.id == Scholarship.student_id).order_by(Student.roll_number, Scholarship.created_at)
        for s, st in (await self.session.execute(stmt)).all():
            was = s.status
            fees, back = await self._withdraw(s, after=None, actor_id=actor_id, label=f"Discounts reset: {s.name}")
            kept = Decimal(0)
            for a in await self.repo.list_for_payer(PayerType.STUDENT, s.student_id):
                kept += sum((amt for _, amt in _live_concessions(await self.repo.get_ledger(a.id), s.id)), Decimal(0))
            if was == ScholarshipStatus.ACTIVE:
                s.status, s.ended_at, s.ended_by = ScholarshipStatus.ENDED, datetime.now(timezone.utc), actor_id
                s.valid_until = max(s.valid_from, min(s.valid_until or today, today))
            if was == ScholarshipStatus.ACTIVE or back > 0:
                await self.audit.log(actor_user_id=actor_id, action="scholarship.reset", entity_type="scholarship", entity_id=s.id,
                                     before={"status": was.value}, after={"status": "ENDED", "withdrawn": float(back), "kept_on_paid": float(kept),
                                                                         "fees_updated": fees, "note": note})
            rows.append({"roll_number": st.roll_number or "", "student": f"{st.first_name} {st.last_name}", "scholarship": s.name,
                         "amount": f"{float(s.value):g}%" if s.kind == ScholarshipKind.PERCENT else f"Rs {float(s.value):,.2f}",
                         "status_before": was.value, "removed_from_unpaid": float(back), "kept_on_paid_installments": float(kept), "fees_updated": fees})
        await self.session.flush()
        return rows

    # ------------------------------------------------------------------ editing
    EDITABLE = ("name", "kind", "value", "fee_structure_id", "fee_type_id", "auto_apply", "valid_from", "valid_until", "reason")

    async def update(self, scholarship_id: uuid.UUID, changes: dict, *, actor_id: uuid.UUID, note: str, approved: bool = False,
                     always_ask: bool = False):
        """Change a running scholarship. A change that gives the student MORE (higher value, another type or scope, earlier
        start, later or no end, applying to later fees again) needs another administrator's approval, like granting.
        The new terms apply to unpaid installments only: the discount on them is withdrawn and worked out again;
        installments already paid in full keep the discount they were paid with. Returns (scholarship | approval request, summary)."""
        s = await self.get(scholarship_id)
        if s.status != ScholarshipStatus.ACTIVE:
            raise BusinessRuleViolationError("Only a running scholarship can be changed.")
        if len((note or "").strip()) < 5:
            raise BusinessRuleViolationError("Say why it is being changed (kept on record).")
        changes = {k: v for k, v in changes.items() if k in self.EDITABLE}
        if "kind" in changes and not isinstance(changes["kind"], ScholarshipKind):
            changes["kind"] = ScholarshipKind(changes["kind"])
        new = {k: getattr(s, k) for k in self.EDITABLE}
        new["value"] = _d(new["value"])
        new.update(changes)
        if "value" in changes:
            new["value"] = _d(changes["value"])
        if new["fee_structure_id"] is not None and new["fee_type_id"] is not None:
            if "fee_structure_id" in changes and "fee_type_id" not in changes:
                new["fee_type_id"] = None
            elif "fee_type_id" in changes and "fee_structure_id" not in changes:
                new["fee_structure_id"] = None
            else:
                raise BusinessRuleViolationError("Choose either one fee, or one kind of fee for every semester, not both.")
        # same checks as granting
        if len((new["name"] or "").strip()) < 3:
            raise BusinessRuleViolationError("Give it a name.")
        if new["kind"] == ScholarshipKind.PERCENT and not (0 < new["value"] <= 100):
            raise BusinessRuleViolationError("A percentage scholarship must be more than 0 and at most 100.")
        if new["kind"] == ScholarshipKind.FIXED and new["value"] <= 0:
            raise BusinessRuleViolationError("The amount must be more than 0.")
        if len((new["reason"] or "").strip()) < 5:
            raise BusinessRuleViolationError("Give the reason for this scholarship (it is kept on record).")
        if new["fee_structure_id"] is not None and await self.session.get(FeeStructure, new["fee_structure_id"]) is None:
            raise BusinessRuleViolationError("That fee does not exist.")
        if new["fee_type_id"] is not None:
            ft = await self.session.get(FeeType, new["fee_type_id"])
            if ft is None:
                raise BusinessRuleViolationError("That kind of fee does not exist.")
            if ft.code == OPENING_CODE:
                raise BusinessRuleViolationError("A scholarship never reduces previous dues.")
        if new["valid_from"] is None:
            raise BusinessRuleViolationError("Give the date it covers installments from.")
        if new["valid_until"] is not None and new["valid_until"] < new["valid_from"]:
            raise BusinessRuleViolationError("The end date cannot be before the start date.")
        old = {k: getattr(s, k) for k in self.EDITABLE}
        old["value"] = _d(old["value"])
        changed = [k for k in self.EDITABLE if new[k] != old[k]]
        if not changed:
            raise BusinessRuleViolationError("Nothing was changed.")
        gives_more = (new["kind"] != old["kind"] or new["value"] > old["value"]
                      or new["fee_structure_id"] != old["fee_structure_id"] or new["fee_type_id"] != old["fee_type_id"]
                      or new["valid_from"] < old["valid_from"]
                      or (old["valid_until"] is not None and (new["valid_until"] is None or new["valid_until"] > old["valid_until"]))
                      or (new["auto_apply"] and not old["auto_apply"]))
        if (gives_more or always_ask) and not approved:
            from app.approvals.service import gate

            student = await self.session.get(Student, s.student_id)
            req = await gate(self.session, kind="SCHOLARSHIP_EDIT", actor_id=actor_id, target_id=s.id, reason=note,
                             amount=float(new["value"]) if new["kind"] == ScholarshipKind.FIXED else None,
                             payload={"scholarship_id": s.id, "changes": {k: new[k] for k in changed}, "note": note},
                             summary=f"Change {s.name} for {student.first_name} {student.last_name} ({student.roll_number or 'no roll no.'}): "
                                     + "; ".join(_describe(k, old[k], new[k]) for k in changed))
            if req is not None:
                return req, None
        # 1. take the discount back off every installment not yet paid in full, 2. apply the new terms to them
        fees_back, back = await self._withdraw(s, after=None, actor_id=actor_id, label=f"Scholarship changed: {s.name}")
        for k in changed:
            setattr(s, k, float(new[k]) if k == "value" else (new[k].strip() if isinstance(new[k], str) else new[k]))
        await self.session.flush()
        applied = Decimal(0)
        for assignment in await self.repo.list_for_payer(PayerType.STUDENT, s.student_id):
            applied += await self.apply_to_assignment(s, assignment, actor_id=actor_id, notify=False)
        await self.audit.log(actor_user_id=actor_id, action="scholarship.changed", entity_type="scholarship", entity_id=s.id,
                             before=_jsonable({k: old[k] for k in changed}), after={**_jsonable({k: new[k] for k in changed}), "note": note.strip(),
                                                                                    "withdrawn": float(back), "applied": float(applied)})
        if back > 0 or applied > 0:
            await self._tell_student(s, f"Scholarship updated: {s.name}", f"Your unpaid installments now carry Rs. {applied:,.2f} off (was Rs. {back:,.2f}).")
        return s, {"withdrawn": float(back), "applied": float(applied), "changed": changed}

    async def _tell_student(self, s: Scholarship, title: str, message: str) -> None:
        student = await self.session.get(Student, s.student_id)
        if student and student.user_id:
            await create_notification(self.session, user_id=student.user_id, category=NotificationCategory.FEE, title=title, message=message,
                                      link="/dashboard/student/fees")

    async def _withdraw(self, s: Scholarship, *, after: date | None, actor_id: uuid.UUID, label: str) -> tuple[int, Decimal]:
        """Undo this scholarship's discount on installments due after `after` (all, when None) that are not yet paid in full.
        Returns (fees changed, total withdrawn)."""
        fees, total = 0, Decimal(0)
        now = datetime.now(timezone.utc)
        for assignment in await self.repo.list_for_payer(PayerType.STUDENT, s.student_id):
            if assignment.status != AssignmentStatus.ACTIVE:
                continue
            await lock_assignment(self.session, assignment.id)
            entries = await self.repo.get_ledger(assignment.id)
            live = _live_concessions(entries, s.id)
            if not live:
                continue
            structure = await self.session.get(FeeStructure, assignment.fee_structure_id)
            rows = {r["ledger_entry_id"]: r for r in FeeStatusCalculator.installments(
                entries, float(structure.late_fee_per_day) if structure.late_fee_per_day else None, pause=late_pause(assignment))}
            debits = {e.id: e for e in entries if e.entry_type == LedgerEntryType.DEBIT}
            here = Decimal(0)
            for c, amount in live:
                row, debit = rows.get(c.related_entry_id), debits.get(c.related_entry_id)
                if row is None or debit is None:
                    continue
                if after is not None and row["due_date"] is not None and row["due_date"] <= after:
                    continue                                                     # due inside the scholarship's dates: keeps it
                if row["remaining"] <= 0.005 and row["paid"] > 0.005:
                    continue                                                     # already paid in full at the discounted amount
                self.session.add(FeeLedgerEntry(fee_assignment_id=assignment.id, entry_type=LedgerEntryType.DEBIT, amount=float(amount),
                                                installment_number=c.installment_number, due_date=debit.due_date, related_entry_id=c.id,
                                                scholarship_id=s.id, description=label, created_by=actor_id, created_at=now))
                here += amount
            if here > 0:
                await self.session.flush()
                await sync_challans(self.session, assignment)
                await self.audit.log(actor_user_id=actor_id, action="scholarship.withdrawn", entity_type="fee_assignment", entity_id=assignment.id,
                                     after={"scholarship_id": str(s.id), "total": float(here), "after": after.isoformat() if after else None})
                fees += 1; total += here
        return fees, total

    async def listing(self, *, student_id: uuid.UUID | None, status: ScholarshipStatus | None) -> list[dict]:
        stmt = select(Scholarship, Student).join(Student, Student.id == Scholarship.student_id).order_by(Scholarship.created_at.desc())
        if student_id:
            stmt = stmt.where(Scholarship.student_id == student_id)
        if status:
            stmt = stmt.where(Scholarship.status == status)
        rows = (await self.session.execute(stmt.limit(500))).all()
        given = {}
        if rows:
            for sid, *total in (await self.session.execute(
                select(FeeLedgerEntry.scholarship_id, FeeLedgerEntry.amount, FeeLedgerEntry.entry_type).where(
                    FeeLedgerEntry.scholarship_id.in_([s.id for s, _ in rows]), FeeLedgerEntry.entry_type.in_([LedgerEntryType.CONCESSION, LedgerEntryType.DEBIT]))
            )).all():
                sign = 1 if total[1] == LedgerEntryType.CONCESSION else -1      # a DEBIT tagged with a scholarship withdraws its discount
                given[sid] = given.get(sid, 0.0) + sign * float(total[0])
        return [{"id": s.id, "student_id": s.student_id, "student_name": f"{st.first_name} {st.last_name}", "roll_number": st.roll_number, "name": s.name, "kind": s.kind, "value": float(s.value),
                 "fee_structure_id": s.fee_structure_id, "fee_type_id": s.fee_type_id, "auto_apply": s.auto_apply, "valid_from": s.valid_from, "valid_until": s.valid_until, "status": s.status, "reason": s.reason, "ended_at": s.ended_at,
                 "given_so_far": round(given.get(s.id, 0.0), 2), "created_at": s.created_at} for s, st in rows]

    # ------------------------------------------------------------------ applying it to fees
    async def apply_to_assignment(self, s: Scholarship, assignment: FeeAssignment, *, actor_id: uuid.UUID, notify: bool = True) -> Decimal:
        """Take the scholarship off this fee's unpaid installments. Returns the total taken off (0 if it does not apply)."""
        if assignment.payer_type != PayerType.STUDENT or assignment.payer_id != s.student_id or assignment.status != AssignmentStatus.ACTIVE:
            return Decimal(0)
        if s.fee_structure_id is not None and s.fee_structure_id != assignment.fee_structure_id:
            return Decimal(0)
        await lock_assignment(self.session, assignment.id)
        structure = await self.session.get(FeeStructure, assignment.fee_structure_id)
        fee_type = await self.session.get(FeeType, structure.fee_type_id)
        if fee_type is not None and fee_type.code == OPENING_CODE:
            return Decimal(0)
        if s.fee_type_id is not None and structure.fee_type_id != s.fee_type_id:
            return Decimal(0)                                                    # e.g. a tuition-only discount leaves exam and library fees alone
        entries = await self.repo.get_ledger(assignment.id)
        live = _live_concessions(entries, s.id)
        has = {c.related_entry_id for c, _ in live}                             # installments that already carry it
        kept = sum((a for _, a in live), Decimal(0))
        # Only installments due inside the scholarship's dates: one granted for next term leaves this term alone,
        # and one that starts this term never reaches dues from earlier terms.
        rows = [r for r in FeeStatusCalculator.installments(entries, float(structure.late_fee_per_day) if structure.late_fee_per_day else None,
                                                            pause=late_pause(assignment))
                if r["remaining"] > 0.005 and covers(s, r["due_date"]) and r["ledger_entry_id"] not in has]
        if not rows:
            return Decimal(0)
        shares: dict = {}
        if s.kind == ScholarshipKind.PERCENT:
            for r in rows:
                shares[r["ledger_entry_id"]] = min((_d(r["charged"]) * _d(s.value) / 100).quantize(Q, ROUND_HALF_UP), _d(r["remaining"]))
        else:
            budget = min(_d(s.value) - kept, sum(_d(r["remaining"]) for r in rows))   # a fixed amount is for the whole fee
            if budget <= 0:
                return Decimal(0)
            gross = sum(_d(r["charged"]) for r in rows)
            left = budget
            for r in rows:
                share = min((budget * _d(r["charged"]) / gross).quantize(Q, ROUND_HALF_UP), _d(r["remaining"]), left)
                shares[r["ledger_entry_id"]] = share; left -= share
            for r in rows:                                                       # put any rounding crumb on an installment that still has room
                room = _d(r["remaining"]) - shares[r["ledger_entry_id"]]
                if left > 0 and room > 0:
                    extra = min(room, left); shares[r["ledger_entry_id"]] += extra; left -= extra
        now, total = datetime.now(timezone.utc), Decimal(0)
        for r in rows:
            share = shares.get(r["ledger_entry_id"], Decimal(0))
            if share <= 0:
                continue
            self.session.add(FeeLedgerEntry(fee_assignment_id=assignment.id, entry_type=LedgerEntryType.CONCESSION, amount=float(share), installment_number=r["installment_number"],
                                            related_entry_id=r["ledger_entry_id"], scholarship_id=s.id, description=f"Scholarship: {s.name}", created_by=actor_id, created_at=now))
            total += share
        if total <= 0:
            return Decimal(0)
        await self.session.flush()
        await sync_challans(self.session, assignment)
        await self.audit.log(actor_user_id=actor_id, action="scholarship.applied", entity_type="fee_assignment", entity_id=assignment.id,
                             after={"scholarship_id": str(s.id), "total": float(total)})
        if notify:
            student = await self.session.get(Student, assignment.payer_id)
            if student and student.user_id:
                await create_notification(self.session, user_id=student.user_id, category=NotificationCategory.FEE, title=f"Scholarship applied: {s.name}",
                                          message=f"Rs. {total:,.2f} off {structure.name}.", link="/dashboard/student/fees")
        return total

    async def _refresh_challans(self, assignment: FeeAssignment, structure: FeeStructure) -> None:
        """Kept for callers elsewhere; the shared sync keeps challans in step with installments."""
        await sync_challans(self.session, assignment)

    async def apply_active_to_assignment(self, assignment: FeeAssignment, *, actor_id: uuid.UUID) -> None:
        """Called whenever a fee is charged to a student: any running scholarship that covers it is applied straight away."""
        if assignment.payer_type != PayerType.STUDENT:
            return
        stmt = select(Scholarship).where(Scholarship.student_id == assignment.payer_id, Scholarship.status == ScholarshipStatus.ACTIVE, Scholarship.auto_apply.is_(True))
        for s in (await self.session.execute(stmt)).scalars().all():
            await self.apply_to_assignment(s, assignment, actor_id=actor_id)        # covers() picks the installments inside its dates