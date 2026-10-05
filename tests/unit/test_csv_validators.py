from __future__ import annotations

from app.imports.validators import (
    REQUIRED_HEADERS,
    normalize_cnic,
    normalize_phone,
    validate_and_normalize_row,
    validate_headers,
)

VALID_ROW = {
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
    "guardian_contact_no": "03007654321",
    "relation_with_guardian": "Father",
    "emergency_contact_no": "03009998888",
    "present_address": "House 12",
    "province": "Punjab",
    "district": "Lahore",
    "city": "Lahore",
    "program": "BSN",
    "roll_number": "BSN-001",
    "admission_fee_paid": "Yes",
    "enrollment_date": "2026-08-01",
}


def test_valid_row_passes():
    result = validate_and_normalize_row(2, VALID_ROW)
    assert result.is_valid, result.errors
    assert result.normalized["cnic"] == "12345-1234567-1"
    assert result.normalized["gender"] == "Female"


def test_missing_required_field():
    row = dict(VALID_ROW)
    row["first_name"] = ""
    result = validate_and_normalize_row(2, row)
    assert not result.is_valid
    assert any(e.field == "first_name" and e.error_type == "REQUIRED_FIELD_MISSING" for e in result.errors)


def test_invalid_cnic_format():
    row = dict(VALID_ROW)
    row["cnic"] = "not-a-cnic"
    result = validate_and_normalize_row(2, row)
    assert not result.is_valid
    assert any(e.field == "cnic" and e.error_type == "INVALID_FORMAT" for e in result.errors)


def test_cnic_normalization_strips_and_reformats():
    assert normalize_cnic("1234512345671") == "12345-1234567-1"
    assert normalize_cnic("12345-1234567-1") == "12345-1234567-1"


def test_invalid_gender_value():
    row = dict(VALID_ROW)
    row["gender"] = "Unknown"
    result = validate_and_normalize_row(2, row)
    assert not result.is_valid
    assert any(e.field == "gender" for e in result.errors)


def test_gender_case_insensitive_normalization():
    row = dict(VALID_ROW)
    row["gender"] = "female"
    result = validate_and_normalize_row(2, row)
    assert result.is_valid
    assert result.normalized["gender"] == "Female"


def test_invalid_phone_format():
    row = dict(VALID_ROW)
    row["contact_no"] = "12345"
    result = validate_and_normalize_row(2, row)
    assert not result.is_valid
    assert any(e.field == "contact_no" for e in result.errors)


def test_phone_normalization():
    assert normalize_phone("0300 123 4567") == "03001234567"
    assert normalize_phone("+92 300 1234567") == "+923001234567"


def test_future_date_of_birth_rejected():
    row = dict(VALID_ROW)
    row["date_of_birth"] = "2099-01-01"
    result = validate_and_normalize_row(2, row)
    assert not result.is_valid
    assert any(e.field == "date_of_birth" for e in result.errors)


def test_alternate_date_formats_accepted():
    row = dict(VALID_ROW)
    row["date_of_birth"] = "11/05/2004"
    result = validate_and_normalize_row(2, row)
    assert result.is_valid


def test_optional_fields_omittable():
    row = dict(VALID_ROW)
    row["program"] = ""
    row["roll_number"] = ""
    row["admission_fee_paid"] = ""
    row["enrollment_date"] = ""
    row["guardian_contact_no"] = ""
    result = validate_and_normalize_row(2, row)
    assert result.is_valid, result.errors
    assert result.normalized["program"] is None
    assert result.normalized["roll_number"] is None


def test_roll_number_column_is_ignored_even_when_malformed():
    """roll_number is deliberately no longer accepted from the CSV — it's
    always system-generated at import time (see the '_TEXT_FORCED_COLUMNS'
    comment in app/imports/validators.py). A garbage value in that column
    must not fail the row; it's discarded like any other retired column,
    same as the already-covered case above where the column is blank."""
    row = dict(VALID_ROW)
    row["roll_number"] = "bad roll #"
    result = validate_and_normalize_row(2, row)
    assert result.is_valid, result.errors
    assert result.normalized["roll_number"] is None


def test_invalid_admission_fee_value():
    row = dict(VALID_ROW)
    row["admission_fee_paid"] = "maybe"
    result = validate_and_normalize_row(2, row)
    assert not result.is_valid
    assert any(e.field == "admission_fee_paid" for e in result.errors)


def test_missing_headers_detected():
    headers = [h for h in REQUIRED_HEADERS if h not in ("cnic", "first_name")]
    errors = validate_headers(headers)
    fields = {e.field for e in errors}
    assert "cnic" in fields
    assert "first_name" in fields


def test_unexpected_header_detected():
    headers = list(REQUIRED_HEADERS) + ["some_random_column"]
    errors = validate_headers(headers)
    assert any(e.field == "some_random_column" and e.error_type == "UNEXPECTED_HEADER" for e in errors)


def test_valid_headers_pass():
    errors = validate_headers(list(REQUIRED_HEADERS))
    assert errors == []