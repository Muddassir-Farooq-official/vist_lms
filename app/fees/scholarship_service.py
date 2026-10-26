"""Scholarships and concessions: a discount that reduces what a student owes, recorded as ledger entries so no history is rewritten."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
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
                    valid_from: date | None, valid_until: date | None, reason: str, actor_id: uuid.UUID, approved: bool = False):
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
        start = valid_from or await self._default_start()
        if valid_until is not None and valid_until < start:
            raise BusinessRuleViolationError("The end date cannot be before the start date.")
        if not approved:
            from app.approvals.service import gate

            what = f"{float(value):g}%" if kind == ScholarshipKind.PERCENT else f"Rs. {float(value):,.0f}"
            req = await gate(self.session, kind="SCHOLARSHIP", actor_id=actor_id, target_id=None, reason=reason,
                             amount=None if kind == ScholarshipKind.PERCENT else float(value),
                             payload={"student_id": student_id, "name": name, "kind": kind.value, "value": float(value), "fee_structure_id": fee_structure_id,
                                      "auto_apply": auto_apply, "valid_from": valid_from, "valid_until": valid_until, "reason": reason},
                             summary=f"{name.strip()} ({what}) for {student.first_name} {student.last_name} ({student.roll_number or 'no roll no.'})")
            if req is not None:
                return req, None
        s = Scholarship(student_id=student_id, name=name.strip(), kind=kind, value=float(value), fee_structure_id=fee_structure_id, auto_apply=auto_apply, valid_from=start,
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

    async def end(self, scholarship_id: uuid.UUID, *, actor_id: uuid.UUID) -> Scholarship:
        s = await self.get(scholarship_id)
        if s.status == ScholarshipStatus.ENDED:
            raise BusinessRuleViolationError("This scholarship has already ended.")
        s.status, s.ended_at, s.ended_by = ScholarshipStatus.ENDED, datetime.now(timezone.utc), actor_id
        await self.audit.log(actor_user_id=actor_id, action="scholarship.ended", entity_type="scholarship", entity_id=s.id)
        return s

    async def listing(self, *, student_id: uuid.UUID | None, status: ScholarshipStatus | None) -> list[dict]:
        stmt = select(Scholarship, Student).join(Student, Student.id == Scholarship.student_id).order_by(Scholarship.created_at.desc())
        if student_id:
            stmt = stmt.where(Scholarship.student_id == student_id)
        if status:
            stmt = stmt.where(Scholarship.status == status)
        rows = (await self.session.execute(stmt.limit(500))).all()
        given = {}
        if rows:
            for sid, total in (await self.session.execute(
                select(FeeLedgerEntry.scholarship_id, FeeLedgerEntry.amount).where(FeeLedgerEntry.scholarship_id.in_([s.id for s, _ in rows]), FeeLedgerEntry.entry_type == LedgerEntryType.CONCESSION)
            )).all():
                given[sid] = given.get(sid, 0.0) + float(total)
        return [{"id": s.id, "student_id": s.student_id, "student_name": f"{st.first_name} {st.last_name}", "roll_number": st.roll_number, "name": s.name, "kind": s.kind, "value": float(s.value),
                 "fee_structure_id": s.fee_structure_id, "auto_apply": s.auto_apply, "valid_from": s.valid_from, "valid_until": s.valid_until, "status": s.status, "reason": s.reason,
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
        entries = await self.repo.get_ledger(assignment.id)
        if any(e.entry_type == LedgerEntryType.CONCESSION and e.scholarship_id == s.id for e in entries):
            return Decimal(0)                                                    # already applied to this fee
        # Only installments due inside the scholarship's dates: one granted for next term leaves this term alone,
        # and one that starts this term never reaches dues from earlier terms.
        rows = [r for r in FeeStatusCalculator.installments(entries, float(structure.late_fee_per_day) if structure.late_fee_per_day else None,
                                                            pause=late_pause(assignment))
                if r["remaining"] > 0.005 and covers(s, r["due_date"])]
        if not rows:
            return Decimal(0)
        shares: dict = {}
        if s.kind == ScholarshipKind.PERCENT:
            for r in rows:
                shares[r["ledger_entry_id"]] = min((_d(r["charged"]) * _d(s.value) / 100).quantize(Q, ROUND_HALF_UP), _d(r["remaining"]))
        else:
            budget = min(_d(s.value), sum(_d(r["remaining"]) for r in rows))
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