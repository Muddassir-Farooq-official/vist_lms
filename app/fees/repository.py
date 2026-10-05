from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fees.models import (
    FeeAssignment,
    FeeLedgerEntry,
    FeeStructure,
    FeeStructureProgram,
    FeeStructureSemester,
    FeeType,
    PayerType,
)
from app.fees.payment_models import PaymentChallan


class FeeTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, fee_type_id: uuid.UUID) -> FeeType | None:
        return await self.session.get(FeeType, fee_type_id)

    async def get_by_code(self, code: str) -> FeeType | None:
        stmt = select(FeeType).where(FeeType.code == code.upper())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, *, active_only: bool = False) -> list[FeeType]:
        stmt = select(FeeType)
        if active_only:
            stmt = stmt.where(FeeType.is_active.is_(True))
        result = await self.session.execute(stmt.order_by(FeeType.name))
        return list(result.scalars())

    async def create(self, fee_type: FeeType) -> FeeType:
        self.session.add(fee_type)
        await self.session.flush()
        return fee_type


class FeeStructureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, structure_id: uuid.UUID) -> FeeStructure | None:
        return await self.session.get(FeeStructure, structure_id)

    async def list_all(self) -> list[FeeStructure]:
        stmt = select(FeeStructure).order_by(FeeStructure.effective_from.desc())
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, structure: FeeStructure) -> FeeStructure:
        self.session.add(structure)
        await self.session.flush()
        return structure

    async def get_program_links(self, structure_id: uuid.UUID) -> list[FeeStructureProgram]:
        stmt = select(FeeStructureProgram).where(FeeStructureProgram.fee_structure_id == structure_id)
        return list((await self.session.execute(stmt)).scalars())

    async def add_program_link(self, link: FeeStructureProgram) -> None:
        self.session.add(link)

    async def get_semester_links(self, structure_id: uuid.UUID) -> list[FeeStructureSemester]:
        stmt = select(FeeStructureSemester).where(FeeStructureSemester.fee_structure_id == structure_id)
        return list((await self.session.execute(stmt)).scalars())

    async def add_semester_link(self, link: FeeStructureSemester) -> None:
        self.session.add(link)

    async def count_assignments(self, structure_id: uuid.UUID) -> int:
        from sqlalchemy import func

        stmt = select(func.count()).select_from(FeeAssignment).where(FeeAssignment.fee_structure_id == structure_id)
        return (await self.session.execute(stmt)).scalar_one()


class FeeAssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, assignment_id: uuid.UUID) -> FeeAssignment | None:
        return await self.session.get(FeeAssignment, assignment_id)

    async def get_existing(
        self, structure_id: uuid.UUID, payer_type: PayerType, payer_id: uuid.UUID
    ) -> FeeAssignment | None:
        """For non-course-scoped fees only — one assignment per
        (structure, payer), ever. Course-scoped fees use
        get_existing_for_enrollment below instead, since they can
        legitimately have several (one per enrollment attempt)."""
        stmt = select(FeeAssignment).where(
            FeeAssignment.fee_structure_id == structure_id,
            FeeAssignment.payer_type == payer_type,
            FeeAssignment.payer_id == payer_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_existing_for_enrollment(self, structure_id: uuid.UUID, enrollment_id: uuid.UUID) -> FeeAssignment | None:
        """For course-scoped fees — has THIS specific enrollment
        (this attempt, first-time or retake) already been charged this
        fee structure? A prior attempt's assignment doesn't count."""
        stmt = select(FeeAssignment).where(
            FeeAssignment.fee_structure_id == structure_id, FeeAssignment.enrollment_id == enrollment_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, assignment: FeeAssignment) -> FeeAssignment:
        self.session.add(assignment)
        await self.session.flush()
        return assignment

    async def list_for_structure(self, structure_id: uuid.UUID) -> list[FeeAssignment]:
        stmt = select(FeeAssignment).where(FeeAssignment.fee_structure_id == structure_id)
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_payer(self, payer_type: PayerType, payer_id: uuid.UUID) -> list[FeeAssignment]:
        stmt = select(FeeAssignment).where(
            FeeAssignment.payer_type == payer_type, FeeAssignment.payer_id == payer_id
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_all(self) -> list[FeeAssignment]:
        stmt = select(FeeAssignment).order_by(FeeAssignment.created_at.desc())
        return list((await self.session.execute(stmt)).scalars())

    async def get_ledger(self, assignment_id: uuid.UUID) -> list[FeeLedgerEntry]:
        stmt = (
            select(FeeLedgerEntry)
            .where(FeeLedgerEntry.fee_assignment_id == assignment_id)
            .order_by(FeeLedgerEntry.due_date, FeeLedgerEntry.created_at)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def add_ledger_entry(self, entry: FeeLedgerEntry) -> FeeLedgerEntry:
        self.session.add(entry)
        await self.session.flush()
        return entry


class ChallanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, challan_id: uuid.UUID) -> PaymentChallan | None:
        return await self.session.get(PaymentChallan, challan_id)

    async def get_by_ledger_entry_id(self, ledger_entry_id: uuid.UUID) -> PaymentChallan | None:
        stmt = select(PaymentChallan).where(PaymentChallan.fee_ledger_entry_id == ledger_entry_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_assignment(self, assignment_id: uuid.UUID) -> list[PaymentChallan]:
        stmt = (
            select(PaymentChallan)
            .where(PaymentChallan.fee_assignment_id == assignment_id)
            .order_by(PaymentChallan.due_date)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, challan: PaymentChallan) -> PaymentChallan:
        self.session.add(challan)
        await self.session.flush()
        return challan