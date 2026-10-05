"""One person's whole fee picture: every fee installment by installment, scholarships, payments, challans -- and the fines they also owe."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError
from app.employees.models import Employee
from app.fees.assignment_service import FeeStatusCalculator
from app.fees.models import AssignmentStatus, FeeStructure, FeeType, LedgerEntryType, PayerType
from app.fees.payment_models import PaymentChallan
from app.fees.repository import FeeAssignmentRepository
from app.fees.scholarship_service import OPENING_CODE, ScholarshipService
from app.fines.models import Fine, FinePayerType, FineStatus
from app.programs.models import Program
from app.students.models import Student


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FeeAssignmentRepository(session)

    async def payer(self, payer_type: PayerType, payer_id: uuid.UUID) -> dict:
        if payer_type == PayerType.STUDENT:
            st = await self.session.get(Student, payer_id)
            if st is None:
                raise ResourceNotFoundError("Student not found.")
            program = await self.session.get(Program, st.program_id) if st.program_id else None
            return {"type": "STUDENT", "id": st.id, "name": f"{st.first_name} {st.last_name}", "identifier": st.roll_number,
                    "group": ((program.code if program else "") + (f" \u00b7 Semester {st.current_semester}" if st.current_semester else "")).strip(" \u00b7") or None}
        em = await self.session.get(Employee, payer_id)
        if em is None:
            raise ResourceNotFoundError("Employee not found.")
        return {"type": "EMPLOYEE", "id": em.id, "name": f"{em.first_name} {em.last_name}", "identifier": em.employee_code, "group": None}

    async def build(self, payer_type: PayerType, payer_id: uuid.UUID, *, staff_view: bool) -> dict:
        payer = await self.payer(payer_type, payer_id)
        assignments, out, payments = await self.repo.list_for_payer(payer_type, payer_id), [], []
        totals = dict(charged=0.0, concession=0.0, paid=0.0, remaining=0.0, overdue=0.0, late_fee_accrued=0.0)
        upcoming: list[tuple[date, float]] = []
        for a in assignments:
            structure = await self.session.get(FeeStructure, a.fee_structure_id)
            fee_type = await self.session.get(FeeType, structure.fee_type_id)
            late = float(structure.late_fee_per_day) if structure.late_fee_per_day else None
            entries = await self.repo.get_ledger(a.id)
            rows = FeeStatusCalculator.installments(entries, late)
            status = FeeStatusCalculator.compute(entries, late)
            challans = {c.fee_ledger_entry_id: c for c in (await self.session.execute(select(PaymentChallan).where(PaymentChallan.fee_assignment_id == a.id))).scalars()}
            for r in rows:
                c = challans.get(r["ledger_entry_id"])
                r["challan"] = {"id": c.id, "consumer_number": c.consumer_number, "status": c.status.value, "amount": float(c.amount), "due_date": c.due_date} if c else None
            for e in entries:
                if e.entry_type == LedgerEntryType.CREDIT:
                    payments.append({"date": e.created_at, "amount": float(e.amount), "description": e.description or "Payment", "fee": structure.name})
            conc = [{"name": e.description, "amount": float(e.amount), "installment_number": e.installment_number} for e in entries if e.entry_type == LedgerEntryType.CONCESSION]
            active = a.status == AssignmentStatus.ACTIVE
            charged, concession = sum(r["charged"] for r in rows), sum(r["concession"] for r in rows)
            paid, remaining = sum(r["paid"] for r in rows), sum(r["remaining"] for r in rows)
            if active:
                totals["charged"] += charged; totals["concession"] += concession; totals["paid"] += paid; totals["remaining"] += remaining
                totals["overdue"] += status["overdue_principal"]; totals["late_fee_accrued"] += status["accrued_fine"]
                upcoming += [(r["due_date"], r["remaining"]) for r in rows if r["remaining"] > 0.005 and r["due_date"] and r["status"] in ("UPCOMING", "PARTIAL")]
            out.append({"id": a.id, "fee_name": structure.name, "term_label": structure.term_label, "is_previous_dues": fee_type.code == OPENING_CODE, "status": a.status.value,
                        "late_fee_per_day": late, "charged": round(charged, 2), "concession": round(concession, 2), "payable": round(charged - concession, 2), "paid": round(paid, 2),
                        "remaining": round(remaining, 2), "overdue": round(status["overdue_principal"], 2), "late_fee_accrued": round(status["accrued_fine"], 2),
                        "installments": rows, "concessions": conc})
        fines_unpaid, fines_count = 0.0, 0
        if payer_type == PayerType.STUDENT:
            fines_unpaid, fines_count = (await self.session.execute(select(func.coalesce(func.sum(Fine.amount), 0), func.count()).where(
                Fine.payer_type == FinePayerType.STUDENT, Fine.payer_id == payer_id, Fine.status == FineStatus.PENDING))).one()
            fines_unpaid = float(fines_unpaid)
        scholarships = await ScholarshipService(self.session).listing(student_id=payer_id, status=None) if payer_type == PayerType.STUDENT else []
        if not staff_view:
            for s in scholarships:
                s.pop("reason", None)
        nxt = min(upcoming, key=lambda x: x[0]) if upcoming else None
        payments.sort(key=lambda p: p["date"], reverse=True)
        return {"payer": payer, "assignments": sorted(out, key=lambda x: (x["is_previous_dues"] is False, x["term_label"] or ""), reverse=False),
                "summary": {"charged": round(totals["charged"], 2), "concession": round(totals["concession"], 2), "paid": round(totals["paid"], 2), "remaining": round(totals["remaining"], 2),
                            "overdue": round(totals["overdue"], 2), "late_fee_accrued": round(totals["late_fee_accrued"], 2), "next_due": {"date": nxt[0], "amount": round(nxt[1], 2)} if nxt else None,
                            "fines_unpaid": round(fines_unpaid, 2), "fines_count": int(fines_count), "total_to_pay": round(totals["remaining"] + fines_unpaid, 2),
                            "credit_balance": round(max(0.0, totals["paid"] - (totals["charged"] - totals["concession"])), 2)},
                "payments": payments, "scholarships": scholarships}