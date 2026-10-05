from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.imports.models import ImportJobStatus
from app.imports.service import ImportService
from app.programs.models import Program
from app.students.models import Student

pytestmark = pytest.mark.asyncio

CSV_HEADER = (
    "first_name,last_name,date_of_birth,gender,cnic,contact_no,marital_status,religion,nationality,"
    "guardian_name,guardian_cnic,guardian_contact_no,relation_with_guardian,emergency_contact_no,"
    "present_address,province,district,city,program,roll_number,admission_fee_paid,enrollment_date\n"
)


def _row(cnic_suffix: str, roll: str, program: str = "BSN") -> str:
    return (
        f"Ayesha,Khan,2004-05-11,Female,12345-123456{cnic_suffix}-1,03001234567,Single,Islam,Pakistani,"
        f"Muhammad Khan,12345-7654321-1,03007654321,Father,03009998888,House 12,Punjab,Lahore,Lahore,"
        f"{program},{roll},Yes,2026-08-01\n"
    )


async def _make_program(db_session) -> Program:
    program = Program(code="BSN", name="BS Nursing", duration_years=4, total_semesters=8)
    db_session.add(program)
    await db_session.commit()
    return program


async def test_valid_csv_imports_successfully(db_session, admin_user):
    await _make_program(db_session)
    csv_bytes = (CSV_HEADER + _row("7", "BSN-100")).encode("utf-8")

    service = ImportService(db_session)
    job = await service.create_job_from_upload(file_bytes=csv_bytes, filename="students.csv", uploaded_by=admin_user.id)
    await db_session.commit()

    job = await service.process_job(job.id, csv_bytes)
    await db_session.commit()

    assert job.status == ImportJobStatus.COMPLETED.value
    assert job.imported_rows == 1
    assert job.invalid_rows == 0

    student = (await db_session.execute(select(Student))).scalar_one()
    # roll_number is deliberately no longer read from the CSV — it's
    # always system-generated (see app/core/sequences.py::generate_roll_number
    # and the matching fix in tests/unit/test_csv_validators.py). The CSV's
    # "BSN-100" value in the roll_number column is expected to be ignored,
    # not reproduced.
    assert student.roll_number == "26001"
    assert student.user_id is not None


async def test_invalid_row_rejects_entire_file(db_session, admin_user):
    await _make_program(db_session)
    bad_row = _row("8", "BSN-101").replace("12345-1234568-1", "not-a-cnic")
    good_row = _row("9", "BSN-102")
    csv_bytes = (CSV_HEADER + bad_row + good_row).encode("utf-8")

    service = ImportService(db_session)
    job = await service.create_job_from_upload(file_bytes=csv_bytes, filename="mixed.csv", uploaded_by=admin_user.id)
    await db_session.commit()

    job = await service.process_job(job.id, csv_bytes)
    await db_session.commit()

    assert job.status == ImportJobStatus.FAILED.value
    assert job.imported_rows == 0

    count = (await db_session.execute(select(Student))).scalars().all()
    assert len(count) == 0  # nothing imported — whole-file rejection policy


async def test_duplicate_cnic_within_file_rejected(db_session, admin_user):
    await _make_program(db_session)
    row = _row("7", "BSN-100")
    csv_bytes = (CSV_HEADER + row + row).encode("utf-8")  # exact same CNIC twice

    service = ImportService(db_session)
    job = await service.create_job_from_upload(file_bytes=csv_bytes, filename="dupe.csv", uploaded_by=admin_user.id)
    await db_session.commit()
    job = await service.process_job(job.id, csv_bytes)
    await db_session.commit()

    assert job.status == ImportJobStatus.FAILED.value
    errors = await service.repo.list_row_errors(job.id, offset=0, limit=50)
    error_types = {e.error_type for e in errors[0]}
    assert "DUPLICATE_IN_FILE" in error_types


async def test_duplicate_cnic_against_existing_student_rejected(db_session, admin_user):
    await _make_program(db_session)
    row = _row("7", "BSN-100")
    csv_bytes = (CSV_HEADER + row).encode("utf-8")

    service = ImportService(db_session)
    job = await service.create_job_from_upload(file_bytes=csv_bytes, filename="first.csv", uploaded_by=admin_user.id)
    await db_session.commit()
    await service.process_job(job.id, csv_bytes)
    await db_session.commit()

    # Second file, different checksum (different roll number), same CNIC.
    row2 = _row("7", "BSN-999")
    csv_bytes2 = (CSV_HEADER + row2).encode("utf-8")
    job2 = await service.create_job_from_upload(file_bytes=csv_bytes2, filename="second.csv", uploaded_by=admin_user.id)
    await db_session.commit()
    job2 = await service.process_job(job2.id, csv_bytes2)
    await db_session.commit()

    assert job2.status == ImportJobStatus.FAILED.value
    errors = await service.repo.list_row_errors(job2.id, offset=0, limit=50)
    error_types = {e.error_type for e in errors[0]}
    assert "DUPLICATE_EXISTING" in error_types


async def test_identical_file_reupload_is_idempotent(db_session, admin_user):
    await _make_program(db_session)
    csv_bytes = (CSV_HEADER + _row("7", "BSN-100")).encode("utf-8")

    service = ImportService(db_session)
    await service.create_job_from_upload(file_bytes=csv_bytes, filename="a.csv", uploaded_by=admin_user.id)
    await db_session.commit()

    from app.core.exceptions import DuplicateResourceError

    with pytest.raises(DuplicateResourceError):
        await service.create_job_from_upload(file_bytes=csv_bytes, filename="a_again.csv", uploaded_by=admin_user.id)


async def test_missing_headers_fail_job_without_importing(db_session, admin_user):
    await _make_program(db_session)
    broken_header = CSV_HEADER.replace("cnic,", "")  # drop the cnic column entirely
    csv_bytes = (broken_header + _row("7", "BSN-100").replace("12345-1234567-1,", "")).encode("utf-8")

    service = ImportService(db_session)
    job = await service.create_job_from_upload(file_bytes=csv_bytes, filename="badheaders.csv", uploaded_by=admin_user.id)
    await db_session.commit()
    job = await service.process_job(job.id, csv_bytes)
    await db_session.commit()

    assert job.status == ImportJobStatus.FAILED.value
    assert job.imported_rows == 0


async def test_program_name_uniqueness_enforced_at_db_level(db_session, admin_user):
    """The 'ambiguous program name' branch in the importer (Section 9) is a
    defense-in-depth path for data that predates or bypasses the ORM layer
    (e.g. direct SQL, a future bulk-program-import feature). Under normal
    operation it can never actually fire, because program creation itself
    enforces case-insensitive name uniqueness at the database level — this
    test proves that invariant holds, which is what actually keeps imports
    unambiguous in practice."""
    from app.core.exceptions import DuplicateResourceError
    from app.programs.schemas import ProgramCreate
    from app.programs.service import ProgramService

    service = ProgramService(db_session)
    await service.create_program(
        ProgramCreate(code="BSN", name="BS Nursing", duration_years=4, total_semesters=8), actor_id=admin_user.id
    )
    with pytest.raises(DuplicateResourceError):
        await service.create_program(
            ProgramCreate(code="BSN2", name="BS NURSING", duration_years=4, total_semesters=8),
            actor_id=admin_user.id,
        )