from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.reference_data import validate_city, validate_district, validate_province, validate_religion
from app.imports.validators import (
    CNIC_RE,
    PHONE_RE,
    VALID_GENDERS,
    VALID_MARITAL_STATUS,
    normalize_phone as _normalize_phone_raw,
)
from app.profiles.schemas import QualificationOut
from app.students.models import StudentStatus


def _normalize_cnic(value: str) -> str:
    if not CNIC_RE.match(value.strip()):
        raise ValueError("CNIC must be 13 digits, formatted as 12345-1234567-1.")
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"{digits[0:5]}-{digits[5:12]}-{digits[12]}"


def _normalize_phone(value: str) -> str:
    # Uses the shared normalizer (which restores a leading zero Excel may
    # have dropped) before validating — same fix as CSV import, same code
    # path, so the two can never drift apart again.
    cleaned = _normalize_phone_raw(value)
    if not PHONE_RE.match(cleaned):
        raise ValueError("Must be a valid Pakistani mobile number (03XXXXXXXXX or +923XXXXXXXXX).")
    return cleaned


class StudentListItem(BaseModel):
    """Compact row shape for list/search endpoints — deliberately excludes
    CNIC, guardian info, and address to minimize sensitive-data exposure
    (Section 43) on high-traffic list views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    roll_number: str | None
    program_id: uuid.UUID | None
    current_semester: int | None = None
    section_label: str | None = None
    status: StudentStatus
    # For a readable directory table. Still no CNIC, guardian or address here.
    program_code: str | None = None
    program_name: str | None = None
    gender: str | None = None
    contact_no: str | None = None
    admission_session: str | None = None
    has_photo: bool = False
    # Only for the administration and the accounts office (app/students/family.py): the father's (else the main
    # guardian's) name and relation, e.g. "s/o Ahmed Khan" / "Guardian: Sara Bibi (Mother)". Never the CNIC or phone.
    guardian_name: str | None = None
    guardian_label: str | None = None


class StudentAdminDetail(BaseModel):
    """Full detail — ADMIN only."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str
    cnic: str
    contact_no: str
    marital_status: str
    religion: str
    nationality: str
    roll_number: str | None
    program_id: uuid.UUID | None
    current_semester: int | None
    status: StudentStatus
    enrollment_date: date | None
    email: str | None = None
    blood_group: str | None = None
    admission_session: str | None = None
    admission_category: str | None = None
    section_label: str | None = None
    present_address: str | None = None
    province: str | None = None
    district: str | None = None
    city: str | None = None
    photo_original_name: str | None = None
    has_photo: bool = False
    certificate_original_name: str | None = None
    has_certificate: bool = False


