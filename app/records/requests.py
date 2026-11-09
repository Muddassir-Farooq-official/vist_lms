"""Certificates on request: a student asks from their portal, the office approves, the student pays the fee (if any) and downloads it.

    REQUESTED ──approve──> AWAITING_FEE ──challan paid──> READY (the student downloads the PDF; it is checkable online by its code)
        │          └─ no fee ───────────────────────────────┘
        ├──reject──> REJECTED          AWAITING_FEE ──office cancels──> CANCELLED (what was still owed is written off)
        └──student cancels──> CANCELLED

The checks a certificate needs (all dues settled, degree complete ...) are made when the office approves; the office can approve
in spite of them with a written reason, as on the direct-issue screen. The fee is set per kind of certificate in Settings and can
be changed or waived per request. It is a normal charge on the student's fee account (one "Certificate fees" fee per student, one
line per request), so the challan is paid like any other and appears in every fee report; nothing is ever deleted from the ledger.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.records.models import CertificateRequest, IssuedDocument
from app.records.service import CERTIFICATES, STUDENT_CERTIFICATES, RecordsService, reference_of, verify_url

STRUCTURE_NAME = "Certificate fees"
FEE_TYPE_CODE = "CERT"
PAY_WITHIN_DAYS = 14
OPEN = ("REQUESTED", "AWAITING_FEE")
STATUS_TEXT = {"REQUESTED": "Waiting for the office", "AWAITING_FEE": "Approved: pay the fee", "READY": "Ready to download",
               "REJECTED": "Not approved", "CANCELLED": "Cancelled"}
STUDENT_LINK = "/dashboard/student/certificates"
OFFICE_LINK = "/dashboard/admin/certificates"


def fee_for(kind: str) -> float:
    return float(getattr(settings, f"certificate_fee_{kind.lower()}", 0) or 0)


def kinds() -> list[dict]:
    return [{"kind": k, "label": CERTIFICATES[k], "fee": fee_for(k)} for k in STUDENT_CERTIFICATES]


class CertificateRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)
        self.records = RecordsService(session)

    # ------------------------------------------------------------------ helpers
    async def _get(self, request_id: uuid.UUID, *, lock: bool = False) -> CertificateRequest:
        stmt = select(CertificateRequest).where(CertificateRequest.id == request_id)
        r = (await self.session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
        if r is None:
            raise ResourceNotFoundError("Request not found.")
        return r

    async def _notify_student(self, r: CertificateRequest, title: str, message: str) -> None:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.students.models import Student

        s = await self.session.get(Student, r.student_id)
        if s is not None and s.user_id:
            await create_notification(self.session, user_id=s.user_id, category=NotificationCategory.APPLICATION, title=title, message=message, link=STUDENT_LINK)

    async def _notify_office(self, r: CertificateRequest, name: str) -> None:
        from app.approvals.service import _users_with_permission
        from app.auth.permissions import Permissions
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        for uid in await _users_with_permission(self.session, Permissions.RECORDS_ISSUE):
            await create_notification(self.session, user_id=uid, category=NotificationCategory.APPLICATION, title="Certificate requested",
                                      message=f"{name} asked for a {CERTIFICATES[r.kind].lower()}: {r.purpose}", link=OFFICE_LINK)

    async def _fee_structure(self, actor_id):
        from app.fees.models import FeeStructure, FeeStructureStatus, FeeType

        existing = (await self.session.execute(select(FeeStructure).where(FeeStructure.name == STRUCTURE_NAME))).scalars().first()
        if existing is not None:
            return existing
        fee_type = (await self.session.execute(select(FeeType).where(FeeType.code == FEE_TYPE_CODE))).scalars().first()
        if fee_type is None:
            fee_type = FeeType(code=FEE_TYPE_CODE, name="Certificate fee", description="Fees for certificates students ask for", is_active=True)
            self.session.add(fee_type)
            await self.session.flush()
        structure = FeeStructure(fee_type_id=fee_type.id, name=STRUCTURE_NAME, term_label="Certificates", total_amount=1, installment_count=1,
                                 installment_interval_months=1, effective_from=institute_today(), late_fee_per_day=None, applies_to_students=False,
                                 applies_to_employees=False, course_id=None, status=FeeStructureStatus.ACTIVE, activated_at=datetime.now(timezone.utc),
                                 created_by=actor_id)
        self.session.add(structure)
        await self.session.flush()
        return structure

    async def _charge(self, r: CertificateRequest, amount: float, actor_id) -> None:
        """One line on the student's 'Certificate fees' account and its challan (the same way previous dues are charged)."""
        from app.fees.challan_factory import create_challan
        from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, LedgerEntryType, PayerType

        structure = await self._fee_structure(actor_id)
        a = (await self.session.execute(select(FeeAssignment).where(FeeAssignment.fee_structure_id == structure.id, FeeAssignment.payer_type == PayerType.STUDENT,
                                                                    FeeAssignment.payer_id == r.student_id, FeeAssignment.enrollment_id.is_(None)))).scalars().first()
        if a is None:
            a = FeeAssignment(fee_structure_id=structure.id, payer_type=PayerType.STUDENT, payer_id=r.student_id, status=AssignmentStatus.ACTIVE)
            self.session.add(a)
            await self.session.flush()
        n = (await self.session.execute(select(func.count()).select_from(FeeLedgerEntry).where(
            FeeLedgerEntry.fee_assignment_id == a.id, FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT))).scalar_one() + 1
        due = institute_today() + timedelta(days=PAY_WITHIN_DAYS)
        entry = FeeLedgerEntry(fee_assignment_id=a.id, entry_type=LedgerEntryType.DEBIT, amount=amount, installment_number=n, due_date=due,
                               description=f"{CERTIFICATES[r.kind]} fee"[:255], created_by=actor_id, created_at=datetime.now(timezone.utc))
        self.session.add(entry)
        await self.session.flush()
        challan = await create_challan(self.session, assignment=a, entry=entry, amount=amount, due_date=due)
        r.fee_ledger_entry_id, r.challan_id = entry.id, challan.id

    # ------------------------------------------------------------------ the student
    async def ask(self, student_id: uuid.UUID, *, kind: str, purpose: str, user_id) -> CertificateRequest:
        from app.students.models import Student

        kind = (kind or "").upper()
        if kind not in STUDENT_CERTIFICATES:
            raise ValidationAppError("Choose a certificate from the list.")
        purpose = " ".join((purpose or "").split())
        if len(purpose) < 4:
            raise ValidationAppError("Say what you need it for (for example: bank account, scholarship, visa).")
        open_same = (await self.session.execute(select(func.count()).select_from(CertificateRequest).where(
            CertificateRequest.student_id == student_id, CertificateRequest.kind == kind, CertificateRequest.status.in_(OPEN)))).scalar_one()
        if open_same:
            raise BusinessRuleViolationError(f"You have already asked for a {CERTIFICATES[kind].lower()}. It is shown below.")
        r = CertificateRequest(student_id=student_id, kind=kind, purpose=purpose[:300], status="REQUESTED", fee_amount=fee_for(kind), requested_by=user_id)
        self.session.add(r)
        await self.session.flush()
        s = await self.session.get(Student, student_id)
        await self._notify_office(r, f"{s.first_name} {s.last_name} ({s.roll_number})")
        await self.audit.log(actor_user_id=user_id, action="certificate_request.created", entity_type="certificate_request", entity_id=r.id,
                             metadata={"kind": kind})
        return r

    async def cancel_mine(self, request_id: uuid.UUID, student_id: uuid.UUID, *, user_id) -> CertificateRequest:
        r = await self._get(request_id, lock=True)
        if r.student_id != student_id:
            raise ResourceNotFoundError("Request not found.")
        if r.status != "REQUESTED":
            raise BusinessRuleViolationError("Only a request the office has not answered yet can be cancelled here. Please contact the office.")
        r.status, r.decided_at, r.decision_note = "CANCELLED", datetime.now(timezone.utc), "Cancelled by the student"
        await self.audit.log(actor_user_id=user_id, action="certificate_request.cancelled", entity_type="certificate_request", entity_id=r.id)
        return r

    # ------------------------------------------------------------------ the office
    async def approve(self, request_id: uuid.UUID, *, actor_id, fee: Decimal | float | None = None, override_reason: str | None = None,
                      note: str | None = None) -> CertificateRequest:
        r = await self._get(request_id, lock=True)
        if r.status != "REQUESTED":
            raise BusinessRuleViolationError("This request has already been answered.")
        amount = float(r.fee_amount if fee is None else fee)
        if amount < 0 or amount > 1_000_000:
            raise ValidationAppError("The fee must be between Rs. 0 and Rs. 1,000,000.")
        problems = await self.records.certificate_problems(r.student_id, r.kind)
        override = (override_reason or "").strip() or None
        if problems and not override:
            raise BusinessRuleViolationError("Not cleared: " + " ".join(problems) + " To approve it anyway, give a reason.")
        if problems and len(override) < 10:
            raise BusinessRuleViolationError("Please give a reason of at least 10 characters.")
        r.fee_amount, r.override_reason = round(amount, 2), (override if problems else None)
        r.decided_by, r.decided_at, r.decision_note = actor_id, datetime.now(timezone.utc), (note or "").strip()[:300] or None
        await self.audit.log(actor_user_id=actor_id, action="certificate_request.approved", entity_type="certificate_request", entity_id=r.id,
                             after={"fee": amount}, metadata={"kind": r.kind, "override_reason": r.override_reason})
        if amount > 0:
            await self._charge(r, round(amount, 2), actor_id)
            r.status = "AWAITING_FEE"
            await self._notify_student(r, "Certificate approved: pay the fee",
                                       f"Your {CERTIFICATES[r.kind].lower()} is approved. Pay the fee of Rs. {amount:,.0f} with the challan "
                                       "on the Certificates page; you can download the certificate as soon as it is paid.")
        else:
            await self._issue(r, actor_id)
        return r

    async def reject(self, request_id: uuid.UUID, *, actor_id, note: str) -> CertificateRequest:
        note = (note or "").strip()
        if len(note) < 4:
            raise ValidationAppError("Say why, so the student knows what to do.")
        r = await self._get(request_id, lock=True)
        if r.status != "REQUESTED":
            raise BusinessRuleViolationError("Only a request waiting for the office can be refused. An approved one can be cancelled.")
        r.status, r.decided_by, r.decided_at, r.decision_note = "REJECTED", actor_id, datetime.now(timezone.utc), note[:300]
        await self.audit.log(actor_user_id=actor_id, action="certificate_request.rejected", entity_type="certificate_request", entity_id=r.id, metadata={"note": note})
        await self._notify_student(r, "Certificate request not approved", f"Your request for a {CERTIFICATES[r.kind].lower()} was not approved: {note}")
        return r

    async def cancel_unpaid(self, request_id: uuid.UUID, *, actor_id, note: str) -> CertificateRequest:
        """An approved request whose fee is not paid: what is still owed is written off (the charge itself stays in the ledger)."""
        from app.fees.assignment_service import FeeStatusCalculator, lock_assignment, sync_challans
        from app.fees.models import FeeLedgerEntry, LedgerEntryType

        note = (note or "").strip()
        if len(note) < 4:
            raise ValidationAppError("Say why it is being cancelled.")
        r = await self._get(request_id, lock=True)
        if r.status != "AWAITING_FEE":
            raise BusinessRuleViolationError("Only an approved request whose fee is not yet paid can be cancelled.")
        await self.sweep(only=r)
        if r.status != "AWAITING_FEE":
            raise BusinessRuleViolationError("The fee has just been paid, so the certificate was issued. Cancel the certificate itself instead if needed.")
        entry = await self.session.get(FeeLedgerEntry, r.fee_ledger_entry_id) if r.fee_ledger_entry_id else None
        if entry is not None:
            a = await lock_assignment(self.session, entry.fee_assignment_id)
            entries = list((await self.session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == a.id))).scalars())
            row = next((x for x in FeeStatusCalculator.installments(entries, None) if x["ledger_entry_id"] == entry.id), None)
            if row is not None and row["remaining"] > 0.005:
                self.session.add(FeeLedgerEntry(fee_assignment_id=a.id, entry_type=LedgerEntryType.CONCESSION, amount=row["remaining"],
                                                installment_number=entry.installment_number, related_entry_id=entry.id,
                                                description=f"Certificate request cancelled: {note}"[:255], created_by=actor_id,
                                                created_at=datetime.now(timezone.utc)))
                await self.session.flush()
                await sync_challans(self.session, a)
        r.status, r.decision_note = "CANCELLED", note[:300]
        await self.audit.log(actor_user_id=actor_id, action="certificate_request.cancelled", entity_type="certificate_request", entity_id=r.id, metadata={"note": note})
        await self._notify_student(r, "Certificate request cancelled", f"Your {CERTIFICATES[r.kind].lower()} request was cancelled by the office: {note}")
        return r

    # ------------------------------------------------------------------ issuing
    async def _issue(self, r: CertificateRequest, actor_id) -> None:
        result = await self.records.issue_student_certificate(r.student_id, r.kind, actor_id=actor_id, override_reason=r.override_reason, checked=True,
                                                              extra={"request_id": str(r.id), "purpose": r.purpose})
        r.issued_document_id = result["doc"].id
        r.content = {"paragraphs": result["paragraphs"], "issued_on": result["issued_on"].isoformat(), "institute": result["institute"]}
        r.status, r.ready_at = "READY", datetime.now(timezone.utc)
        await self._notify_student(r, "Your certificate is ready", f"Your {CERTIFICATES[r.kind].lower()} is ready. Download it from the Certificates page.")

    async def sweep(self, *, student_id: uuid.UUID | None = None, only: CertificateRequest | None = None) -> int:
        """Issue every approved certificate whose challan is now paid. Run on each look at the list, and by the background job."""
        from app.fees.payment_models import ChallanStatus, PaymentChallan

        stmt = (select(CertificateRequest).join(PaymentChallan, PaymentChallan.id == CertificateRequest.challan_id)
                .where(CertificateRequest.status == "AWAITING_FEE", PaymentChallan.status == ChallanStatus.PAID))
        if student_id is not None:
            stmt = stmt.where(CertificateRequest.student_id == student_id)
        if only is not None:
            stmt = stmt.where(CertificateRequest.id == only.id)
        n = 0
        for r in (await self.session.execute(stmt.with_for_update(of=CertificateRequest, skip_locked=only is None))).scalars().all():
            await self._issue(r, r.decided_by)
            n += 1
        if n:
            await self.session.flush()
        return n

    # ------------------------------------------------------------------ lists
    async def _out(self, rows: list[CertificateRequest], *, with_student: bool) -> list[dict]:
        from app.fees.payment_models import PaymentChallan
        from app.students.models import Student

        challans = {}
        ids = {r.challan_id for r in rows if r.challan_id}
        if ids:
            challans = {c.id: c for c in (await self.session.execute(select(PaymentChallan).where(PaymentChallan.id.in_(ids)))).scalars()}
        docs = {}
        dids = {r.issued_document_id for r in rows if r.issued_document_id}
        if dids:
            docs = {d.id: d for d in (await self.session.execute(select(IssuedDocument).where(IssuedDocument.id.in_(dids)))).scalars()}
        students = {}
        if with_student and rows:
            students = {s.id: s for s in (await self.session.execute(select(Student).where(Student.id.in_({r.student_id for r in rows})))).scalars()}
        out = []
        for r in rows:
            c, d = challans.get(r.challan_id), docs.get(r.issued_document_id)
            item = {"id": r.id, "kind": r.kind, "kind_label": CERTIFICATES.get(r.kind, r.kind), "purpose": r.purpose, "status": r.status,
                    "status_text": STATUS_TEXT.get(r.status, r.status), "fee": float(r.fee_amount or 0), "asked_on": r.created_at,
                    "decided_on": r.decided_at, "note": r.decision_note, "ready_on": r.ready_at,
                    "challan": {"id": c.id, "consumer_number": c.consumer_number, "due_date": c.due_date, "status": c.status.value, "amount": float(c.amount)} if c else None,
                    "reference": reference_of(d) if d else None, "revoked": bool(d and d.revoked_at), "verify_url": verify_url(d.verification_code) if d else None}
            if with_student:
                s = students.get(r.student_id)
                item.update({"student_id": r.student_id, "student": f"{s.first_name} {s.last_name}" if s else "", "roll_number": s.roll_number if s else "",
                             "override_reason": r.override_reason})
            out.append(item)
        return out

    async def mine(self, student_id: uuid.UUID) -> list[dict]:
        await self.sweep(student_id=student_id)
        rows = (await self.session.execute(select(CertificateRequest).where(CertificateRequest.student_id == student_id)
                                           .order_by(CertificateRequest.created_at.desc()))).scalars().all()
        return await self._out(list(rows), with_student=False)

    async def office(self, *, status: str | None = None, limit: int = 300) -> dict:
        await self.sweep()
        stmt = select(CertificateRequest)
        if status == "OPEN":
            stmt = stmt.where(CertificateRequest.status.in_(OPEN))
        elif status:
            stmt = stmt.where(CertificateRequest.status == status.upper())
        rows = (await self.session.execute(stmt.order_by(CertificateRequest.created_at.desc()).limit(limit))).scalars().all()
        counts = dict((await self.session.execute(select(CertificateRequest.status, func.count()).group_by(CertificateRequest.status))).all())
        out = await self._out(list(rows), with_student=True)
        for item in out:
            if item["status"] == "REQUESTED":
                item["problems"] = await self.records.certificate_problems(item["student_id"], item["kind"])
        return {"requests": out, "counts": {k: counts.get(k, 0) for k in STATUS_TEXT}, "kinds": kinds()}

    async def pdf_parts(self, request_id: uuid.UUID, *, student_id: uuid.UUID | None = None) -> dict:
        """What the certificate PDF needs, exactly as it was issued. student_id given: it must be theirs."""
        r = await self._get(request_id)
        if student_id is not None and r.student_id != student_id:
            raise ResourceNotFoundError("Request not found.")
        if r.status == "AWAITING_FEE":
            await self.sweep(only=r)
        if r.status != "READY" or not r.issued_document_id or not r.content:
            raise BusinessRuleViolationError("This certificate is not ready yet." + (" Pay the fee first." if r.status == "AWAITING_FEE" else ""))
        d = await self.session.get(IssuedDocument, r.issued_document_id)
        if d.revoked_at is not None:
            raise BusinessRuleViolationError("This certificate was cancelled by the office" + (f": {d.revoke_reason}" if d.revoke_reason else "") + ".")
        from datetime import date as _date

        return {"doc": d, "paragraphs": r.content["paragraphs"], "institute": r.content.get("institute") or settings.institute_name,
                "issued_on": _date.fromisoformat(r.content["issued_on"])}


async def sweep_paid(session: AsyncSession) -> int:
    """Background: issue certificates whose fee was paid (the student is told at once, without opening the page)."""
    return await CertificateRequestService(session).sweep()
