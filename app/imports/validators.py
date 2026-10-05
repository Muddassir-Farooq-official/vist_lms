"""Pure validation/normalization functions for the student CSV import.

Deliberately free of any DB/IO so it's cheaply unit-testable (Section 40).
Cross-row concerns (duplicate CNIC within the same file, existing DB
records, program lookups) are handled one layer up in service.py, which has
access to the session and the full row set.
"""
from __future__ import annotations

from app.core.clock import institute_today

import re
from dataclasses import dataclass, field
from datetime import date, datetime

# Column order and names are the CSV import contract. Changing this is a
# breaking change for every admin who has a saved import template.
REQUIRED_HEADERS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "date_of_birth",
    "gender",
    "cnic",
    "contact_no",
    "marital_status",
    "religion",
    "nationality",
    "guardian_name",
    "guardian_cnic",
    "relation_with_guardian",
    "emergency_contact_no",
    "present_address",
    "province",
    "district",
    "city",
)

OPTIONAL_HEADERS: tuple[str, ...] = (
    "guardian_contact_no",
    "program",
    "roll_number",
    "admission_fee_paid",
    "enrollment_date",
    "current_semester",
    "email",
    "blood_group",
    "admission_session",
    "admission_category",
    "section",
)

ALL_KNOWN_HEADERS = set(REQUIRED_HEADERS) | set(OPTIONAL_HEADERS)

VALID_GENDERS = {"Male", "Female", "Other"}
VALID_MARITAL_STATUS = {"Single", "Married", "Divorced", "Widowed"}

# Pakistani CNIC: 13 digits, optionally dashed as 5-7-1.
_CNIC_RE = re.compile(r"^\d{5}-?\d{7}-?\d{1}$")
# Pakistani mobile: 03XXXXXXXXX (11 digits) or +923XXXXXXXXX.
_PHONE_RE = re.compile(r"^(03\d{9}|\+923\d{9})$")
_ROLL_NUMBER_RE = re.compile(r"^[A-Za-z0-9-]{2,30}$")

# Public aliases — reused by app/students/schemas.py so the manual "Add
# Student" form enforces exactly the same format rules as CSV import,
# rather than a second hand-copied regex that could quietly drift.
CNIC_RE = _CNIC_RE
PHONE_RE = _PHONE_RE
ROLL_NUMBER_RE = _ROLL_NUMBER_RE


@dataclass
class FieldError:
    field: str
    error_type: str
    message: str


@dataclass
class RowValidationResult:
    row_number: int
    normalized: dict = field(default_factory=dict)
    errors: list[FieldError] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _norm_str(value: str | None) -> str:
    return (value or "").strip()