class StudentSelfDetail(BaseModel):
    """What a STUDENT sees of their own record — no CNIC/guardian CNIC
    surfaced beyond what the student already knows about themselves, and
    definitely nothing about other students.

    UPDATED per approved decision: expanded to the student's own full
    detail — the earlier CNIC-hiding rule was about not leaking a
    GUARDIAN's CNIC via a student's session, not about hiding a
    student's own identity fields from themselves."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str
    cnic: str
    contact_no: str
    marital_status: str
    religion: str
    nationality: str
    roll_number: str | None
    program_id: uuid.UUID | None
    current_semester: int | None
    status: StudentStatus
    admission_fee_paid: bool | None
    enrollment_date: date | None
    email: str | None = None
    blood_group: str | None = None
    admission_session: str | None = None
    admission_category: str | None = None
    section_label: str | None = None
    present_address: str | None = None
    province: str | None = None
    district: str | None = None
    city: str | None = None
    photo_original_name: str | None = None
    has_photo: bool = False
    certificate_original_name: str | None = None
    has_certificate: bool = False


class StudentStatusChangeRequest(BaseModel):
    new_status: StudentStatus
    reason: str | None = None
    # Leaving (DROPPED) cancels the fee installments not yet due; True keeps them for this student.
    keep_future_fees: bool = False


class StudentCreateRequest(BaseModel):
    """Manual single-student creation form. Uses the exact same format
    rules as CSV import (shared regex from app.imports.validators) so a
    row that would be accepted by bulk import is accepted here too, and
    vice versa — one set of rules, not two that can drift apart."""

    # Personal
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    gender: str
    cnic: str
    contact_no: str
    marital_status: str
    religion: str = Field(min_length=1, max_length=50)
    nationality: str = Field(min_length=1, max_length=50)

    # Guardian / emergency
    guardian_name: str = Field(min_length=1, max_length=150)
    guardian_cnic: str
    guardian_contact_no: str | None = None
    relation_with_guardian: str = Field(min_length=1, max_length=50)
    emergency_contact_no: str

    # Address
    present_address: str = Field(min_length=1, max_length=255)
    province: str = Field(min_length=1, max_length=100)
    district: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)

    # Academic (optional — a form-driven create picks a real program_id
    # from a dropdown rather than free-text code/name matching, which
    # sidesteps the CSV importer's ambiguity-resolution problem entirely)
    program_id: uuid.UUID | None = None
    current_semester: int | None = Field(default=None, ge=1, le=20)
    # roll_number is deliberately NOT an input field here — it's
    # system-generated (sequential per program/year, see
    # app/core/sequences.py) and doubles as the student's permanent
    # login username, so it can never be admin-supplied or edited.
    admission_fee_paid: bool | None = None
    enrollment_date: date | None = None
    email: str | None = None
    blood_group: str | None = None
    admission_session: str | None = None
    admission_category: str | None = None
    section_label: str | None = None

    # Real Pydantic validators — these run automatically while FastAPI
    # parses the request body, so bad input produces a clean 422 with a
    # field-level message, the same way every other endpoint in this API
    # behaves. (A ValueError raised from a plain helper method called
    # later, inside the service layer, is NOT caught by FastAPI's request
    # validation — it becomes an unhandled 500. That was the bug here.)
    @field_validator("section_label", check_fields=False)
    @classmethod
    def _validate_section_label_field(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = " ".join(v.split())
        if len(cleaned) > 30:
            raise ValueError("Section must be 30 characters or fewer.")
        return cleaned or None

    @field_validator("email", check_fields=False)
    @classmethod
    def _validate_email_field(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1] or " " in v or len(v) > 255:
            raise ValueError("Enter a valid email address.")
        return v

    @field_validator("blood_group", check_fields=False)
    @classmethod
    def _validate_blood_group_field(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().upper().replace(" ", "")
        if v not in {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}:
            raise ValueError("Blood group must be one of A+, A-, B+, B-, AB+, AB-, O+, O-.")
        return v

    @field_validator("gender")
    @classmethod
    def _validate_gender(cls, v: str) -> str:
        g = v.strip().capitalize()
        if g not in VALID_GENDERS:
            raise ValueError(f"Gender must be one of: {', '.join(sorted(VALID_GENDERS))}.")
        return g

    @field_validator("marital_status")
    @classmethod
    def _validate_marital_status(cls, v: str) -> str:
        m = v.strip().capitalize()
        if m not in VALID_MARITAL_STATUS:
            raise ValueError(f"Marital status must be one of: {', '.join(sorted(VALID_MARITAL_STATUS))}.")
        return m

    @field_validator("cnic")
    @classmethod
    def _validate_cnic_field(cls, v: str) -> str:
        return _normalize_cnic(v)

    @field_validator("guardian_cnic")
    @classmethod
    def _validate_guardian_cnic_field(cls, v: str) -> str:
        return _normalize_cnic(v)

    @field_validator("contact_no")
    @classmethod
    def _validate_contact_no_field(cls, v: str) -> str:
        return _normalize_phone(v)

    @field_validator("emergency_contact_no")
    @classmethod
    def _validate_emergency_contact_no_field(cls, v: str) -> str:
        return _normalize_phone(v)

    @field_validator("guardian_contact_no")
    @classmethod
    def _validate_guardian_contact_no_field(cls, v: str | None) -> str | None:
        if not v:
            return None
        return _normalize_phone(v)

    @field_validator("religion")
    @classmethod
    def _validate_religion_field(cls, v: str) -> str:
        return validate_religion(v)

    @field_validator("province")
    @classmethod
    def _validate_province_field(cls, v: str) -> str:
        return validate_province(v)

    @field_validator("city")
    @classmethod
    def _validate_city_field(cls, v: str) -> str:
        return validate_city(v)

    @model_validator(mode="after")
    def _validate_district_against_province(self) -> "StudentCreateRequest":
        self.district = validate_district(self.district, self.province)
        return self


class StudentCreatedResponse(BaseModel):
    """Returned after manual creation — includes the generated
    roll_number (which is also the login username) so the admin can
    hand it to the student. The password is the known institution-wide
    default, communicated out of band, not repeated here to avoid it
    ending up in logs/screenshots of this response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    username: str
    roll_number: str | None
    status: StudentStatus


