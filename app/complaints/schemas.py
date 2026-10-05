from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.complaints.models import ComplaintPartyType, ComplaintReason, ComplaintStatus


class ComplaintCreateRequest(BaseModel):
    target_type: ComplaintPartyType
    target_id: uuid.UUID
    reason: ComplaintReason
    custom_reason: str | None = Field(default=None, max_length=255)
    description: str = Field(min_length=10, max_length=2000)
    is_anonymous: bool = False

    @model_validator(mode="after")
    def _require_custom_reason_when_other(self) -> "ComplaintCreateRequest":
        if self.reason == ComplaintReason.OTHER and not (self.custom_reason and self.custom_reason.strip()):
            raise ValueError("custom_reason is required when reason is OTHER.")
        return self


class ComplaintStatusUpdateRequest(BaseModel):
    """Admin-only — can move to any of these three; SUBMITTED is the
    only status a complaint starts in and is never set manually, and
    WITHDRAWN is reserved for the complainant's own action."""

    status: ComplaintStatus = Field(description="One of UNDER_REVIEW, RESOLVED, DISMISSED")
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _restrict_admin_settable_statuses(self) -> "ComplaintStatusUpdateRequest":
        allowed = {ComplaintStatus.UNDER_REVIEW, ComplaintStatus.RESOLVED, ComplaintStatus.DISMISSED}
        if self.status not in allowed:
            raise ValueError(f"Admin can only set status to one of: {', '.join(s.value for s in allowed)}.")
        return self


class ComplaintSelfActionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class ComplaintReasonOption(BaseModel):
    value: str
    label: str


class ComplaintTargetSearchResult(BaseModel):
    id: uuid.UUID
    name: str
    identifier: str | None
    role_label: str  # "Student", or the employee's job role title (e.g. "Teacher", "Librarian")


class ComplaintOut(BaseModel):
    id: uuid.UUID
    # None when the complaint is anonymous and the viewer is not the person who filed it
    complainant_type: ComplaintPartyType | None
    complainant_id: uuid.UUID | None
    is_anonymous: bool = False
    target_type: ComplaintPartyType
    target_id: uuid.UUID
    reason: ComplaintReason
    custom_reason: str | None
    description: str
    status: ComplaintStatus
    resolution_note: str | None
    under_review_at: datetime | None = None
    resolved_at: datetime | None
    created_at: datetime


class ComplaintDetail(ComplaintOut):
    complainant_name: str
    complainant_identifier: str | None
    target_name: str
    target_identifier: str | None
    target_role_label: str
    resolved_by_name: str | None


class ComplaintAnalyticsEntry(BaseModel):
    target_type: ComplaintPartyType
    target_id: uuid.UUID
    target_name: str
    target_role_label: str
    total_count: int
    open_count: int  # SUBMITTED + UNDER_REVIEW — the ones still needing attention
    highlighted: bool  # True when total_count crosses the notable-pattern threshold


class ComplaintIdentityOut(BaseModel):
    """What an administrator sees after deliberately revealing who filed an anonymous complaint."""

    complaint_id: uuid.UUID
    complainant_type: ComplaintPartyType
    complainant_id: uuid.UUID
    complainant_name: str
    complainant_identifier: str | None


class CountItem(BaseModel):
    key: str
    label: str
    count: int


class WeekPoint(BaseModel):
    week_start: date
    filed: int
    closed: int


class ComplaintKpis(BaseModel):
    total: int
    open: int                    # filed, or being reviewed: the ones still needing attention
    under_review: int
    resolved: int
    dismissed: int
    withdrawn: int
    anonymous: int
    anonymous_pct: float
    avg_resolution_hours: float | None
    filed_last_30_days: int
    filed_previous_30_days: int
    oldest_open_days: int | None


class ComplaintOverviewOut(BaseModel):
    kpis: ComplaintKpis
    by_status: list[CountItem]
    by_reason: list[CountItem]
    by_week: list[WeekPoint]
    filed_by: list[CountItem]        # who files complaints: students / employees
    filed_against: list[CountItem]   # who they are about


class PublicComplaintKpis(BaseModel):
    total: int
    open: int
    under_review: int
    resolved: int
    dismissed: int
    withdrawn: int
    avg_resolution_hours: float | None
    filed_last_30_days: int
    filed_previous_30_days: int


class PublicComplaintStats(BaseModel):
    """What every student and staff member may see about how complaints are handled (aggregate only)."""

    kpis: PublicComplaintKpis
    by_status: list[CountItem]
    by_reason: list[CountItem]
    by_week: list[WeekPoint]
    resolution_rate: float | None