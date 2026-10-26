"""Opening balances: money a student already owed before the system started, carried in as a normal, payable charge."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError
from app.fees.challan_factory import create_challan
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, FeeStructureStatus, FeeType, LedgerEntryType, PayerType
from app.fees.scholarship_service import OPENING_CODE
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.students.models import Student, StudentStatus

STRUCTURE_NAME = "Previous dues (carried forward)"
MAX_AMOUNT = Decimal("10000000")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")


class OpeningBalanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def structure(self, actor_id: uuid.UUID) -> FeeStructure:
        """The one fee that holds everyone's previous dues. It applies to nobody automatically (so new admissions are never charged it)."""
        existing = (await self.session.execute(select(FeeStructure).where(FeeStructure.name == STRUCTURE_NAME))).scalars().first()
        if existing is not None:
            return existing
        fee_type = (await self.session.execute(select(FeeType).where(FeeType.code == OPENING_CODE))).scalars().first()
        if fee_type is None:
            fee_type = FeeType(code=OPENING_CODE, name="Previous dues", description="Money owed from before the system started", is_active=True)
            self.session.add(fee_type)
            await self.session.flush()
        now = datetime.now(timezone.utc)
        structure = FeeStructure(fee_type_id=fee_type.id, name=STRUCTURE_NAME, term_label="Carried forward", total_amount=1, installment_count=1, installment_interval_months=1,
                                 effective_from=institute_today(), late_fee_per_day=None, applies_to_students=False, applies_to_employees=False, course_id=None,
                                 status=FeeStructureStatus.ACTIVE, activated_at=now, created_by=actor_id)
        self.session.add(structure)
        await self.session.flush()
        return structure

    async def add(self, *, student_id: uuid.UUID, amount: Decimal, due_date: date, note: str | None, actor_id: uuid.UUID, notify: bool = True) -> dict:
        student = await self.session.get(Student, student_id)
        if student is None or student.status not in (StudentStatus.ACTIVE, StudentStatus.SUSPENDED):
            raise BusinessRuleViolationError("Choose a current student.")
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        if amount <= 0 or amount > MAX_AMOUNT:
            raise BusinessRuleViolationError("The amount must be more than 0 and at most Rs. 10,000,000.")
        structure = await self.structure(actor_id)
        if await self.already_recorded(student_id, amount, due_date):
            raise BusinessRuleViolationError(f"Previous dues of Rs. {amount:,.2f} due {due_date:%d %b %Y} are already recorded for this student.")
        assignment = (await self.session.execute(select(FeeAssignment).where(FeeAssignment.fee_structure_id == structure.id, FeeAssignment.payer_type == PayerType.STUDENT,
                                                                             FeeAssignment.payer_id == student_id))).scalars().first()
        if assignment is None:
            assignment = FeeAssignment(fee_structure_id=structure.id, payer_type=PayerType.STUDENT, payer_id=student_id, status=AssignmentStatus.ACTIVE)
            self.session.add(assignment)
            await self.session.flush()
        n = (await self.session.execute(select(func.count()).select_from(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == assignment.id, FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT))).scalar_one() + 1
        entry = FeeLedgerEntry(fee_assignment_id=assignment.id, entry_type=LedgerEntryType.DEBIT, amount=float(amount), installment_number=n, due_date=due_date,
                               description=(note or "Previous dues (opening balance)").strip()[:255], created_by=actor_id, created_at=datetime.now(timezone.utc))
        self.session.add(entry)
        await self.session.flush()
        challan = await create_challan(self.session, assignment=assignment, entry=entry, amount=float(amount), due_date=due_date)
        await self.audit.log(actor_user_id=actor_id, action="opening_balance.added", entity_type="fee_assignment", entity_id=assignment.id, after={"amount": float(amount), "due_date": due_date.isoformat()})
        if notify and student.user_id:
            await create_notification(self.session, user_id=student.user_id, category=NotificationCategory.FEE, title="Previous dues added to your account",
                                      message=f"Rs. {amount:,.2f}, due {due_date:%d %b %Y}.", link="/dashboard/student/fees")
        return {"assignment_id": assignment.id, "ledger_entry_id": entry.id, "challan_id": challan.id, "consumer_number": challan.consumer_number}

    async def already_recorded(self, student_id: uuid.UUID, amount: Decimal, due_date: date) -> bool:
        """The same previous-dues amount and due date already on this student's account (the same sheet imported twice)."""
        structure = (await self.session.execute(select(FeeStructure).where(FeeStructure.name == STRUCTURE_NAME))).scalars().first()
        if structure is None:
            return False
        n = (await self.session.execute(select(func.count()).select_from(FeeLedgerEntry).join(FeeAssignment, FeeAssignment.id == FeeLedgerEntry.fee_assignment_id).where(
            FeeAssignment.fee_structure_id == structure.id, FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.payer_id == student_id,
            FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT, FeeLedgerEntry.amount == amount, FeeLedgerEntry.due_date == due_date))).scalar_one()
        return n > 0

    async def listing(self, *, q: str | None = None, limit: int = 300) -> list[dict]:
        """Every previous-dues entry, newest first, with what is still owed on it."""
        from app.fees.assignment_service import FeeStatusCalculator

        structure = (await self.session.execute(select(FeeStructure).where(FeeStructure.name == STRUCTURE_NAME))).scalars().first()
        if structure is None:
            return []
        stmt = select(FeeAssignment, Student).join(Student, Student.id == FeeAssignment.payer_id).where(
            FeeAssignment.fee_structure_id == structure.id, FeeAssignment.payer_type == PayerType.STUDENT)
        if q and q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where((Student.roll_number.ilike(like)) | (Student.first_name.ilike(like)) | (Student.last_name.ilike(like)))
        out = []
        for a, st in (await self.session.execute(stmt.limit(limit))).all():
            entries = list((await self.session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == a.id))).scalars())
            rows = {r["ledger_entry_id"]: r for r in FeeStatusCalculator.installments(entries, None)}
            for e in entries:
                r = rows.get(e.id)
                if r is None:
                    continue
                out.append({"ledger_entry_id": e.id, "assignment_id": a.id, "student_id": st.id, "student_name": f"{st.first_name} {st.last_name}",
                            "roll_number": st.roll_number, "amount": r["charged"], "cancelled": r["concession"], "paid": r["paid"], "remaining": r["remaining"],
                            "due_date": e.due_date, "note": e.description, "status": r["status"], "created_at": e.created_at})
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return out

    async def cancel_entry(self, ledger_entry_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID) -> dict:
        """A previous-dues entry typed in by mistake: what is still owed on it is written off (the ledger is append-only,
        so the entry stays, with a matching write-off). Money already paid against it stays recorded."""
        from app.fees.assignment_service import FeeStatusCalculator, lock_assignment, sync_challans

        if len((reason or "").strip()) < 5:
            raise BusinessRuleViolationError("Say why this previous-dues entry is being cancelled.")
        entry = await self.session.get(FeeLedgerEntry, ledger_entry_id)
        structure = (await self.session.execute(select(FeeStructure).where(FeeStructure.name == STRUCTURE_NAME))).scalars().first()
        a = await self.session.get(FeeAssignment, entry.fee_assignment_id) if entry is not None else None
        if entry is None or entry.entry_type != LedgerEntryType.DEBIT or structure is None or a is None or a.fee_structure_id != structure.id:
            raise BusinessRuleViolationError("That is not a previous-dues entry.")
        a = await lock_assignment(self.session, a.id)
        entries = list((await self.session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == a.id))).scalars())
        row = next((r for r in FeeStatusCalculator.installments(entries, None) if r["ledger_entry_id"] == entry.id), None)
        if row is None or row["remaining"] <= 0.005:
            raise BusinessRuleViolationError("Nothing is still owed on this entry, so there is nothing to cancel.")
        self.session.add(FeeLedgerEntry(fee_assignment_id=a.id, entry_type=LedgerEntryType.CONCESSION, amount=row["remaining"], installment_number=entry.installment_number,
                                        related_entry_id=entry.id, description=f"Previous dues cancelled: {reason.strip()}"[:255], created_by=actor_id,
                                        created_at=datetime.now(timezone.utc)))
        await sync_challans(self.session, a)
        await self.audit.log(actor_user_id=actor_id, action="opening_balance.cancelled", entity_type="fee_ledger_entry", entity_id=entry.id,
                             after={"written_off": row["remaining"]}, metadata={"reason": reason.strip()})
        return {"ledger_entry_id": entry.id, "written_off": row["remaining"]}

    # ------------------------------------------------------------------ a whole sheet of them
    @staticmethod
    def _parse_date(text: str) -> date | None:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text.strip(), fmt).date()
            except ValueError:
                continue
        return None

    async def check_csv(self, content: bytes) -> list[dict]:
        """Read and check every row without saving anything. Columns: roll_number, amount, due_date, note (optional)."""
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise BusinessRuleViolationError("The file is not a text (CSV) file. Save it as CSV (UTF-8) and try again.")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise BusinessRuleViolationError("The file is empty.")
        names = {(h or "").strip().lower().replace(" ", "_"): h for h in reader.fieldnames}
        pick = lambda *keys: next((names[k] for k in keys if k in names), None)                       # noqa: E731
        c_roll, c_amt, c_due, c_note = pick("roll_number", "roll_no", "roll", "student"), pick("amount", "balance", "dues"), pick("due_date", "due", "last_date"), pick("note", "description", "remarks")
        missing = [label for label, col in (("roll_number", c_roll), ("amount", c_amt), ("due_date", c_due)) if col is None]
        if missing:
            raise BusinessRuleViolationError("The file needs these columns: roll_number, amount, due_date (and optionally note). Missing: " + ", ".join(missing) + ".")
        rows, seen = [], set()
        for i, raw in enumerate(reader, start=2):
            roll, amt, due = (raw.get(c_roll) or "").strip(), (raw.get(c_amt) or "").strip().replace(",", ""), (raw.get(c_due) or "").strip()
            note = (raw.get(c_note) or "").strip() if c_note else ""
            if not (roll or amt or due):
                continue
            errors, student, amount, due_date = [], None, None, None
            if not roll:
                errors.append("roll number is empty")
            else:
                student = (await self.session.execute(select(Student).where(func.lower(Student.roll_number) == roll.lower()))).scalars().first()
                if student is None:
                    errors.append(f"no student with roll number {roll}")
                elif student.status not in (StudentStatus.ACTIVE, StudentStatus.SUSPENDED):
                    errors.append("this student is not a current student")
            try:
                amount = Decimal(amt).quantize(Decimal("0.01"))
                if amount <= 0 or amount > MAX_AMOUNT:
                    errors.append("amount must be more than 0 and at most 10,000,000")
            except (InvalidOperation, ValueError):
                errors.append(f"\u201c{amt}\u201d is not an amount")
            due_date = self._parse_date(due) if due else None
            if due_date is None:
                errors.append(f"\u201c{due}\u201d is not a date (use 2026-12-31 or 31/12/2026)")
            key = (roll.lower(), str(amount), str(due_date), note.lower())
            if not errors and key in seen:
                errors.append("this exact row appears twice in the file")
            if not errors and await self.already_recorded(student.id, amount, due_date):
                errors.append("these previous dues (same amount and due date) are already recorded for this student")
            seen.add(key)
            rows.append({"line": i, "roll_number": roll, "student_id": student.id if student else None, "student_name": f"{student.first_name} {student.last_name}" if student else None,
                         "amount": float(amount) if amount is not None and not errors else (float(amount) if amount is not None else None), "due_date": due_date, "note": note, "errors": errors, "ok": not errors})
        if not rows:
            raise BusinessRuleViolationError("The file has no rows.")
        if len(rows) > 5000:
            raise BusinessRuleViolationError("A file can have at most 5,000 rows. Split it into parts.")
        return rows

    async def import_csv(self, content: bytes, *, actor_id: uuid.UUID, commit: bool) -> dict:
        rows = await self.check_csv(content)
        bad = [r for r in rows if not r["ok"]]
        result = {"rows": rows, "total_rows": len(rows), "bad_rows": len(bad), "total_amount": round(sum(r["amount"] or 0 for r in rows if r["ok"]), 2), "imported": 0, "committed": False}
        if commit:
            if bad:
                raise BusinessRuleViolationError(f"{len(bad)} row{'s' if len(bad) != 1 else ''} have problems. Nothing was imported. Fix them and try again.")
            for r in rows:
                await self.add(student_id=r["student_id"], amount=Decimal(str(r["amount"])), due_date=r["due_date"], note=r["note"] or None, actor_id=actor_id, notify=True)
            result.update(imported=len(rows), committed=True)
        return result