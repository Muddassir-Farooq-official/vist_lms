from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClassSubstitute(Base):
    """One class session whose teacher is on approved leave: PROPOSED (a free teacher suggested), CONFIRMED (the office
    agreed; that teacher takes the class and can mark its attendance) or CANCELLED (no class; the session is cancelled)."""
    __tablename__ = "class_substitutes"
    __table_args__ = (Index("ix_class_substitutes_substitute", "substitute_employee_id"), Index("ix_class_substitutes_status", "status"))

    class_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), primary_key=True)
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leave_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    absent_employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    substitute_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PROPOSED")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
