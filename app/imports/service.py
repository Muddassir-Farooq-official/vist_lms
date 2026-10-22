"""Student CSV import orchestration.

Policy in force (approved): if ANY row fails validation, the ENTIRE file is
rejected — nothing is imported, and the full error report is returned so
the admin can fix the source file and re-upload. This keeps `import_jobs`
lifecycle simple: UPLOADED -> VALIDATING -> (VALIDATED -> IMPORTING ->
COMPLETED) | FAILED.

Idempotency: the SHA-256 checksum of the uploaded file has a DB unique
constraint (`import_jobs.file_checksum`), so re-uploading an identical file
is rejected before any parsing happens. The `students.cnic` and
`students.roll_number` unique constraints are the final floor in case two
*different* files describe the same person.
"""
from __future__ import annotations

from app.core.clock import institute_today

import csv
import hashlib
import io
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.core.config import settings
from app.core.security import new_temp_password
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ValidationAppError
from app.core.security import hash_password
from app.core.sequences import generate_roll_number
from app.imports.models import ImportJob, ImportJobStatus, ImportRowError
from app.imports.repository import ImportRepository
from app.imports.validators import FieldError, RowValidationResult, validate_and_normalize_row, validate_headers
from app.programs.models import Program
from app.students.models import Student, StudentAddress, StudentEmergencyContact, StudentGuardian, StudentStatus
from app.students.repository import StudentRepository

MAX_FILE_SIZE_BYTES = settings.import_max_file_size_mb * 1024 * 1024


class ImportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ImportRepository(session)
        self.student_repo = StudentRepository(session)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------
    # Upload / idempotency
    # ------------------------------------------------------------------
    async def create_job_from_upload(
        self, *, file_bytes: bytes, filename: str, uploaded_by: uuid.UUID
    ) -> ImportJob:
        if len(file_bytes) == 0:
            raise ValidationAppError("Uploaded file is empty.")
        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            raise ValidationAppError(
                f"File exceeds the maximum allowed size of {settings.import_max_file_size_mb} MB."
            )
        if not filename.lower().endswith(".csv"):
            raise ValidationAppError("Only .csv files are accepted.")

        checksum = hashlib.sha256(file_bytes).hexdigest()
        existing = await self.repo.get_by_checksum(checksum)
        if existing is not None:
            raise DuplicateResourceError(
                "This exact file has already been uploaded "
                f"(import job {existing.id}, status={existing.status}). "
                "Re-upload only if the content has changed."
            )

        job = ImportJob(
            uploaded_by=uploaded_by,
            filename=filename,
            file_checksum=checksum,
            status=ImportJobStatus.UPLOADED.value,
        )
        await self.repo.create(job)
        await self.audit.log(
            actor_user_id=uploaded_by,
            action="import.uploaded",
            entity_type="import_job",
            entity_id=job.id,
            metadata={"filename": filename, "size_bytes": len(file_bytes)},
        )
        return job

    # ------------------------------------------------------------------
    # Processing (invoked as a background task by the router; the function
    # itself has no FastAPI dependency and can be lifted into an RQ worker
    # unchanged — it only needs an AsyncSession and the raw file bytes)
    # ------------------------------------------------------------------
    async def process_job(self, job_id: uuid.UUID, file_bytes: bytes) -> ImportJob:
        job = await self.repo.get_by_id(job_id)
        if job is None:
            raise ValidationAppError("Import job not found.")

        started = time.monotonic()
        job.status = ImportJobStatus.VALIDATING.value
        job.started_at = datetime.now(timezone.utc)
        await self.session.flush()

        try:
            text = self._decode(file_bytes)
        except UnicodeDecodeError:
            await self._fail(job, "File encoding is invalid — expected UTF-8.")
            return job

        try:
            rows, header_errors = self._parse_csv(text)
        except csv.Error as exc:
            await self._fail(job, f"Malformed CSV: {exc}")
            return job

        job.total_rows = len(rows)

        if header_errors:
            await self._record_row_errors(job, row_number=0, errors=header_errors, raw_row={})
            job.invalid_rows = 1
            await self._fail(job, "CSV headers do not match the expected template.")
            return job

        results = [validate_and_normalize_row(i + 2, row) for i, row in enumerate(rows)]  # +2: header is row 1

        # Cross-row + DB validation (duplicates, program resolution) only
        # for rows that are otherwise clean — no point double-reporting.
        await self._cross_validate(results)

        valid_results = [r for r in results if r.is_valid]
        invalid_results = [r for r in results if not r.is_valid]

        job.valid_rows = len(valid_results)
        job.invalid_rows = len(invalid_results)

        if invalid_results:
            for r in invalid_results:
                await self._record_row_errors(job, row_number=r.row_number, errors=r.errors, raw_row=r.raw)
            await self._fail(job, f"{len(invalid_results)} row(s) failed validation. No rows were imported.")
            return job

        job.status = ImportJobStatus.VALIDATED.value
        await self.session.flush()

        job.status = ImportJobStatus.IMPORTING.value
        await self.session.flush()

        imported = 0
        for r in valid_results:
            await self._import_row(r, job=job)
            imported += 1

        job.imported_rows = imported
        job.status = ImportJobStatus.COMPLETED.value
        job.finished_at = datetime.now(timezone.utc)
        job.processing_duration_ms = int((time.monotonic() - started) * 1000)

        await self.audit.log(
            actor_user_id=job.uploaded_by,
            action="import.completed",
            entity_type="import_job",
            entity_id=job.id,
            metadata={"imported_rows": imported, "total_rows": job.total_rows},
        )
        return job

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _decode(file_bytes: bytes) -> str:
        return file_bytes.decode("utf-8-sig")  # tolerates a BOM from Excel exports

    @staticmethod
    def _parse_csv(text: str) -> tuple[list[dict[str, str]], list[FieldError]]:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            return [], [FieldError(field="__file__", error_type="MALFORMED_CSV", message="CSV has no header row.")]

        headers = [h.strip() for h in reader.fieldnames]
        header_errors = validate_headers(headers)

        rows: list[dict[str, str]] = []
        for raw in reader:
            # Skip fully empty rows (Section 6: "empty rows") rather than
            # treating them as validation failures.
            if raw is None or all(not (v or "").strip() for v in raw.values()):
                continue
            rows.append({k.strip(): (v or "") for k, v in raw.items() if k is not None})

        return rows, header_errors

    async def _cross_validate(self, results: list[RowValidationResult]) -> None:
        seen_cnics: dict[str, int] = {}

        candidate_cnics = {r.normalized.get("cnic") for r in results if r.normalized.get("cnic")}

        existing_cnics: set[str] = set()
        if candidate_cnics:
            stmt = select(Student.cnic).where(Student.cnic.in_(candidate_cnics))
            existing_cnics = set((await self.session.execute(stmt)).scalars().all())

        programs_by_code: dict[str, Program] = {}
        programs_all = (await self.session.execute(select(Program))).scalars().all()
        for p in programs_all:
            programs_by_code[p.code.upper()] = p

        for r in results:
            cnic = r.normalized.get("cnic")
            if cnic:
                if cnic in existing_cnics:
                    r.errors.append(FieldError("cnic", "DUPLICATE_EXISTING", "CNIC already exists in LMS."))
                elif cnic in seen_cnics:
                    r.errors.append(
                        FieldError(
                            "cnic",
                            "DUPLICATE_IN_FILE",
                            f"CNIC duplicated within this file (first seen at row {seen_cnics[cnic]}).",
                        )
                    )
                else:
                    seen_cnics[cnic] = r.row_number

            program_value = r.normalized.get("program")
            if program_value:
                matched = programs_by_code.get(program_value.strip().upper())
                if matched is None:
                    name_matches = [
                        p for p in programs_all if p.name.strip().lower() == program_value.strip().lower()
                    ]
                    if len(name_matches) == 1:
                        matched = name_matches[0]
                    elif len(name_matches) > 1:
                        r.errors.append(
                            FieldError(
                                "program",
                                "AMBIGUOUS_PROGRAM",
                                f"'{program_value}' matches multiple programs; use the exact program code instead.",
                            )
                        )
                if matched is None and not any(e.field == "program" for e in r.errors):
                    r.errors.append(
                        FieldError("program", "PROGRAM_NOT_FOUND", f"No program found matching '{program_value}'.")
                    )
                if matched is not None:
                    r.normalized["_program_id"] = matched.id
                    r.normalized["_program_code"] = matched.code
                    sem = r.normalized.get("current_semester")
                    if sem and sem > matched.total_semesters:
                        r.errors.append(
                            FieldError(
                                "current_semester",
                                "INVALID_VALUE",
                                f"Semester {sem} exceeds {matched.code}'s {matched.total_semesters} total semesters.",
                            )
                        )

    async def _import_row(self, r: RowValidationResult, *, job: ImportJob) -> None:
        n = r.normalized

        enrollment_date = n.get("enrollment_date")
        roll_year = enrollment_date.year if enrollment_date else institute_today().year
        roll_number = await generate_roll_number(self.session, year=roll_year)

        student = Student(
            first_name=n["first_name"],
            last_name=n["last_name"],
            date_of_birth=n["date_of_birth"],
            gender=n["gender"],
            cnic=n["cnic"],
            contact_no=n["contact_no"],
            marital_status=n["marital_status"],
            religion=n["religion"],
            nationality=n["nationality"],
            roll_number=roll_number,
            admission_fee_paid=n.get("admission_fee_paid"),
            enrollment_date=enrollment_date,
            program_id=n.get("_program_id"),
            # Program with no semester = student who can never enroll or be
            # charged a semester fee. Default new admissions to semester 1.
            current_semester=n.get("current_semester") or (1 if n.get("_program_id") else None),
            email=n.get("email"),
            blood_group=n.get("blood_group"),
            admission_session=n.get("admission_session"),
            admission_category=n.get("admission_category"),
            section_label=n.get("section_label"),
            status=StudentStatus.ACTIVE,
            source_import_job_id=job.id,
        )
        self.session.add(student)
        await self.session.flush()

        self.session.add(
            StudentGuardian(
                student_id=student.id,
                name=n["guardian_name"],
                cnic=n["guardian_cnic"],
                contact_no=n.get("guardian_contact_no"),
                relation=n["relation_with_guardian"],
                is_primary=True,
            )
        )
        self.session.add(
            StudentEmergencyContact(student_id=student.id, contact_no=n["emergency_contact_no"])
        )
        self.session.add(
            StudentAddress(
                student_id=student.id,
                address_type="PRESENT",
                address_line=n["present_address"],
                province=n["province"],
                district=n["district"],
                city=n["city"],
            )
        )

        # Account creation: a random one-time password (shown on the admission slip), forced
        # change on first login. Username IS the roll number now — one
        # identifier, not two, already guaranteed unique by the generator.
        username = roll_number
        temp = new_temp_password()
        user = User(
            username=username,
            password_hash=hash_password(temp),
            temp_password=temp,
            is_active=True,
            must_change_password=True,
        )
        self.session.add(user)
        await self.session.flush()

        student_role_stmt = select(Role).where(Role.code == "STUDENT")
        student_role = (await self.session.execute(student_role_stmt)).scalar_one_or_none()
        if student_role is not None:
            self.session.add(UserRole(user_id=user.id, role_id=student_role.id))

        student.user_id = user.id

        # Charge every already-ACTIVE fee structure matching this student.
        if student.program_id is not None:
            from app.fees.service import FeeStructureService

            await FeeStructureService(self.session).assign_matching_active_structures_to_student(
                student, actor_id=job.uploaded_by
            )

        # NOTE: credential delivery (email/SMS) is intentionally NOT sent
        # inline here — it is queued as a notification event decoupled from
        # this transaction (Section 47/48 — outbox pattern), so a delivery
        # failure can never roll back or block the student record itself.
        # The notification module is out of scope for this Phase A build.

        await self.audit.log(
            actor_user_id=job.uploaded_by,
            action="student.created_via_import",
            entity_type="student",
            entity_id=student.id,
            after={"cnic": student.cnic, "roll_number": student.roll_number},
            metadata={"import_job_id": str(job.id)},
        )

    async def _record_row_errors(
        self, job: ImportJob, *, row_number: int, errors: list[FieldError], raw_row: dict
    ) -> None:
        await self.repo.add_row_errors(
            [
                ImportRowError(
                    import_job_id=job.id,
                    row_number=row_number,
                    field=e.field,
                    error_type=e.error_type,
                    message=e.message,
                    raw_row=raw_row or None,
                )
                for e in errors
            ]
        )

    async def _fail(self, job: ImportJob, reason: str) -> None:
        job.status = ImportJobStatus.FAILED.value
        job.failure_reason = reason
        job.finished_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.audit.log(
            actor_user_id=job.uploaded_by,
            action="import.failed",
            entity_type="import_job",
            entity_id=job.id,
            metadata={"reason": reason},
        )