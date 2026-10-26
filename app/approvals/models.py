"""Second-person approval for sensitive actions (owner decision 10, audit SEC-07).

When someone asks for one of these actions and another person holds `approvals.decide`, the action is not done; a
request is stored instead and an administrator other than the requester approves or rejects it. Approving runs the
action exactly as the requester asked. When nobody else could approve (one administrator only), the action goes ahead
and the audit log records that it was decided by a sole approver, so work never gets stuck.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class ApprovalRequest(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approval_requests_status", "status"), Index("ix_approval_requests_target", "kind", "target_id"))

    kind: Mapped[str] = mapped_column(String(30), nullable=False)              # FEE_STATUS FINE_WAIVE BOOKS_REOPEN PREVIOUS_DUES_CANCEL SCHOLARSHIP
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)          # "Waive Tuition - Fall 2026 for Ali Raza (24015)"
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # what to do, exactly as asked
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="REQUESTED")   # REQUESTED APPROVED REJECTED CANCELLED
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
