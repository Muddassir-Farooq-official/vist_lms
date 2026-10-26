"""What happens to a student's fees when their status changes (owner decisions 4 and 6, audit FIN-13).

- Suspended or dropped: late fees stop counting from that day (FeeAssignment.late_fee_paused_from). Made active again:
  they count again from that day (late_fee_paused_until); the days in between never carry a late fee.
- Dropped (left the institute): every installment not yet due is cancelled, unless the office keeps them for this
  student. Anything already due stays owed. Cancelling is a CONCESSION row per installment (the ledger is never edited),
  so it shows on the statement and can be traced in the audit log.
- Withdrawn from one course: the same, for that course's own fee only.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, LedgerEntryType, PayerType

CANCEL_PREFIX = "Cancelled: not yet due when"


async def _active_student_fees(session: AsyncSession, student_id: uuid.UUID) -> list[FeeAssignment]:
    return list((await session.execute(select(FeeAssignment).where(
        FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.payer_id == student_id, FeeAssignment.status == AssignmentStatus.ACTIVE))).scalars())


async def cancel_future_installments(session: AsyncSession, assignment: FeeAssignment, *, after: date, why: str, actor_id) -> float:
    """Cancel what is still unpaid on installments due after `after`. Returns the total cancelled."""
    from app.fees.assignment_service import FeeStatusCalculator, late_pause, lock_assignment, sync_challans
    from app.fees.repository import FeeAssignmentRepository

    assignment = await lock_assignment(session, assignment.id)
    if assignment.status != AssignmentStatus.ACTIVE:
        return 0.0
    entries = await FeeAssignmentRepository(session).get_ledger(assignment.id)
    now, total = datetime.now(timezone.utc), 0.0
    for r in FeeStatusCalculator.installments(entries, None, pause=late_pause(assignment)):
        if r["due_date"] is None or r["due_date"] <= after or r["remaining"] <= 0.005:
            continue
        session.add(FeeLedgerEntry(fee_assignment_id=assignment.id, entry_type=LedgerEntryType.CONCESSION, amount=r["remaining"],
                                   installment_number=r["installment_number"], related_entry_id=r["ledger_entry_id"],
                                   description=f"{CANCEL_PREFIX} {why}"[:255], created_by=actor_id, created_at=now))
        total += r["remaining"]
    if total:
        await session.flush()
        await sync_challans(session, assignment)
        await AuditService(session).log(actor_user_id=actor_id, action="fee.future_installments_cancelled", entity_type="fee_assignment",
                                        entity_id=assignment.id, metadata={"amount": round(total, 2), "after": after.isoformat(), "why": why})
    return round(total, 2)


async def apply_student_status(session: AsyncSession, student_id: uuid.UUID, *, old_status, new_status, actor_id, keep_future_fees: bool = False,
                               reason: str | None = None) -> dict:
    from app.students.models import StudentStatus

    today = institute_today()
    out = {"paused": 0, "resumed": 0, "cancelled": 0.0}
    for a in await _active_student_fees(session, student_id):
        if new_status in (StudentStatus.SUSPENDED, StudentStatus.DROPPED):
            if a.late_fee_paused_from is None or a.late_fee_paused_until is not None:
                a.late_fee_paused_from, a.late_fee_paused_until = today, None
                out["paused"] += 1
        elif new_status == StudentStatus.ACTIVE and a.late_fee_paused_from is not None and a.late_fee_paused_until is None:
            a.late_fee_paused_until = today
            out["resumed"] += 1
        if new_status == StudentStatus.DROPPED and not keep_future_fees:
            out["cancelled"] += await cancel_future_installments(session, a, after=today, why=f"the student left on {today:%d %b %Y}", actor_id=actor_id)
    await session.flush()
    if any(out.values()) or keep_future_fees:
        await AuditService(session).log(actor_user_id=actor_id, action="fee.status_rules_applied", entity_type="student", entity_id=student_id,
                                        metadata={**out, "new_status": new_status.value, "kept_future_fees": keep_future_fees, "reason": reason})
    return out


async def cancel_course_fee_after_withdrawal(session: AsyncSession, enrollment, *, actor_id) -> float:
    rows = (await session.execute(select(FeeAssignment, FeeStructure).join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id).where(
        FeeAssignment.enrollment_id == enrollment.id, FeeAssignment.status == AssignmentStatus.ACTIVE))).all()
    total = 0.0
    today = institute_today()
    for a, structure in rows:
        total += await cancel_future_installments(session, a, after=today, why=f"withdrew from the course on {today:%d %b %Y}", actor_id=actor_id)
    return total
