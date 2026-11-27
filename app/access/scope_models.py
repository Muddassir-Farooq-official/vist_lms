"""A person's department and program limits (app/access/scope.py)."""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class AccessScope(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "access_scopes"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "value", name="uq_access_scopes_user_kind_value"),
        CheckConstraint("kind IN ('DEPARTMENT', 'PROGRAM')", name="ck_access_scopes_kind"),
        Index("ix_access_scopes_user_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    value: Mapped[str] = mapped_column(String(120), nullable=False)    # department name, or program id
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