class StudentUpdateRequest(BaseModel):
    """Partial update of a student's own record fields — deliberately
    excludes cnic (immutable identity document, changing it would be an
    identity-fraud vector, not a data-correction one) and status (that has
    its own dedicated endpoint with transition rules, not a free-form
    field edit). Every field optional; only provided fields are changed."""

    # Data-entry corrections (a typo in a name / birth date / gender could
    # previously never be fixed). CNIC and roll number stay immutable.
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    date_of_birth: date | None = None
    gender: str | None = None
    contact_no: str | None = None
    marital_status: str | None = None
    religion: str | None = Field(default=None, min_length=1, max_length=50)
    nationality: str | None = Field(default=None, min_length=1, max_length=50)
    program_id: uuid.UUID | None = None
    current_semester: int | None = Field(default=None, ge=1, le=20)
    # roll_number is NOT editable here — see StudentCreateRequest for why
    # (system-generated, doubles as the permanent login username).
    admission_fee_paid: bool | None = None
    enrollment_date: date | None = None
    email: str | None = None
    blood_group: str | None = None
    admission_session: str | None = None
    admission_category: str | None = None
    section_label: str | None = None

    # Address — updates the single PRESENT StudentAddress row. All
    # optional (partial update); if district is provided, province must
    # be provided in the same request too (or already be about to be
    # set) so the cross-check has something to validate against — see
    # the model validator below.
    present_address: str | None = Field(default=None, min_length=1, max_length=255)
    province: str | None = None
    district: str | None = None
    city: str | None = None

    @field_validator("section_label", check_fields=False)
    @classmethod
    def _validate_section_label_field(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = " ".join(v.split())
        if len(cleaned) > 30:
            raise ValueError("Section must be 30 characters or fewer.")
        return cleaned or None

    @field_validator("email", check_fields=False)
    @classmethod
    def _validate_email_field(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1] or " " in v or len(v) > 255:
            raise ValueError("Enter a valid email address.")
        return v

    @field_validator("blood_group", check_fields=False)
    @classmethod
    def _validate_blood_group_field(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().upper().replace(" ", "")
        if v not in {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}:
            raise ValueError("Blood group must be one of A+, A-, B+, B-, AB+, AB-, O+, O-.")
        return v

    @field_validator("gender")
    @classmethod
    def _validate_gender_update(cls, v: str | None) -> str | None:
        if v is None:
            return None
        g = v.strip().capitalize()
        if g not in VALID_GENDERS:
            raise ValueError(f"Gender must be one of: {', '.join(sorted(VALID_GENDERS))}.")
        return g

    @field_validator("contact_no")
    @classmethod
    def _validate_contact_no_field(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _normalize_phone(v)

    @field_validator("marital_status")
    @classmethod
    def _validate_marital_status_field(cls, v: str | None) -> str | None:
        if v is None:
            return None
        m = v.strip().capitalize()
        if m not in VALID_MARITAL_STATUS:
            raise ValueError(f"Marital status must be one of: {', '.join(sorted(VALID_MARITAL_STATUS))}.")
        return m

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
    def _validate_district_against_province(self) -> "StudentUpdateRequest":
        if self.district is not None:
            self.district = validate_district(self.district, self.province)
        return self


class AdvanceSemesterRequest(BaseModel):
    """Bulk end-of-term action: bumps current_semester by 1 for every
    matching active student. program_id=None means every active program's
    students at once. A student is silently SKIPPED (never advanced past
    a validation error) if: they have no current_semester set yet, they
    have no program assigned (nothing to validate the cap against), or
    they're already at their program's final semester (advancing further
    would mean 'more semesters than the program defines' — that's a
    graduation/status decision, not a semester increment)."""

    program_id: uuid.UUID | None = None
    # dry_run=True computes exactly what WOULD happen and changes nothing:
    # the UI calls this first so the admin sees who will be held back.
    dry_run: bool = False
    # Hold back students whose current-semester courses are still open
    # (status ENROLLED = "End Semester" not run / no result yet).
    skip_open_enrollments: bool = True
    # Hold back students with overdue fee principal (institute policy
    # varies, so this is opt-in).
    skip_overdue_fees: bool = False
    # Academic standing (Step 4): hold back students with too many failed
    # courses still to clear, or a CGPA below the minimum (thresholds in
    # settings), and students with any unpaid fine.
    skip_failed_courses: bool = True
    skip_low_cgpa: bool = True
    skip_unpaid_fines: bool = True


class SkippedStudent(BaseModel):
    roll_number: str | None
    name: str
    reason: str


class AdvanceSemesterResponse(BaseModel):
    advanced_count: int
    skipped_already_at_max: int
    skipped_no_semester_set: int
    skipped_no_program: int
    skipped_open_enrollments: int = 0
    skipped_overdue_fees: int = 0
    skipped_failed_courses: int = 0
    skipped_low_cgpa: int = 0
    skipped_unpaid_fines: int = 0
    min_cgpa: float | None = None
    max_courses_to_clear: int | None = None
    dry_run: bool = False
    # First 200 held-back students with the reason, so nobody has to guess.
    skipped_students: list[SkippedStudent] = []


class GuardianInfo(BaseModel):
    name: str
    cnic: str
    contact_no: str | None
    relation: str | None


class EmergencyContactInfo(BaseModel):
    name: str | None
    contact_no: str
    relation: str | None


class EnrollmentHistoryEntry(BaseModel):
    course_code: str
    course_title: str
    credit_hours: int
    term_label: str
    status: str
    outcome: str | None
    is_retake: bool


class FeeSummary(BaseModel):
    total_assignments: int
    total_due: float
    total_paid: float
    total_outstanding: float


class LibrarySummary(BaseModel):
    active_loans: int
    overdue_loans: int
    past_loans: int


class FineSummary(BaseModel):
    total_pending: float
    total_paid: float
    total_waived: float


class ComplaintSummary(BaseModel):
    filed_by_them: int
    filed_against_them: int


class StudentFullProfileResponse(BaseModel):
    """The 'everything about this student in one place' view — personal
    details, guardian/emergency contacts, qualifications, full academic
    enrollment history, and summaries from Fees/Library/Fines/Complaints.
    Each module's own dedicated page still has the full deep-dive detail
    (e.g. every ledger entry, every book ever borrowed) — this is the
    at-a-glance aggregate a real SIS profile page shows first."""

    # Core identity/personal
    id: uuid.UUID
    first_name: str
    last_name: str
    roll_number: str | None
    cnic: str
    contact_no: str
    date_of_birth: date
    gender: str
    marital_status: str
    religion: str
    nationality: str
    status: StudentStatus
    photo_original_name: str | None
    has_photo: bool
    certificate_original_name: str | None
    has_certificate: bool

    # Academic placement
    program_id: uuid.UUID | None
    program_name: str | None
    current_semester: int | None
    enrollment_date: date | None
    email: str | None = None
    blood_group: str | None = None
    admission_session: str | None = None
    admission_category: str | None = None
    section_label: str | None = None
    admission_fee_paid: bool | None

    # Address
    present_address: str | None
    province: str | None
    district: str | None
    city: str | None

    # Family/emergency
    guardians: list[GuardianInfo]
    emergency_contacts: list[EmergencyContactInfo]

    # Academic history
    qualifications: list[QualificationOut]
    enrollments: list[EnrollmentHistoryEntry]

    # Cross-module summaries
    fees: FeeSummary
    library: LibrarySummary
    fines: FineSummary
    complaints: ComplaintSummary


class IdentityCorrectionRequest(BaseModel):
    """Correct a mistyped CNIC or the guardian's details. A reason is required and kept in the audit log."""
    student_cnic: str | None = None
    guardian_name: str | None = Field(default=None, min_length=2, max_length=150)
    guardian_cnic: str | None = None
    guardian_relation: str | None = Field(default=None, min_length=2, max_length=50)
    guardian_contact_no: str | None = None
    reason: str = Field(min_length=10, max_length=300)

    @field_validator("student_cnic", "guardian_cnic")
    @classmethod
    def _cnic(cls, v: str | None) -> str | None:
        return None if v is None else _normalize_cnic(v)

    @field_validator("guardian_contact_no")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return None if v is None else _normalize_phone(v)