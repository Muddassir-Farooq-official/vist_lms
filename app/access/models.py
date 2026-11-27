"""Access control tables (Settings -> Access control): rights taken from a built-in role, one person's own rights,
and the history of every access change (for undo)."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class RolePermissionRemoval(Base):
    """A right an administrator took away from a built-in role: the deploy's permission seed does not put it back."""
    __tablename__ = "role_permission_removals"

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)
    removed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    removed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserAccessOverride(UUIDPKMixin, TimestampMixin, Base):
    """One person's own right on top of their roles (GRANT) or taken away from what their roles give (DENY),
    optionally until a date (inclusive). DENY wins over every role."""
    __tablename__ = "user_access_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "permission_code", name="uq_user_access_overrides_user_permission"),
        CheckConstraint("effect IN ('GRANT', 'DENY')", name="ck_user_access_overrides_effect"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    permission_code: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str] = mapped_column(String(8), nullable=False)
    until: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reminded_on: Mapped[date | None] = mapped_column(Date, nullable=True)   # "ends in 2 days" was sent (once)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AccessChange(UUIDPKMixin, TimestampMixin, Base):
    """One access change, with what it was before and after, so it can be shown and undone."""
    __tablename__ = "access_changes"
    __table_args__ = (
        Index("ix_access_changes_created_at", "created_at"),
        Index("ix_access_changes_target", "target_type", "target_id"),
    )

    kind: Mapped[str] = mapped_column(String(40), nullable=False)         # ROLE_RIGHTS, ROLE_CREATED, ROLE_DELETED, PERSON_RIGHTS, PERSON_ROLES
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)  # ROLE / USER
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    target_label: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    undo_of: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    undone_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRule(UUIDPKMixin, TimestampMixin, Base):
    """Who approves one kind of action (app/approvals/rules.py). requester_role None = anyone; min_amount None = any
    amount. mode NONE = no approval needed; APPROVE = approver_type decides: DEFAULT (as the system does without rules),
    ROLE (anyone holding approver_value), USER (that person), HOD (head of the department concerned) or COURSE_TEACHER."""
    __tablename__ = "approval_rules"
    __table_args__ = (
        CheckConstraint("mode IN ('NONE', 'APPROVE')", name="ck_approval_rules_mode"),
        Index("ix_approval_rules_action", "action"),
    )

    action: Mapped[str] = mapped_column(String(40), nullable=False)
    requester_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    approver_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approver_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