def _collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_cnic(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 13:
        return f"{digits[0:5]}-{digits[5:12]}-{digits[12]}"
    return value.strip()


def normalize_phone(value: str) -> str:
    """Cleans a phone number and corrects the single most common
    real-world corruption source: Excel silently stripping a leading zero
    from a numeric-looking CSV cell (03001234567 -> 3001234567) when
    someone opens the downloaded template, types a number, and saves.

    If the cleaned value is exactly 10 digits and starts with '3' (every
    Pakistani mobile prefix), a leading zero is restored before the
    regex check runs. This is the correct place to fix this — NOT the
    template file itself, since forcing Excel to preserve a leading zero
    means writing a formula-looking cell value (e.g. ="03001234567"),
    which is exactly the CSV-formula-injection pattern this system
    otherwise sanitizes against (Section 24). Fixing the parser is safe;
    fixing the file would trade one bug for a worse one."""
    digits = re.sub(r"[^\d+]", "", value)
    if not digits.startswith("+") and len(digits) == 10 and digits.startswith("3"):
        digits = "0" + digits
    return digits


def validate_headers(headers: list[str]) -> list[FieldError]:
    errors: list[FieldError] = []
    header_set = set(headers)

    missing = set(REQUIRED_HEADERS) - header_set
    for h in sorted(missing):
        errors.append(FieldError(field=h, error_type="MISSING_HEADER", message=f"Required column '{h}' is missing."))

    unexpected = header_set - ALL_KNOWN_HEADERS
    for h in sorted(unexpected):
        errors.append(
            FieldError(field=h, error_type="UNEXPECTED_HEADER", message=f"Column '{h}' is not a recognized field.")
        )

    return errors


def validate_and_normalize_row(row_number: int, raw_row: dict[str, str]) -> RowValidationResult:
    result = RowValidationResult(row_number=row_number, raw=dict(raw_row))
    n = result.normalized
    errs = result.errors

    def require(field_name: str, display: str | None = None) -> str:
        val = _norm_str(raw_row.get(field_name))
        if not val:
            errs.append(
                FieldError(field=field_name, error_type="REQUIRED_FIELD_MISSING", message=f"{display or field_name} is required.")
            )
        return val

    # --- Personal ---
    first_name = require("first_name", "First name")
    last_name = require("last_name", "Last name")
    n["first_name"] = _collapse_spaces(first_name)
    n["last_name"] = _collapse_spaces(last_name)

    dob_raw = require("date_of_birth", "Date of birth")
    if dob_raw:
        parsed_dob = _parse_date(dob_raw)
        if parsed_dob is None:
            errs.append(
                FieldError(
                    field="date_of_birth",
                    error_type="INVALID_FORMAT",
                    message="Date of birth must be in YYYY-MM-DD format.",
                )
            )
        elif parsed_dob >= institute_today():
            errs.append(
                FieldError(field="date_of_birth", error_type="INVALID_VALUE", message="Date of birth must be in the past.")
            )
        else:
            n["date_of_birth"] = parsed_dob

    gender_raw = require("gender", "Gender")
    if gender_raw:
        gender_normalized = gender_raw.strip().capitalize()
        if gender_normalized not in VALID_GENDERS:
            errs.append(
                FieldError(
                    field="gender",
                    error_type="INVALID_VALUE",
                    message=f"Gender must be one of: {', '.join(sorted(VALID_GENDERS))}.",
                )
            )
        else:
            n["gender"] = gender_normalized

    cnic_raw = require("cnic", "Student CNIC")
    if cnic_raw:
        if not _CNIC_RE.match(cnic_raw.strip()):
            errs.append(
                FieldError(field="cnic", error_type="INVALID_FORMAT", message="CNIC must be 13 digits (formatted as 12345-1234567-1).")
            )
        else:
            n["cnic"] = normalize_cnic(cnic_raw)

    contact_raw = require("contact_no", "Student contact number")
    if contact_raw:
        normalized_contact = normalize_phone(contact_raw)
        if not _PHONE_RE.match(normalized_contact):
            errs.append(
                FieldError(
                    field="contact_no",
                    error_type="INVALID_FORMAT",
                    message="Contact number must be a valid Pakistani mobile number (03XXXXXXXXX or +923XXXXXXXXX).",
                )
            )
        else:
            n["contact_no"] = normalized_contact

    marital_raw = require("marital_status", "Marital status")
    if marital_raw:
        marital_normalized = marital_raw.strip().capitalize()
        if marital_normalized not in VALID_MARITAL_STATUS:
            errs.append(
                FieldError(
                    field="marital_status",
                    error_type="INVALID_VALUE",
                    message=f"Marital status must be one of: {', '.join(sorted(VALID_MARITAL_STATUS))}.",
                )
            )
        else:
            n["marital_status"] = marital_normalized

    n["religion"] = _collapse_spaces(require("religion", "Religion"))
    n["nationality"] = _collapse_spaces(require("nationality", "Nationality"))

    # --- Guardian / emergency ---
    n["guardian_name"] = _collapse_spaces(require("guardian_name", "Father/Guardian name"))

    guardian_cnic_raw = require("guardian_cnic", "Father/Guardian CNIC")
    if guardian_cnic_raw:
        if not _CNIC_RE.match(guardian_cnic_raw.strip()):
            errs.append(
                FieldError(
                    field="guardian_cnic", error_type="INVALID_FORMAT", message="Guardian CNIC must be 13 digits."
                )
            )
        else:
            n["guardian_cnic"] = normalize_cnic(guardian_cnic_raw)

    guardian_contact_raw = _norm_str(raw_row.get("guardian_contact_no"))
    if guardian_contact_raw:
        normalized_gc = normalize_phone(guardian_contact_raw)
        if not _PHONE_RE.match(normalized_gc):
            errs.append(
                FieldError(
                    field="guardian_contact_no",
                    error_type="INVALID_FORMAT",
                    message="Guardian contact number must be a valid Pakistani mobile number.",
                )
            )
        else:
            n["guardian_contact_no"] = normalized_gc
    else:
        n["guardian_contact_no"] = None

    n["relation_with_guardian"] = _collapse_spaces(require("relation_with_guardian", "Relation with guardian"))

    emergency_raw = require("emergency_contact_no", "Emergency contact number")
    if emergency_raw:
        normalized_ec = normalize_phone(emergency_raw)
        if not _PHONE_RE.match(normalized_ec):
            errs.append(
                FieldError(
                    field="emergency_contact_no",
                    error_type="INVALID_FORMAT",
                    message="Emergency contact number must be a valid Pakistani mobile number.",
                )
            )
        else:
            n["emergency_contact_no"] = normalized_ec

    # --- Address ---
    n["present_address"] = _collapse_spaces(require("present_address", "Present address"))
    n["province"] = _collapse_spaces(require("province", "Province"))
    n["district"] = _collapse_spaces(require("district", "District"))
    n["city"] = _collapse_spaces(require("city", "City"))

    # --- Academic (optional) ---
    program_raw = _norm_str(raw_row.get("program"))
    n["program"] = program_raw or None

    # roll_number is no longer accepted from the CSV — it's always
    # system-generated (sequential per program/year, see
    # app/core/sequences.py) since it now doubles as the student's
    # permanent login username. The column is left in OPTIONAL_HEADERS
    # so an old template that still includes it doesn't hard-fail header
    # validation; its value, if present, is simply ignored.
    n["roll_number"] = None

    # --- Newer optional columns ---
    sem_raw = _norm_str(raw_row.get("current_semester"))
    n["current_semester"] = None
    if sem_raw:
        if sem_raw.isdigit() and 1 <= int(sem_raw) <= 20:
            n["current_semester"] = int(sem_raw)
        else:
            errs.append(
                FieldError(
                    field="current_semester",
                    error_type="INVALID_VALUE",
                    message="Current semester must be a whole number between 1 and 20.",
                )
            )

    email_raw = _norm_str(raw_row.get("email"))
    n["email"] = None
    if email_raw:
        e = email_raw.strip().lower()
        if "@" not in e or " " in e or "." not in e.split("@")[-1] or len(e) > 255:
            errs.append(FieldError(field="email", error_type="INVALID_FORMAT", message="Email is not a valid address."))
        else:
            n["email"] = e

    bg_raw = _norm_str(raw_row.get("blood_group"))
    n["blood_group"] = None
    if bg_raw:
        bg = bg_raw.strip().upper().replace(" ", "")
        if bg not in {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}:
            errs.append(
                FieldError(
                    field="blood_group",
                    error_type="INVALID_VALUE",
                    message="Blood group must be one of A+, A-, B+, B-, AB+, AB-, O+, O-.",
                )
            )
        else:
            n["blood_group"] = bg

    n["admission_session"] = (_norm_str(raw_row.get("admission_session")) or "").strip()[:50] or None
    n["admission_category"] = (_norm_str(raw_row.get("admission_category")) or "").strip()[:50] or None
    n["section_label"] = " ".join((_norm_str(raw_row.get("section")) or "").split())[:30] or None

    fee_paid_raw = _norm_str(raw_row.get("admission_fee_paid"))
    if fee_paid_raw:
        lowered = fee_paid_raw.strip().lower()
        if lowered in {"yes", "true", "1"}:
            n["admission_fee_paid"] = True
        elif lowered in {"no", "false", "0"}:
            n["admission_fee_paid"] = False
        else:
            errs.append(
                FieldError(
                    field="admission_fee_paid",
                    error_type="INVALID_VALUE",
                    message="Admission fee paid must be Yes/No.",
                )
            )
    else:
        n["admission_fee_paid"] = None

    enrollment_raw = _norm_str(raw_row.get("enrollment_date"))
    if enrollment_raw:
        parsed_enroll = _parse_date(enrollment_raw)
        if parsed_enroll is None:
            errs.append(
                FieldError(
                    field="enrollment_date", error_type="INVALID_FORMAT", message="Enrollment date must be YYYY-MM-DD."
                )
            )
        else:
            n["enrollment_date"] = parsed_enroll
    else:
        n["enrollment_date"] = None

    return result


def _parse_date(value: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def generate_template_csv() -> str:
    """Builds the downloadable CSV template directly from REQUIRED_HEADERS /
    OPTIONAL_HEADERS, so it can never silently drift out of sync with the
    actual validation rules above."""
    import csv
    import io

    headers = _template_headers()
    example_row = _template_example_row()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerow({h: example_row.get(h, "") for h in headers})
    return buffer.getvalue()


def _template_headers() -> list[str]:
    return list(REQUIRED_HEADERS) + list(OPTIONAL_HEADERS)


def _template_example_row() -> dict[str, str]:
    return {
        "first_name": "Ayesha",
        "last_name": "Khan",
        "date_of_birth": "2004-05-11",
        "gender": "Female",
        "cnic": "12345-1234567-1",
        "contact_no": "03001234567",
        "marital_status": "Single",
        "religion": "Islam",
        "nationality": "Pakistani",
        "guardian_name": "Muhammad Khan",
        "guardian_cnic": "12345-7654321-1",
        "relation_with_guardian": "Father",
        "emergency_contact_no": "03009998888",
        "present_address": "House 12, Street 5",
        "province": "Punjab",
        "district": "Lahore",
        "city": "Lahore",
        "guardian_contact_no": "03007654321",
        "program": "BSN",
        "roll_number": "",  # no longer used — roll number is always system-generated now
        "admission_fee_paid": "Yes",
        "enrollment_date": "2026-08-01",
    }


# Columns that must be forced to Text format in the XLSX template — these
# are the ones Excel silently mangles if left as "General": phone numbers
# and CNICs lose their leading zero / get corrupted by Excel treating a
# long digit string as a number. roll_number stays listed here too even
# though its value is now ignored, purely so an old template column
# doesn't render as a mangled number if someone pastes stale data into it.
_TEXT_FORCED_COLUMNS = {"cnic", "contact_no", "guardian_cnic", "guardian_contact_no", "emergency_contact_no", "roll_number"}


def generate_template_xlsx() -> bytes:
    """Builds the downloadable XLSX template with phone/CNIC columns
    pre-formatted as Text (number_format '@') for a large row range —
    not just the demo row. This is what actually fixes Excel corrupting
    admin-entered data: a CSV file has no cell-formatting metadata at all,
    so a plain .csv template can only protect the one example row it ships
    with (or not even that, without a formula-injection-risk trick). An
    .xlsx column pre-set to Text persists that formatting for every row
    the admin types afterward, and survives Excel's own 'Save As CSV'
    step before upload, because the underlying stored value is already a
    literal string, not a formatted number."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    headers = _template_headers()
    example_row = _template_example_row()

    wb = Workbook()
    ws = wb.active
    ws.title = "Students"

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = Font(bold=True)

    for col_idx, header in enumerate(headers, start=1):
        ws.cell(row=2, column=col_idx, value=example_row.get(header, ""))

    # Pre-format the sensitive columns as Text for rows 2 through 1000 —
    # generous headroom for a real import batch, applied BEFORE any admin
    # data is typed in, which is the only way to reliably prevent Excel
    # from reinterpreting these values as numbers.
    for col_idx, header in enumerate(headers, start=1):
        if header in _TEXT_FORCED_COLUMNS:
            col_letter = get_column_letter(col_idx)
            for row_idx in range(2, 1001):
                ws.cell(row=row_idx, column=col_idx).number_format = "@"
            ws.column_dimensions[col_letter].width = 18

    import io

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
    writer.writerow({h: example_row.get(h, "") for h in headers})
    return buffer.getvalue()