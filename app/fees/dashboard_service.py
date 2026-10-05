from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fees.assignment_service import FeeAssignmentService, FeeStatusCalculator
from app.fees.models import (
    AssignmentStatus,
    FeeLedgerEntry,
    FeeStructure,
    FeeStructureStatus,
    LedgerEntryType,
)
from app.fees.repository import FeeAssignmentRepository


class FeeDashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.assignment_repo = FeeAssignmentRepository(session)
        self.assignment_service = FeeAssignmentService(session)

    async def get_dashboard(self) -> dict:
        total_structures = (
            await self.session.execute(select(func.count()).select_from(FeeStructure))
        ).scalar_one()
        active_structures = (
            await self.session.execute(
                select(func.count())
                .select_from(FeeStructure)
                .where(FeeStructure.status == FeeStructureStatus.ACTIVE)
            )
        ).scalar_one()

        total_collected = (
            await self.session.execute(
                select(func.coalesce(func.sum(FeeLedgerEntry.amount), 0)).where(
                    FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT
                )
            )
        ).scalar_one()
        total_charged = (
            await self.session.execute(
                select(func.coalesce(func.sum(FeeLedgerEntry.amount), 0)).where(
                    FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT
                )
            )
        ).scalar_one() - (
            await self.session.execute(
                select(func.coalesce(func.sum(FeeLedgerEntry.amount), 0)).where(
                    FeeLedgerEntry.entry_type == LedgerEntryType.CONCESSION
                )
            )
        ).scalar_one()      # what is actually owed: charges minus scholarships

        assignments = await self.assignment_repo.list_all()
        active_assignments = [a for a in assignments if a.status == AssignmentStatus.ACTIVE]

        # Real N+1 fix: this used to call get_status() per assignment,
        # each doing its OWN separate ledger query — for N active
        # assignments that's N+1 round trips (or worse, since get_status
        # also re-fetches the structure each time). Batch both the
        # ledger entries and the structures in two queries total,
        # regardless of how many assignments there are, then run the
        # (unavoidably per-assignment) FIFO calculation in Python
        # against already-fetched data.
        total_outstanding = 0.0
        total_overdue = 0.0
        overdue_count = 0

        if active_assignments:
            assignment_ids = [a.id for a in active_assignments]
            all_entries_stmt = select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id.in_(assignment_ids))
            all_entries = (await self.session.execute(all_entries_stmt)).scalars().all()
            entries_by_assignment: dict = {}
            for entry in all_entries:
                entries_by_assignment.setdefault(entry.fee_assignment_id, []).append(entry)

            structure_ids = {a.fee_structure_id for a in active_assignments}
            structures_stmt = select(FeeStructure).where(FeeStructure.id.in_(structure_ids))
            structures_by_id = {s.id: s for s in (await self.session.execute(structures_stmt)).scalars().all()}

            for assignment in active_assignments:
                entries = entries_by_assignment.get(assignment.id, [])
                structure = structures_by_id.get(assignment.fee_structure_id)
                late_fee = float(structure.late_fee_per_day) if structure and structure.late_fee_per_day else None
                status_data = FeeStatusCalculator.compute(entries, late_fee)

                outstanding = status_data["total_charged"] - status_data["total_paid"]
                total_outstanding += max(0.0, outstanding)
                if status_data["total_due_now"] > 0:
                    total_overdue += status_data["total_due_now"]
                    overdue_count += 1

        recent_credits_stmt = (
            select(FeeLedgerEntry)
            .where(FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT)
            .order_by(FeeLedgerEntry.created_at.desc())
            .limit(8)
        )
        recent_credits = (await self.session.execute(recent_credits_stmt)).scalars().all()
        recent_payments: list[dict] = []
        for credit in recent_credits:
            assignment = await self.assignment_repo.get_by_id(credit.fee_assignment_id)
            if assignment is None:
                continue
            structure = await self.session.get(FeeStructure, assignment.fee_structure_id)
            payer_name = await self.assignment_service.resolve_payer_name(assignment.payer_type, assignment.payer_id)
            recent_payments.append(
                {
                    "payer_name": payer_name,
                    "fee_structure_name": structure.name if structure else "Unknown",
                    "amount": float(credit.amount),
                    "created_at": credit.created_at.isoformat(),
                }
            )

        return {
            "total_fee_structures": total_structures,
            "active_fee_structures": active_structures,
            "total_collected": round(float(total_collected), 2),
            "total_charged": round(float(total_charged), 2),
            "total_outstanding": round(total_outstanding, 2),
            "total_overdue": round(total_overdue, 2),
            "overdue_assignment_count": overdue_count,
            "active_assignment_count": len(active_assignments),
            "recent_payments": recent_payments,
        }