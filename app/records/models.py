"""Records: the register of certificates and ID cards (each checkable online by its code), and documents kept on file.

See migration d9f5a6b7c890 for the exact DDL. Neither table is ever deleted from: a certificate is cancelled
(revoked), a document is archived, and the history stays.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Integer, String, Text
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
