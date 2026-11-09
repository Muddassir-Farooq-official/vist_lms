"""Records: the register of certificates and ID cards (each checkable online by its code), and documents kept on file.

See migration d9f5a6b7c890 for the exact DDL. Neither table is ever deleted from: a certificate is cancelled
(revoked), a document is archived, and the history stays.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Identity, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class IssuedDocument(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "issued_documents"
    __table_args__ = (Index("ix_issued_documents_owner", "owner_type", "owner_id"),)

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)               # BONAFIDE, CHARACTER, COMPLETION, EXPERIENCE, CARD
    owner_type: Mapped[str] = mapped_column(String(10), nullable=False)         # STUDENT | EMPLOYEE
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    owner_name: Mapped[str] = mapped_column(String(200), nullable=False)        # who they were when it was issued, as printed
    verification_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PersonDocument(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "person_documents"
    __table_args__ = (Index("ix_person_documents_owner", "owner_type", "owner_id"),)

    owner_type: Mapped[str] = mapped_column(String(10), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class CertificateRequest(UUIDPKMixin, TimestampMixin, Base):
    """A student asks for a certificate from their portal (app/records/requests.py, migration e6a8c0d2f4b6).

    status: REQUESTED (waiting for the office) -> AWAITING_FEE (approved, challan to pay) -> READY (issued: the student downloads it);
    or REJECTED / CANCELLED. Never deleted. The fee is an ordinary charge on the student's fee account, so paying it works like any
    challan (bank, 1Bill, counter) and it shows in every fee report. content keeps the exact wording issued, so a later download is
    always the same paper.
    """

    __tablename__ = "certificate_requests"
    __table_args__ = (Index("ix_certificate_requests_student", "student_id"), Index("ix_certificate_requests_status", "status"))

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="REQUESTED")
    fee_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    fee_ledger_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fee_ledger_entries.id", ondelete="SET NULL"), nullable=True)
    challan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payment_challans.id", ondelete="SET NULL"), nullable=True)
    issued_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issued_documents.id", ondelete="SET NULL"), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MonthlyReportRun(UUIDPKMixin, TimestampMixin, Base):
    """One row per month whose report was sent out on the 1st (app/reports/monthly.py): stops it being sent twice.
    The report itself is never stored: it is worked out from the records each time it is opened."""

    __tablename__ = "monthly_report_runs"

    month: Mapped[date] = mapped_column(Date, unique=True, nullable=False)          # first day of the month reported on
    sent_to: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
