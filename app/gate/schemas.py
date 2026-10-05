from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator


def _days(v: list[int]) -> list[int]:
    v = sorted(set(v))
    if not v or any(d < 0 or d > 6 for d in v):
        raise ValueError("Choose at least one day (0 = Monday ... 6 = Sunday).")
    return v


# ------------------------------------------------------------------ rules the administration sets
class SettingsIn(BaseModel):
    student_in_time: time
    student_out_time: time | None = None
    student_grace_minutes: int = Field(ge=0, le=180)
    student_days: list[int]
    student_fine_per_hour: Decimal = Field(ge=0, le=100000)
    student_max_fine_per_day: Decimal | None = Field(default=None, ge=0, le=1000000)
    student_auto_fine: bool = True
    staff_default_in: time | None = None
    staff_default_out: time | None = None
    staff_grace_minutes: int = Field(ge=0, le=180)
    staff_default_days: list[int]

    @model_validator(mode="after")
    def _check(self) -> "SettingsIn":
        self.student_days, self.staff_default_days = _days(self.student_days), _days(self.staff_default_days)
        if self.student_out_time is not None and self.student_out_time <= self.student_in_time:
            raise ValueError("The students' leaving time must be after their arrival time.")
        if (self.staff_default_in is None) != (self.staff_default_out is None):
            raise ValueError("Set both the default staff arrival and leaving time, or neither.")
        if self.staff_default_in is not None and self.staff_default_out <= self.staff_default_in:
            raise ValueError("The default staff leaving time must be after their arrival time.")
        return self


class SettingsOut(SettingsIn):
    pass


class ScheduleIn(BaseModel):
    in_time: time
    out_time: time
    working_days: list[int]

    @model_validator(mode="after")
    def _check(self) -> "ScheduleIn":
        self.working_days = _days(self.working_days)
        if self.out_time <= self.in_time:
            raise ValueError("The leaving time must be after the arrival time (night shifts that cross midnight are not supported yet).")
        return self


class BulkScope(str, Enum):
    ALL = "ALL"
    JOB_ROLE = "JOB_ROLE"


class BulkScheduleIn(ScheduleIn):
    scope: BulkScope
    job_role_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _needs_role(self) -> "BulkScheduleIn":
        if self.scope == BulkScope.JOB_ROLE and self.job_role_id is None:
            raise ValueError("Choose which job role to set it for.")
        return self


class ScheduleRow(BaseModel):
    employee_id: uuid.UUID
    employee_code: str
    name: str
    job_title: str
    job_role_id: uuid.UUID
    in_time: time | None
    out_time: time | None
    working_days: list[int]
    days_text: str
    is_default: bool            # no timing of their own: the default applies


# ------------------------------------------------------------------ the gate
class Kind(str, Enum):
    STUDENT = "STUDENT"
    EMPLOYEE = "EMPLOYEE"


class MarkIn(BaseModel):
    kind: Kind
    person_id: uuid.UUID


class FineBrief(BaseModel):
    id: uuid.UUID
    amount: Decimal
    status: str


class PersonOut(BaseModel):
    kind: Kind
    person_id: uuid.UUID
    name: str
    identifier: str | None
    group: str | None
    expected_in: time | None
    expected_out: time | None
    working_today: bool
    state: str                     # NOT_ARRIVED, INSIDE, LEFT
    check_in_at: datetime | None
    check_out_at: datetime | None
    late_minutes: int
    early_minutes: int
    log_id: uuid.UUID | None
    can_check_in: bool
    can_check_out: bool
    can_undo: bool
    fine: FineBrief | None = None


class PeopleOut(BaseModel):
    people: list[PersonOut]
    has_more: bool


class LogOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    person_id: uuid.UUID
    name: str
    identifier: str | None
    group: str | None
    log_date: date
    check_in_at: datetime | None
    check_out_at: datetime | None
    expected_in: time | None
    expected_out: time | None
    late_minutes: int
    early_minutes: int
    is_working_day: bool
    in_marked_by: str | None
    out_marked_by: str | None
    note: str | None
    fine: FineBrief | None
    can_undo: bool
    warning: str | None = None


class LogEdit(BaseModel):
    check_in_time: time | None = None
    check_out_time: time | None = None
    clear_check_out: bool = False
    note: str | None = Field(default=None, max_length=500)


class SummaryOut(BaseModel):
    date: date
    inside_students: int
    inside_staff: int
    checked_in_students: int
    checked_in_staff: int
    late_students: int
    late_staff: int
    left_today: int
    staff_not_arrived: int         # staff expected today who have not been marked in


class ProgramRef(BaseModel):
    id: uuid.UUID
    code: str


class JobRoleRef(BaseModel):
    id: uuid.UUID
    title: str


class FiltersOut(BaseModel):
    programs: list[ProgramRef]
    semesters: list[int]
    job_roles: list[JobRoleRef]


# ------------------------------------------------------------------ fines
class FineRow(BaseModel):
    fine_id: uuid.UUID
    student_id: uuid.UUID
    name: str
    roll_number: str | None
    group: str | None
    log_id: uuid.UUID | None
    log_date: date | None
    arrived_at: datetime | None
    expected_in: time | None
    late_minutes: int
    hours: int
    amount: Decimal
    suggested_amount: Decimal | None
    status: str
    reason: str
    decision_note: str | None
    decided_at: datetime | None


class Decision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class FineDecision(BaseModel):
    action: Decision
    amount: Decimal | None = Field(default=None, gt=0, le=1000000)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _reject_needs_a_reason(self) -> "FineDecision":
        if self.action == Decision.REJECT and len((self.note or "").strip()) < 3:
            raise ValueError("Say why the fine is not being charged.")
        return self


# ------------------------------------------------------------------ report
class LatePerson(BaseModel):
    kind: Kind
    person_id: uuid.UUID
    name: str
    identifier: str | None
    group: str | None
    days_present: int
    late_days: int
    total_late_minutes: int
    average_late_minutes: int
    longest_late_minutes: int
    fines_approved: Decimal
    fines_pending: Decimal


class WeekLate(BaseModel):
    week_start: date
    late: int
    present: int


class ReportOut(BaseModel):
    days: int
    since: date | None
    present_days: int
    late_days: int
    late_rate: float | None
    total_late_minutes: int
    fines_approved: Decimal
    fines_pending: Decimal
    weeks: list[WeekLate]
    people: list[LatePerson]