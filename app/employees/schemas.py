from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth.permissions import Roles
from app.core.reference_data import validate_city, validate_district, validate_province, validate_religion
from app.employees.models import EmployeeStatus
from app.imports.validators import CNIC_RE, PHONE_RE, VALID_GENDERS, normalize_phone as _normalize_phone_raw
from app.profiles.schemas import QualificationOut


def _normalize_cnic(value: str) -> str:
    if not CNIC_RE.match(value.strip()):
        raise ValueError("CNIC must be 13 digits, formatted as 12345-1234567-1.")
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"{digits[0:5]}-{digits[5:12]}-{digits[12]}"


def _normalize_phone(value: str) -> str:
    cleaned = _normalize_phone_raw(value)
    if not PHONE_RE.match(cleaned):
        raise ValueError("Must be a valid Pakistani mobile number (03XXXXXXXXX or +923XXXXXXXXX).")
    return cleaned

EMPLOYMENT_TYPES = ("PERMANENT", "CONTRACT", "VISITING", "PART_TIME")
_BLOOD_GROUPS = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}


def _clean_email(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    v = v.strip().lower()
    if "@" not in v or " " in v or "." not in v.split("@")[-1] or len(v) > 255:
        raise ValueError("Enter a valid email address.")
    return v


def _clean_blood_group(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    v = v.strip().upper().replace(" ", "")
    if v not in _BLOOD_GROUPS:
        raise ValueError("Blood group must be one of A+, A-, B+, B-, AB+, AB-, O+, O-.")
    return v


def _clean_marital_status(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    v = v.strip().capitalize()
    if v not in {"Single", "Married", "Divorced", "Widowed"}:
        raise ValueError("Marital status must be one of: Divorced, Married, Single, Widowed.")
    return v


def _clean_employment_type(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    v = v.strip().upper().replace(" ", "_").replace("-", "_")
    if v not in EMPLOYMENT_TYPES:
        raise ValueError("Employment type must be one of: " + ", ".join(EMPLOYMENT_TYPES) + ".")
    return v



# ---------------------------------------------------------------------------
# Job roles
# ---------------------------------------------------------------------------
# Real security gap, closed here: Roles.ALL includes ADMIN and STUDENT,
# both of which used to be accepted as a job role's default_role_code.
# One misconfigured job role would have made every new hire an admin
# (or, just as wrong, a student) — ADMIN is granted explicitly per
# person via /users, never inherited from a job title, and STUDENT is
# a completely separate onboarding flow (app/students), not staff at
# all. Job roles may only map to an actual STAFF system role.
JOB_ROLE_ASSIGNABLE_ROLE_CODES = tuple(r for r in Roles.ALL if r not in (Roles.ADMIN, Roles.STUDENT))


class JobRoleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=100)
    default_role_code: str

    @field_validator("default_role_code")
    @classmethod
    def _validate_role_code(cls, v: str) -> str:
        if v not in JOB_ROLE_ASSIGNABLE_ROLE_CODES:
            raise ValueError(
                f"default_role_code must be one of: {', '.join(JOB_ROLE_ASSIGNABLE_ROLE_CODES)}. "
                "ADMIN and STUDENT can't be assigned through a job role."
            )
        return v


class JobRoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    title: str
    default_role_code: str
    is_active: bool


class JobRoleUpdate(BaseModel):
    """Edit a job role. `code` is deliberately not editable — it's the
    stable identifier other data (audit logs, historical references)
    keys against, same reasoning as Program.code. `title` and
    `default_role_code` are safe to change after the fact: existing
    employees already have their RBAC role assigned at hire time (it's
    not re-derived live), so changing default_role_code only affects
    people hired after the change."""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    default_role_code: str | None = None
    is_active: bool | None = None

    @field_validator("default_role_code")
    @classmethod
    def _validate_role_code(cls, v: str | None) -> str | None:
        if v is not None and v not in JOB_ROLE_ASSIGNABLE_ROLE_CODES:
            raise ValueError(
                f"default_role_code must be one of: {', '.join(JOB_ROLE_ASSIGNABLE_ROLE_CODES)}. "
                "ADMIN and STUDENT can't be assigned through a job role."
            )
        return v


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------
class EmployeeCreateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    gender: str
    cnic: str
    contact_no: str
    hire_date: date
    department: str | None = Field(default=None, max_length=100)
    job_role_id: uuid.UUID
    base_salary: float = Field(ge=0)
    # Admin sets this directly per the approved decision — no fixed
    # institution-wide default for staff the way students have vertex123.
    password: str = Field(min_length=10, max_length=256)

    # Profile — all optional at creation time (an employee can exist and
    # log in before these are filled in, same as a student without a
    # program yet), dropdown-validated per approved decision.
    religion: str | None = Field(default=None, max_length=50)
    province: str | None = None
    district: str | None = None
    city: str | None = None
    address_line: str | None = Field(default=None, max_length=255)
    email: str | None = None
    blood_group: str | None = None
    marital_status: str | None = None
    employment_type: str | None = None
    emergency_contact_name: str | None = Field(default=None, max_length=150)
    emergency_contact_no: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email_field(cls, v: str | None) -> str | None:
        return _clean_email(v)

    @field_validator("blood_group")
    @classmethod
    def _validate_blood_group_field(cls, v: str | None) -> str | None:
        return _clean_blood_group(v)

    @field_validator("marital_status")
    @classmethod
    def _validate_marital_status_field(cls, v: str | None) -> str | None:
        return _clean_marital_status(v)

    @field_validator("employment_type")
    @classmethod
    def _validate_employment_type_field(cls, v: str | None) -> str | None:
        return _clean_employment_type(v)

    @field_validator("emergency_contact_no")
    @classmethod
    def _validate_emergency_contact_no_field(cls, v: str | None) -> str | None:
        return _normalize_phone(v) if v else None

    @field_validator("gender")
    @classmethod
    def _validate_gender(cls, v: str) -> str:
        g = v.strip().capitalize()
        if g not in VALID_GENDERS:
            raise ValueError(f"Gender must be one of: {', '.join(sorted(VALID_GENDERS))}.")
        return g

    @field_validator("cnic")
    @classmethod
    def _validate_cnic_field(cls, v: str) -> str:
        return _normalize_cnic(v)

    @field_validator("contact_no")
    @classmethod
    def _validate_contact_no_field(cls, v: str) -> str:
        return _normalize_phone(v)

    @field_validator("religion")
    @classmethod
    def _validate_religion_field(cls, v: str | None) -> str | None:
        return validate_religion(v) if v is not None else None

    @field_validator("province")
    @classmethod
    def _validate_province_field(cls, v: str | None) -> str | None:
        return validate_province(v) if v is not None else None

    @field_validator("city")
    @classmethod
    def _validate_city_field(cls, v: str | None) -> str | None:
        return validate_city(v) if v is not None else None

    @model_validator(mode="after")
    def _validate_district_against_province(self) -> "EmployeeCreateRequest":
        if self.district is not None:
            self.district = validate_district(self.district, self.province)
        return self


class EmployeeListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    employee_code: str
    status: EmployeeStatus
    job_role_id: uuid.UUID
    # For a readable staff table (no CNIC, address or bank details here).
    job_title: str | None = None
    department: str | None = None
    contact_no: str | None = None
    hire_date: date | None = None
    employment_type: str | None = None
    gender: str | None = None
    has_photo: bool = False


class EmployeeDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    employee_code: str
    cnic: str
    contact_no: str
    date_of_birth: date
    gender: str
    hire_date: date
    department: str | None
    status: EmployeeStatus
    job_role_id: uuid.UUID
    username: str | None = None
    religion: str | None = None
    province: str | None = None
    district: str | None = None
    city: str | None = None
    address_line: str | None = None
    email: str | None = None
    blood_group: str | None = None
    marital_status: str | None = None
    employment_type: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_no: str | None = None
    photo_original_name: str | None = None
    has_photo: bool = False
    certificate_original_name: str | None = None
    has_certificate: bool = False
    bank_name: str | None = None
    bank_account_title: str | None = None
    bank_iban: str | None = None


class EmployeeUpdateRequest(BaseModel):
    """Partial update, ADMIN only — deliberately excludes cnic
    (identity-fraud vector, not a data-correction one), employee_code
    (system-generated, doubles as the permanent login username), and
    job_role_id (a role change is an HR action with its own workflow,
    not a free-form field edit). Every field optional; only provided
    fields are changed."""

    contact_no: str | None = None
    department: str | None = Field(default=None, max_length=100)
    religion: str | None = Field(default=None, max_length=50)
    province: str | None = None
    district: str | None = None
    city: str | None = None
    address_line: str | None = Field(default=None, max_length=255)
    email: str | None = None
    blood_group: str | None = None
    marital_status: str | None = None
    employment_type: str | None = None
    emergency_contact_name: str | None = Field(default=None, max_length=150)
    emergency_contact_no: str | None = None
    bank_name: str | None = Field(default=None, max_length=100)
    bank_account_title: str | None = Field(default=None, max_length=150)
    bank_iban: str | None = Field(default=None, max_length=40)
    # Data-entry corrections (typos in a name / birth date / hire date could
    # previously never be fixed). CNIC and employee_code stay immutable.
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    date_of_birth: date | None = None
    gender: str | None = None
    hire_date: date | None = None

    @field_validator("gender")
    @classmethod
    def _validate_gender_field(cls, v: str | None) -> str | None:
        if v is None:
            return None
        g = v.strip().capitalize()
        if g not in VALID_GENDERS:
            raise ValueError(f"Gender must be one of: {', '.join(sorted(VALID_GENDERS))}.")
        return g

    @field_validator("email")
    @classmethod
    def _validate_email_field(cls, v: str | None) -> str | None:
        return _clean_email(v)

    @field_validator("blood_group")
    @classmethod
    def _validate_blood_group_field(cls, v: str | None) -> str | None:
        return _clean_blood_group(v)

    @field_validator("marital_status")
    @classmethod
    def _validate_marital_status_field(cls, v: str | None) -> str | None:
        return _clean_marital_status(v)

    @field_validator("employment_type")
    @classmethod
    def _validate_employment_type_field(cls, v: str | None) -> str | None:
        return _clean_employment_type(v)

    @field_validator("emergency_contact_no")
    @classmethod
    def _validate_emergency_contact_no_field(cls, v: str | None) -> str | None:
        return _normalize_phone(v) if v else None

    @field_validator("bank_iban")
    @classmethod
    def _validate_bank_iban(cls, v: str | None) -> str | None:
        """A Pakistani IBAN (PK, 2 check digits, 4-letter bank code, 16 digits) or a plain account number."""
        if v is None:
            return None
        v = "".join(v.split()).upper().replace("-", "")
        if not v:
            return ""
        if v.startswith("PK"):
            import re as _re

            if not _re.fullmatch(r"PK\d{2}[A-Z]{4}\d{16}", v):
                raise ValueError("A Pakistani IBAN is PK, 2 digits, the 4-letter bank code and 16 digits (24 characters).")
            moved = v[4:] + v[:4]
            if int("".join(str(int(c, 36)) for c in moved)) % 97 != 1:
                raise ValueError("This IBAN's check digits are wrong. Copy it again from the bank letter or cheque book.")
            return v
        if not v.isdigit() or not 6 <= len(v) <= 20:
            raise ValueError("Give the IBAN (starts with PK) or the account number (digits only).")
        return v

    @field_validator("contact_no")
    @classmethod
    def _validate_contact_no_field(cls, v: str | None) -> str | None:
        return _normalize_phone(v) if v is not None else None

    @field_validator("religion")
    @classmethod
    def _validate_religion_field(cls, v: str | None) -> str | None:
        return validate_religion(v) if v is not None else None

    @field_validator("province")
    @classmethod
    def _validate_province_field(cls, v: str | None) -> str | None:
        return validate_province(v) if v is not None else None

    @field_validator("city")
    @classmethod
    def _validate_city_field(cls, v: str | None) -> str | None:
        return validate_city(v) if v is not None else None

    @model_validator(mode="after")
    def _validate_district_against_province(self) -> "EmployeeUpdateRequest":
        if self.district is not None:
            self.district = validate_district(self.district, self.province)
        return self


class EmployeeSelfDetail(BaseModel):
    """What an EMPLOYEE sees of their own record via /employees/me —
    same full-detail philosophy as StudentSelfDetail: nothing hidden
    from someone about their own data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    employee_code: str
    cnic: str
    contact_no: str
    date_of_birth: date
    gender: str
    hire_date: date
    department: str | None
    status: EmployeeStatus
    job_role_id: uuid.UUID
    religion: str | None
    province: str | None
    district: str | None
    city: str | None
    address_line: str | None
    email: str | None = None
    blood_group: str | None = None
    marital_status: str | None = None
    employment_type: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_no: str | None = None
    photo_original_name: str | None = None
    has_photo: bool = False
    certificate_original_name: str | None = None
    has_certificate: bool = False


class EmployeeCreatedResponse(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    employee_code: str
    username: str
    status: EmployeeStatus


class EmployeeStatusChangeRequest(BaseModel):
    new_status: EmployeeStatus
    reason: str | None = None


class EmployeePasswordResetRequest(BaseModel):
    """Unlike students (fixed vertex123 default), employee passwords are
    admin-set per person at creation — so 'reset' here means the admin
    chooses a new password directly, not a reset to some shared default
    that doesn't exist for this role."""

    new_password: str = Field(min_length=10, max_length=256)


# ---------------------------------------------------------------------------
# Salary / bonuses
# ---------------------------------------------------------------------------
class SalaryIncrementRequest(BaseModel):
    change_type: str  # "FIXED_AMOUNT" | "PERCENTAGE"
    value: float = Field(gt=0)
    reason: str | None = Field(default=None, max_length=255)
    effective_from: date | None = None  # defaults to today if omitted

    @field_validator("change_type")
    @classmethod
    def _validate_change_type(cls, v: str) -> str:
        if v not in ("FIXED_AMOUNT", "PERCENTAGE"):
            raise ValueError("change_type must be FIXED_AMOUNT or PERCENTAGE.")
        return v


class SalaryHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    base_salary: float
    effective_from: date
    effective_to: date | None
    change_type: str
    change_value: float | None
    reason: str | None


class BonusCreateRequest(BaseModel):
    amount: float = Field(gt=0)
    reason: str = Field(min_length=1, max_length=255)
    awarded_at: date | None = None  # defaults to today if omitted


class BonusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount: float
    reason: str
    awarded_at: date


class SalaryHistoryEntry(BaseModel):
    base_salary: float
    effective_from: date
    effective_to: date | None
    reason: str | None


class BonusEntry(BaseModel):
    amount: float
    reason: str
    awarded_at: date


class TeachingSummary(BaseModel):
    offerings_count: int
    total_students_taught: int
    passed_count: int
    failed_count: int
    pass_rate: float | None


class EmployeeFeeSummary(BaseModel):
    total_assignments: int
    total_due: float
    total_paid: float
    total_outstanding: float


class EmployeeLibrarySummary(BaseModel):
    active_loans: int
    overdue_loans: int
    past_loans: int


class EmployeeFineSummary(BaseModel):
    total_pending: float
    total_paid: float
    total_waived: float


class EmployeeComplaintSummary(BaseModel):
    filed_by_them: int
    filed_against_them: int


class EmployeeFullProfileResponse(BaseModel):
    """The 'everything about this employee in one place' view —
    counterpart to StudentFullProfileResponse in app/students/schemas.py.
    Same philosophy: each module's own dedicated page still has the
    full deep-dive (every payslip, every book ever borrowed), this is
    the at-a-glance aggregate a real HR/SIS profile page shows first."""

    id: uuid.UUID
    first_name: str
    last_name: str
    employee_code: str
    cnic: str
    contact_no: str
    date_of_birth: date
    gender: str
    hire_date: date
    department: str | None
    status: EmployeeStatus
    job_role_title: str | None
    religion: str | None
    email: str | None = None
    blood_group: str | None = None
    marital_status: str | None = None
    employment_type: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_no: str | None = None
    photo_original_name: str | None
    has_photo: bool
    certificate_original_name: str | None
    has_certificate: bool

    present_address: str | None
    province: str | None
    district: str | None
    city: str | None

    qualifications: list[QualificationOut]

    current_salary: float | None
    salary_history: list[SalaryHistoryEntry]
    bonuses: list[BonusEntry]

    teaching: TeachingSummary | None  # only populated if they hold the TEACHER role

    fees: EmployeeFeeSummary
    library: EmployeeLibrarySummary
    fines: EmployeeFineSummary
    complaints: EmployeeComplaintSummary


class JobRoleChangeRequest(BaseModel):
    """HR action: move an employee to a different job title. Because a job
    title maps to a system (RBAC) role, this also swaps their login role."""

    new_job_role_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=255)


class EmployeeClearanceOut(BaseModel):
    """Everything still attached to an employee -- what HR must settle
    before/after termination. Informational; never blocks the status change."""

    active_teaching_offerings: int
    open_library_loans: int
    unpaid_fines: int
    outstanding_fee_balance: float
    can_leave_cleanly: bool