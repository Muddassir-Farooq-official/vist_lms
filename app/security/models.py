"""Security events: attempts that were refused or looked like an attack, kept so the administration can see and trace them.

Nothing here blocks a request. The protection itself is elsewhere (parameterised SQL, escaped pages, the content security
policy, permission checks, the sign-in limiter); this table is the record of who tried what, from where, and when.
Failed sign-ins are not copied here: they already live in login_attempts and the Security screen reads them from there.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SecurityEvent(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_created", "created_at"),
        Index("ix_security_events_kind_created", "kind", "created_at"),
        Index("ix_security_events_ip_created", "ip_address", "created_at"),
        Index("ix_security_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # ACCESS_DENIED | SUSPICIOUS_INPUT | BAD_TOKEN | SIGNIN_LIMIT | PROBE
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="WARN")          # INFO | WARN | HIGH
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)