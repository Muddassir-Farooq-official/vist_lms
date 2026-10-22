from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.fines.models import FinePayerType, FineSource, FineStatus


class FineCreateRequest(BaseModel):
    """Direct issue — ADMIN only, via FineService.issue(). Already
    approved the moment it's created."""

    payer_type: FinePayerType
    payer_id: uuid.UUID
    amount: float = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=500)
    source: FineSource = FineSource.OTHER


class FineRequestCreate(BaseModel):
    """LIBRARIAN/ACCOUNTS_OFFICER entry point — via FineService.request().
    Deliberately has NO source field: the server derives it from the
    requester's own role (see default_source_for_roles), so a request
    can never claim a source outside that role's domain no matter what
    a client sends."""

    payer_type: FinePayerType
    payer_id: uuid.UUID
    amount: float = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=500)


class FineResolveRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class FinePaidRequest(FineResolveRequest):
    """How the fine was paid. Method defaults to cash; anything else needs a reference."""

    method: str | None = Field(default=None, max_length=20)
    reference: str | None = Field(default=None, max_length=80)
    paid_on: date | None = None


class FineRejectRequest(BaseModel):
    """A rejection must always say why — the requester (librarian/
    accounts officer) is notified with this note and has no other way
    to find out what was wrong with their request."""

    note: str = Field(min_length=1, max_length=500)


class FineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payer_type: FinePayerType
    payer_id: uuid.UUID
    amount: float
    reason: str
    source: FineSource
    status: FineStatus
    issued_at: datetime
    approved_at: datetime | None
    resolved_at: datetime | None
    resolution_note: str | None
    payment_method: str | None = None
    payment_reference: str | None = None
    paid_on: date | None = None
    receipt_number: str | None = None


class FineDetail(FineOut):
    """Enriched with the payer's name and identifier (roll number /
    employee code) and who issued/approved/resolved it — what the
    admin/accounts 'deep tracking' view actually needs, not just raw
    IDs."""

    payer_name: str
    payer_identifier: str | None
    issued_by_name: str | None
    approved_by_name: str | None
    resolved_by_name: str | None


class FinePayerSearchResult(BaseModel):
    id: uuid.UUID
    name: str
    identifier: str | None


class FineBalanceOut(BaseModel):
    payer_type: FinePayerType
    payer_id: uuid.UUID
    total_pending: float
    total_paid: float
    total_waived: float
    pending_count: int