from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.fees.models import AssignmentStatus, FeeStructureStatus, LedgerEntryType, PayerType, PaymentMethod


class FeeTypeCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class FeeTypeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class FeeTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool


class FeeStructureCreateRequest(BaseModel):
    fee_type_id: uuid.UUID
    name: str = Field(min_length=1, max_length=150)
    term_label: str = Field(min_length=1, max_length=50)
    total_amount: float = Field(gt=0)
    installment_count: int = Field(default=1, ge=1, le=24)
    installment_interval_months: int = Field(default=1, ge=1, le=12)
    effective_from: date
    late_fee_per_day: float | None = Field(default=None, ge=0)
    applies_to_students: bool = True
    applies_to_employees: bool = False
    program_ids: list[uuid.UUID] = Field(default_factory=list)
    semester_numbers: list[int] = Field(default_factory=list, description="Empty = every semester")
    # Course-scoped fee (charged per enrollment, including retakes — see
    # FeeStructure.course_id in app/fees/models.py) instead of the
    # program/semester scoping above. Was previously missing from this
    # request schema entirely even though FeeStructureService.create()
    # has always unconditionally read data.course_id — meaning every
    # real POST /fees/structures call crashed with a 500
    # AttributeError before this fix, regardless of whether the caller
    # wanted a course-scoped fee or not.
    course_id: uuid.UUID | None = Field(default=None)

    @field_validator("semester_numbers")
    @classmethod
    def _validate_semester_numbers(cls, v: list[int]) -> list[int]:
        for n in v:
            if n < 1 or n > 20:
                raise ValueError("Semester numbers must be between 1 and 20.")
        return sorted(set(v))


class CloneStructureRequest(BaseModel):
    """Rolls an existing fee structure forward into a new term at an
    adjusted amount — the correct way to 'raise fees 10%' without
    touching the original structure or anyone's existing charges. Copies
    fee type, program scope, installment settings, and late-fee rate from
    the source; only the term, amount, and effective date change."""

    new_term_label: str = Field(min_length=1, max_length=50)
    percentage_change: float = Field(
        ge=-100, le=1000, description="e.g. 10 for a 10% increase, -5 for a 5% decrease, 0 for no change"
    )
    effective_from: date
    new_name: str | None = Field(default=None, max_length=150)


class FeeStructureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fee_type_id: uuid.UUID
    fee_type_name: str
    name: str
    term_label: str
    total_amount: float
    installment_count: int
    installment_interval_months: int
    effective_from: date
    late_fee_per_day: float | None
    late_fee_once: bool = False            # True: a fixed late fine, charged once (School mode vouchers)
    applies_to_students: bool
    applies_to_employees: bool
    status: FeeStructureStatus
    program_names: list[str]
    semester_numbers: list[int]
    assignment_count: int
    # Previously missing entirely: app/fees/router.py::_to_structure_out
    # has always computed both of these (including a Course lookup just
    # to build course_label) and passed them into this model's
    # constructor — but Pydantic v2 silently drops unrecognized
    # constructor kwargs by default, so neither field ever actually
    # reached any API response. No caller (including the templates)
    # could ever tell a course-scoped fee structure apart from an
    # ordinary one through this endpoint.
    course_id: uuid.UUID | None = None
    course_label: str | None = None


class FeeAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    late_fee_posted: float | None = None          # set by "record a payment" when it settled a late instalment (its late fee went to Fines)

    id: uuid.UUID
    fee_structure_id: uuid.UUID
    fee_structure_name: str
    payer_type: PayerType
    payer_id: uuid.UUID
    payer_name: str
    status: AssignmentStatus
    total_concession: float = 0.0
    total_charged: float
    total_payable: float = 0.0       # charged minus scholarships / write-offs
    total_remaining: float = 0.0     # still to be paid, due now or later
    total_paid: float
    overdue_principal: float
    accrued_fine: float
    total_due_now: float
    is_in_good_standing: bool
    payer_identifier: str | None = None     # roll number or employee code
    term_label: str | None = None


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_type: LedgerEntryType
    amount: float
    installment_number: int | None
    due_date: date | None
    description: str | None
    created_at: datetime
    payment_method: str | None = None
    payment_reference: str | None = None
    paid_on: date | None = None
    receipt_number: str | None = None


class PaymentDetails(BaseModel):
    """How a payment was made — required for every staff-recorded payment so
    collections can be reconciled with the bank statement / cash book."""

    method: PaymentMethod
    reference: str | None = Field(default=None, max_length=100, description="Deposit slip / transaction / cheque number. Required unless CASH.")
    paid_on: date | None = Field(default=None, description="The day the money was paid. Defaults to today.")

    @model_validator(mode="after")
    def _reference_needed(self):
        if self.method != PaymentMethod.CASH and not (self.reference or "").strip():
            raise ValueError("A reference (deposit slip, transaction or cheque number) is required unless the payment was cash.")
        if self.method == PaymentMethod.ONE_BILL:
            raise ValueError("1Bill payments are recorded by the bank integration, not by hand.")
        self.reference = (self.reference or "").strip() or None
        return self


class RecordPaymentRequest(PaymentDetails):
    amount: float = Field(gt=0)
    description: str | None = Field(default=None, max_length=255)


class AssignmentStatusUpdateRequest(BaseModel):
    status: AssignmentStatus
    reason: str | None = Field(default=None, max_length=500)     # required by the service (kept in the audit log)


class MarkUnpaidRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class MarkPaidRequest(PaymentDetails):
    note: str | None = Field(default=None, max_length=255)


class RecentPaymentOut(BaseModel):
    payer_name: str
    fee_structure_name: str
    amount: float
    created_at: str


class FeeDashboardResponse(BaseModel):
    total_fee_structures: int
    active_fee_structures: int
    total_collected: float
    total_charged: float
    total_outstanding: float
    total_overdue: float
    overdue_assignment_count: int
    active_assignment_count: int
    recent_payments: list[RecentPaymentOut]


class ChallanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    late_fee_posted: float | None = None          # set when paying this settled a late instalment and its late fee went to Fines

    id: uuid.UUID
    fee_assignment_id: uuid.UUID
    consumer_number: str
    amount: float
    due_date: date
    status: str
    provider: str
    gateway_reference: str | None
    paid_at: datetime | None
    generated_at: datetime