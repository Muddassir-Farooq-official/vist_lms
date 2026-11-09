"""Records: degree audit, clearance, certificates and ID cards (numbered, checkable online), documents on file."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.records.models import IssuedDocument, PersonDocument

CERTIFICATES = {"BONAFIDE": "Bonafide certificate", "CHARACTER": "Character certificate", "COMPLETION": "Completion certificate",
                "PROVISIONAL": "Provisional certificate (result)", "MIGRATION": "Migration certificate / NOC", "FEE_CLEARANCE": "Fee clearance letter",
                "EXPERIENCE": "Experience certificate", "CARD": "ID card"}
PREFIX = {"BONAFIDE": "BF", "CHARACTER": "CH", "COMPLETION": "CM", "PROVISIONAL": "PV", "MIGRATION": "MG", "FEE_CLEARANCE": "FC", "EXPERIENCE": "EX", "CARD": "ID"}
STUDENT_CERTIFICATES = ("BONAFIDE", "CHARACTER", "COMPLETION", "PROVISIONAL", "MIGRATION", "FEE_CLEARANCE")
# What has to be settled first: CLEAR = nothing outstanding (fees, fines, library, store); DEGREE = every course passed and the CGPA met.
CERTIFICATE_CHECKS = {"BONAFIDE": (), "CHARACTER": ("CLEAR",), "COMPLETION": ("CLEAR", "DEGREE"), "PROVISIONAL": ("DEGREE",),
                      "MIGRATION": ("CLEAR",), "FEE_CLEARANCE": ("FEES",)}
DOCUMENTS = {"CNIC": "CNIC / B-Form", "PHOTO": "Photograph", "MATRIC": "Matric certificate", "INTERMEDIATE": "Intermediate certificate",
             "DEGREE": "Degree / transcript", "DOMICILE": "Domicile", "TRANSFER": "Transfer / leaving certificate", "CONTRACT": "Contract / appointment letter",
             "EXPERIENCE": "Experience letter", "OTHER": "Other"}
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
OWNER = {"students": "STUDENT", "employees": "EMPLOYEE"}


def reference_of(d: IssuedDocument) -> str:
    return f"{PREFIX.get(d.kind, 'DOC')}-{(d.created_at or datetime.now(timezone.utc)).year}-{d.serial_no:05d}"


def verify_url(code: str) -> str:
    return f"{(settings.public_url or '').rstrip('/')}/verify/{code}"


class RecordsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ people
    async def _student(self, student_id: uuid.UUID):
        from app.students.models import Student

        s = await self.session.get(Student, student_id)
        if s is None:
            raise ResourceNotFoundError("Student not found.")
        return s

    async def _employee(self, employee_id: uuid.UUID):
        from app.employees.models import Employee

        e = await self.session.get(Employee, employee_id)
        if e is None:
            raise ResourceNotFoundError("Employee not found.")
        return e

    async def owner_of_user(self, user_id: uuid.UUID) -> tuple[str, uuid.UUID]:
        """The student or employee record of this login: ("STUDENT" | "EMPLOYEE", id)."""
        from app.employees.models import Employee
        from app.students.models import Student

        sid = (await self.session.execute(select(Student.id).where(Student.user_id == user_id))).scalar_one_or_none()
        if sid is not None:
            return "STUDENT", sid
        eid = (await self.session.execute(select(Employee.id).where(Employee.user_id == user_id))).scalar_one_or_none()
        if eid is not None:
            return "EMPLOYEE", eid
        raise ResourceNotFoundError("There is no student or staff record for your account.")

    async def student_of_user(self, user_id: uuid.UUID):
        from app.students.models import Student

        s = (await self.session.execute(select(Student).where(Student.user_id == user_id))).scalar_one_or_none()
        if s is None:
            raise ResourceNotFoundError("There is no student record for your account.")
        return s

    async def _owner_name(self, owner_type: str, owner_id: uuid.UUID) -> str:
        p = await (self._student(owner_id) if owner_type == "STUDENT" else self._employee(owner_id))
        return f"{p.first_name} {p.last_name}"

    # ------------------------------------------------------------------ degree audit
    async def degree_audit(self, student_id: uuid.UUID) -> dict:
        from app.academics.models import Course, CourseOffering, CourseProgram, Enrollment, EnrollmentStatus
        from app.academics.transcript_service import TranscriptService

        student = await self._student(student_id)
        curriculum = []
        if student.program_id:
            curriculum = (await self.session.execute(
                select(Course, CourseProgram.semester_number).join(CourseProgram, CourseProgram.course_id == Course.id)
                .where(CourseProgram.program_id == student.program_id).order_by(CourseProgram.semester_number, Course.code))).all()
        t = await TranscriptService(self.session).build(student_id)
        passed, failed = set(), set()
        for term in t["terms"]:
            for c in term["courses"]:
                if c.get("withdrawn"):
                    continue
                (passed if c["outcome"] == "PASSED" else failed).add(c["course_code"])
        failed -= passed                                           # a later pass clears an earlier failure
        studying = set((await self.session.execute(
            select(Course.code).join(CourseOffering, CourseOffering.course_id == Course.id).join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
            .where(Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED))).scalars().all())
        courses, missing = [], []
        for course, semester in curriculum:
            status = "PASSED" if course.code in passed else "IN_PROGRESS" if course.code in studying else "FAILED" if course.code in failed else "NOT_TAKEN"
            courses.append({"semester": semester, "code": course.code, "title": course.title, "credit_hours": course.credit_hours, "status": status})
            if status != "PASSED":
                missing.append(course.code)
        required = sum(c["credit_hours"] for c in courses)
        earned = sum(c["credit_hours"] for c in courses if c["status"] == "PASSED")
        cgpa, min_cgpa = float(t["summary"]["cgpa"] or 0), float(settings.graduation_min_cgpa)
        problems = []
        if not courses:
            problems.append("The program has no course list to check against.")
        if missing:
            problems.append(f"{len(missing)} course(s) still to pass.")
        if courses and cgpa + 1e-9 < min_cgpa:
            problems.append(f"CGPA {cgpa:.2f} is below the {min_cgpa:.2f} needed.")
        return {"student_id": student_id, "credits_passed": earned, "credits_required": required, "cgpa": cgpa, "min_cgpa": min_cgpa,
                "missing": missing, "courses": courses, "problems": problems, "clear": not problems}

    # ------------------------------------------------------------------ clearance
    async def clearance(self, owner_type: str, owner_id: uuid.UUID) -> dict:
        from app.fines.models import Fine, FinePayerType, FineStatus
        from app.inventory.models import MovementKind, StockItem, StockMovement
        from app.library.models import BookIssue, BorrowerType, IssueStatus

        await self._owner_name(owner_type, owner_id)
        problems, to_settle, store = [], [], []
        pending = (await self.session.execute(select(func.coalesce(func.sum(Fine.amount), 0), func.count()).where(
            Fine.payer_type == FinePayerType(owner_type), Fine.payer_id == owner_id, Fine.status == FineStatus.PENDING))).one()
        if pending[1]:
            problems.append(f"{pending[1]} unpaid fine(s), Rs. {float(pending[0]):,.0f} in all.")
        books = (await self.session.execute(select(func.count()).select_from(BookIssue).where(
            BookIssue.borrower_type == BorrowerType(owner_type), BookIssue.borrower_id == owner_id, BookIssue.status == IssueStatus.ISSUED))).scalar_one()
        if books:
            problems.append(f"{books} library book(s) not returned.")
        issued = (await self.session.execute(
            select(StockMovement, StockItem.name).join(StockItem, StockItem.id == StockMovement.item_id).where(
                StockMovement.kind == MovementKind.ISSUED, StockMovement.party_type == owner_type, StockMovement.party_id == owner_id, StockItem.is_returnable.is_(True)))).all()
        for mv, name in issued:
            back = (await self.session.execute(select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
                StockMovement.issue_id == mv.id, StockMovement.kind == MovementKind.RETURNED))).scalar_one()
            reversed_ = (await self.session.execute(select(func.count()).select_from(StockMovement).where(StockMovement.reverses_id == mv.id))).scalar_one()
            left = abs(float(mv.quantity)) - float(back)
            if left > 1e-9 and not reversed_:
                store.append({"quantity": left, "item": name, "reference": f"MOV-{mv.serial_no:06d}"})
        if store:
            problems.append("Store item(s) not returned: " + ", ".join(f"{s['quantity']:g} x {s['item']}" for s in store) + ".")
        if owner_type == "STUDENT":
            problems += await self._student_fee_problems(owner_id)
            from app.hostel.models import HostelStay

            stay = (await self.session.execute(select(func.count()).select_from(HostelStay).where(HostelStay.student_id == owner_id, HostelStay.end_date.is_(None)))).scalar_one()
            if stay:
                problems.append("Still living in the hostel: end the stay first.")
            to_settle.append("Return the ID card and library card.")
        else:
            from app.payroll.models import AdvanceStatus, SalaryAdvance

            owed = (await self.session.execute(select(func.coalesce(func.sum(SalaryAdvance.balance), 0)).where(
                SalaryAdvance.employee_id == owner_id, SalaryAdvance.status == AdvanceStatus.ACTIVE))).scalar_one()
            if float(owed) > 0:
                to_settle.append(f"Salary advance still owed: Rs. {float(owed):,.0f} (recover from the final pay).")
            try:
                from app.payroll.service import PayrollService

                held = next((p["held"] for p in (await PayrollService(self.session).security_report())["people"] if p["employee_id"] == owner_id), 0)
                if held:
                    to_settle.append(f"Security deposit held: Rs. {held:,.0f} (to be returned).")
            except Exception:  # noqa: BLE001 - clearance must still answer
                pass
            to_settle.append("Return the ID card, keys and equipment.")
        return {"clear": not problems, "problems": problems, "store_items": store, "to_settle": to_settle}

    async def _student_fee_problems(self, student_id: uuid.UUID) -> list[str]:
        """Clearance needs ALL fees settled, including installments not yet due and late fees not yet posted
        (owner decision 7). Unpaid fines are checked separately."""
        from app.fees.assignment_service import FeeStatusCalculator, late_pause
        from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, PayerType

        rows = (await self.session.execute(select(FeeAssignment, FeeStructure.late_fee_per_day).join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id).where(
            FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.payer_id == student_id, FeeAssignment.status == AssignmentStatus.ACTIVE))).all()
        overdue = upcoming = late = 0.0
        for a, per_day in rows:
            entries = list((await self.session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == a.id))).scalars())
            for r in FeeStatusCalculator.installments(entries, float(per_day) if per_day else None, pause=late_pause(a)):
                if r["status"] == "OVERDUE":
                    overdue += r["remaining"]
                else:
                    upcoming += r["remaining"]
                late += max(r["late_fee"] - r["late_fee_posted"], 0.0)       # posted late fees are fines, counted with the fines
        out = []
        if overdue > 0.005:
            out.append(f"Fees overdue: Rs. {overdue:,.0f}.")
        if upcoming > 0.005:
            out.append(f"Fees not yet due: Rs. {upcoming:,.0f}.")
        if late > 0.005:
            out.append(f"Late fee: Rs. {late:,.0f}.")
        return out

    async def fee_dues_total(self, student_id: uuid.UUID) -> list[str]:
        return await self._student_fee_problems(student_id)

    # ------------------------------------------------------------------ certificates
    async def _register(self, kind: str, owner_type: str, owner_id: uuid.UUID, name: str, details: dict, actor_id) -> IssuedDocument:
        for _ in range(5):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
            if (await self.session.execute(select(func.count()).select_from(IssuedDocument).where(IssuedDocument.verification_code == code))).scalar_one() == 0:
                break
        doc = IssuedDocument(kind=kind, owner_type=owner_type, owner_id=owner_id, owner_name=name, verification_code=code, details=details, issued_by=actor_id)
        self.session.add(doc)
        await self.session.flush()
        await self.session.refresh(doc)
        await self.audit.log(actor_user_id=actor_id, action=f"records.{kind.lower()}_issued", entity_type="issued_document", entity_id=doc.id, metadata={"owner": name, **details})
        return doc

    async def certificate_problems(self, student_id: uuid.UUID, kind: str) -> list[str]:
        """What stands in the way of this certificate (empty = it can be issued)."""
        problems: list[str] = []
        checks = CERTIFICATE_CHECKS.get(kind, ())
        if "CLEAR" in checks:
            problems += (await self.clearance("STUDENT", student_id))["problems"]
        if "FEES" in checks:
            problems += await self._student_fee_problems(student_id)
        if "DEGREE" in checks:
            problems += (await self.degree_audit(student_id))["problems"]
        return problems

    async def certificate_paragraphs(self, student_id: uuid.UUID, kind: str) -> list[str]:
        from app.programs.models import Program

        s = await self._student(student_id)
        name = f"{s.first_name} {s.last_name}"
        program = await self.session.get(Program, s.program_id) if s.program_id else None
        prog = program.name if program else "the institute"
        inst = settings.institute_name
        today = institute_today()
        if kind == "BONAFIDE":
            who = f"{name}, {'son' if (s.gender or '').lower().startswith('m') else 'daughter' if (s.gender or '').lower().startswith('f') else 'child'} of the guardian on record"
            return [f"This is to certify that {who}, holding roll number {s.roll_number}, is a bonafide student of {inst}, enrolled in {prog}"
                    + (f" and currently studying in semester {s.current_semester}" if s.current_semester else "") + ".",
                    "This certificate is issued on the student's request for whatever purpose it may serve."]
        if kind == "CHARACTER":
            return [f"This is to certify that {name}, roll number {s.roll_number}, was a student of {prog} at {inst}.",
                    "During this time the student's conduct and character were found satisfactory, and nothing is recorded against them in the institute's disciplinary or accounts records."]
        if kind == "COMPLETION":
            return [f"This is to certify that {name}, roll number {s.roll_number}, has completed all the requirements of {prog} at {inst}.",
                    "The degree itself is awarded separately by the competent authority."]
        if kind == "PROVISIONAL":
            a = await self.degree_audit(student_id)
            return [f"This is to certify that {name}, roll number {s.roll_number}, has passed the examinations of {prog} at {inst}, "
                    f"earning {a['credits_passed']:g} credit hours with a CGPA of {a['cgpa']:.2f}.",
                    "This provisional certificate is valid until the degree or final transcript is issued by the competent authority."]
        if kind == "MIGRATION":
            return [f"This is to certify that {name}, roll number {s.roll_number}, has been a student of {prog} at {inst}"
                    + (f" up to semester {s.current_semester}" if s.current_semester else "") + ".",
                    "Nothing is outstanding against the student in the institute's accounts, library or store, and the institute has "
                    "no objection to the student migrating to another institution."]
        if kind == "FEE_CLEARANCE":
            return [f"This is to certify that {name}, roll number {s.roll_number}, a student of {prog} at {inst}, has paid all the fees "
                    f"charged by the institute up to {today:%d %B %Y}.",
                    "Nothing is outstanding in the student's fee account on this date."]
        raise ValidationAppError("Unknown certificate.")

    async def issue_student_certificate(self, student_id: uuid.UUID, kind: str, *, actor_id, override_reason: str | None = None,
                                        checked: bool = False, extra: dict | None = None) -> dict:
        """checked=True: the checks were already made (a request approved earlier), so they are not made again now."""
        kind = kind.upper()
        if kind not in STUDENT_CERTIFICATES:
            raise ValidationAppError("Unknown certificate. Choose one from the list.")
        from app.programs.models import Program

        s = await self._student(student_id)
        name = f"{s.first_name} {s.last_name}"
        program = await self.session.get(Program, s.program_id) if s.program_id else None
        override = (override_reason or "").strip() or None
        if not checked:
            problems = await self.certificate_problems(student_id, kind)
            if problems and not override:
                raise BusinessRuleViolationError("Not cleared: " + " ".join(problems) + " To issue it anyway, give a reason.")
            if problems and len(override) < 10:
                raise BusinessRuleViolationError("Please give a reason of at least 10 characters.")
        paragraphs = await self.certificate_paragraphs(student_id, kind)
        doc = await self._register(kind, "STUDENT", student_id, name, {"roll_number": s.roll_number, "program": program.name if program else None,
                                                                         "override_reason": override, **(extra or {})}, actor_id)
        return {"doc": doc, "paragraphs": paragraphs, "institute": settings.institute_name, "issued_on": institute_today()}

    async def issue_experience_certificate(self, employee_id: uuid.UUID, *, actor_id, override_reason: str | None = None) -> dict:
        from app.employees.models import JobRole

        e = await self._employee(employee_id)
        name = f"{e.first_name} {e.last_name}"
        clear = await self.clearance("EMPLOYEE", employee_id)
        override = (override_reason or "").strip() or None
        if clear["problems"] and not override:
            raise BusinessRuleViolationError("Not cleared: " + " ".join(clear["problems"]) + " To issue it anyway, give a reason.")
        if clear["problems"] and len(override) < 10:
            raise BusinessRuleViolationError("Please give a reason of at least 10 characters.")
        title = (await self.session.execute(select(JobRole.title).where(JobRole.id == e.job_role_id))).scalar_one_or_none() or "staff member"
        doc = await self._register("EXPERIENCE", "EMPLOYEE", employee_id, name, {"employee_code": e.employee_code, "title": title, "override_reason": override}, actor_id)
        end = e.left_on.strftime("%d %B %Y") if e.left_on else "the present day"
        paragraphs = [f"This is to certify that {name} (employee code {e.employee_code}) has worked at {settings.institute_name} as {title}"
                      f"{' in the ' + e.department + ' department' if e.department else ''} from {e.hire_date.strftime('%d %B %Y')} to {end}.",
                      "During this period their work and conduct were found satisfactory. We wish them success in the future."]
        return {"doc": doc, "paragraphs": paragraphs, "institute": settings.institute_name, "issued_on": institute_today()}

    async def id_cards(self, *, student_ids: list[uuid.UUID], employee_ids: list[uuid.UUID], actor_id) -> dict:
        from app.employees.models import JobRole
        from app.programs.models import Program

        if not student_ids and not employee_ids:
            raise ValidationAppError("Choose at least one person.")
        if len(student_ids) + len(employee_ids) > 200:
            raise ValidationAppError("At most 200 ID cards at a time.")
        today = institute_today()
        valid_to = today.replace(year=today.year + 1).strftime("%d/%m/%Y")
        cards = []
        for sid in student_ids:
            s = await self._student(sid)
            program = await self.session.get(Program, s.program_id) if s.program_id else None
            name = f"{s.first_name} {s.last_name}"
            doc = await self._register("CARD", "STUDENT", sid, name, {"roll_number": s.roll_number}, actor_id)
            cards.append({"kind": "STUDENT", "name": name, "photo_key": s.photo_storage_key,
                          "fields": [("Roll No", s.roll_number), ("Program", program.name if program else None), ("Semester", str(s.current_semester) if s.current_semester else None),
                                     ("CNIC No", s.cnic), ("Contact", s.contact_no)],
                          "verify_url": verify_url(doc.verification_code), "reference": reference_of(doc), "valid_to": valid_to})
        for eid in employee_ids:
            e = await self._employee(eid)
            title = (await self.session.execute(select(JobRole.title).where(JobRole.id == e.job_role_id))).scalar_one_or_none() or "Staff"
            name = f"{e.first_name} {e.last_name}"
            doc = await self._register("CARD", "EMPLOYEE", eid, name, {"employee_code": e.employee_code}, actor_id)
            cards.append({"kind": "STAFF", "name": name, "photo_key": e.photo_storage_key, "title": title,
                          "fields": [("Code", e.employee_code), ("Designation", title), ("Department", e.department), ("CNIC No", e.cnic), ("Contact", e.contact_no)],
                          "verify_url": verify_url(doc.verification_code), "reference": reference_of(doc), "valid_to": valid_to})
        return {"institute": settings.institute_name, "cards": cards}

    async def issued_for(self, owner_type: str, owner_id: uuid.UUID) -> list[dict]:
        rows = (await self.session.execute(select(IssuedDocument).where(IssuedDocument.owner_type == owner_type, IssuedDocument.owner_id == owner_id)
                                           .order_by(IssuedDocument.created_at.desc()))).scalars().all()
        return [{"id": d.id, "reference": reference_of(d), "kind": d.kind, "kind_label": CERTIFICATES.get(d.kind, d.kind), "issued_at": d.created_at,
                 "code": d.verification_code, "revoked": d.revoked_at is not None, "revoke_reason": d.revoke_reason} for d in rows]

    async def revoke(self, issued_id: uuid.UUID, reason: str, *, actor_id) -> dict:
        d = await self.session.get(IssuedDocument, issued_id)
        if d is None:
            raise ResourceNotFoundError("Certificate not found.")
        if d.revoked_at is not None:
            raise BusinessRuleViolationError("This one is already cancelled.")
        d.revoked_at, d.revoke_reason = datetime.now(timezone.utc), reason.strip()
        await self.audit.log(actor_user_id=actor_id, action="records.revoked", entity_type="issued_document", entity_id=d.id, metadata={"reference": reference_of(d), "reason": d.revoke_reason})
        return {"id": d.id, "revoked": True}

    async def verify(self, code: str) -> dict | None:
        """Public check. Returns only what a stranger holding the paper needs: what it is, whose, when, and whether it still stands."""
        code = (code or "").strip().upper()
        d = (await self.session.execute(select(IssuedDocument).where(IssuedDocument.verification_code == code))).scalar_one_or_none() if code else None
        if d is None:
            return None
        return {"reference": reference_of(d), "kind": d.kind, "kind_label": CERTIFICATES.get(d.kind, d.kind), "owner_name": d.owner_name,
                "issued_on": d.created_at.date(), "revoked": d.revoked_at is not None, "revoke_reason": d.revoke_reason}

    # ------------------------------------------------------------------ documents on file
    @staticmethod
    def doc_out(d: PersonDocument) -> dict:
        return {"id": d.id, "kind": d.kind, "kind_label": DOCUMENTS.get(d.kind, d.kind), "title": d.title, "file": d.original_name, "uploaded_at": d.created_at,
                "verified": d.verified_at is not None, "archived": d.archived_at is not None, "archive_reason": d.archive_reason}

    async def list_documents(self, owner_type: str, owner_id: uuid.UUID, *, include_archived: bool) -> list[dict]:
        stmt = select(PersonDocument).where(PersonDocument.owner_type == owner_type, PersonDocument.owner_id == owner_id)
        if not include_archived:
            stmt = stmt.where(PersonDocument.archived_at.is_(None))
        return [self.doc_out(d) for d in (await self.session.execute(stmt.order_by(PersonDocument.created_at.desc()))).scalars()]

    async def add_document(self, owner_type: str, owner_id: uuid.UUID, *, kind: str, title: str | None, content: bytes, filename: str, content_type: str | None, actor_id) -> dict:
        from app.core.storage import FileTooLargeError, UnsupportedFileTypeError, save_attachment

        kind = (kind or "OTHER").upper()
        if kind not in DOCUMENTS:
            raise ValidationAppError("Choose a kind of document from the list.")
        await self._owner_name(owner_type, owner_id)
        try:
            key, name, ctype = save_attachment(entity_id=owner_id, content=content, original_filename=filename, declared_content_type=content_type, folder="person-documents")
        except (UnsupportedFileTypeError, FileTooLargeError) as exc:
            raise ValidationAppError(str(exc)) from exc
        d = PersonDocument(owner_type=owner_type, owner_id=owner_id, kind=kind, title=(title or "").strip() or DOCUMENTS[kind], original_name=name, storage_key=key,
                           content_type=ctype, size_bytes=len(content), uploaded_by=actor_id)
        self.session.add(d)
        await self.session.flush()
        await self.session.refresh(d)
        await self.audit.log(actor_user_id=actor_id, action="records.document_added", entity_type="person_document", entity_id=d.id, metadata={"kind": kind, "owner_type": owner_type})
        return self.doc_out(d)

    async def document(self, document_id: uuid.UUID) -> PersonDocument:
        d = await self.session.get(PersonDocument, document_id)
        if d is None:
            raise ResourceNotFoundError("Document not found.")
        return d

    async def verify_document(self, document_id: uuid.UUID, *, actor_id) -> dict:
        d = await self.document(document_id)
        d.verified_by, d.verified_at = actor_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=actor_id, action="records.document_checked", entity_type="person_document", entity_id=d.id)
        return self.doc_out(d)

    async def archive_document(self, document_id: uuid.UUID, reason: str, *, actor_id) -> dict:
        d = await self.document(document_id)
        if d.archived_at is not None:
            raise BusinessRuleViolationError("This document is already archived.")
        d.archived_at, d.archive_reason = datetime.now(timezone.utc), reason.strip()[:300]
        await self.audit.log(actor_user_id=actor_id, action="records.document_archived", entity_type="person_document", entity_id=d.id, metadata={"reason": d.archive_reason})
        return self.doc_out(d)

    # ------------------------------------------------------------------ everything on file, as one PDF
    async def person(self, owner_type: str, owner_id: uuid.UUID):
        return await (self._student(owner_id) if owner_type == "STUDENT" else self._employee(owner_id))

    async def bundle_parts(self, owner_type: str, owner_id: uuid.UUID) -> tuple[dict, list]:
        """What goes into the one-PDF download: photo, the combined certificate PDF the person uploaded, then every
        current (not archived) document, in the order of the document kinds."""
        from app.core.storage import get_file_path
        from app.records.bundle import Part

        p = await self.person(owner_type, owner_id)

        def read(key: str | None) -> bytes | None:
            path = get_file_path(key) if key else None
            return path.read_bytes() if path is not None and path.exists() else None

        parts = []
        if p.photo_storage_key:
            parts.append(Part("Photograph (profile photo)", "", read(p.photo_storage_key), "image/jpeg" if (p.photo_original_name or "").lower().endswith((".jpg", ".jpeg")) else
                              "image/png" if (p.photo_original_name or "").lower().endswith(".png") else "image/webp", p.photo_original_name or "photo"))
        if p.certificate_storage_key:
            parts.append(Part("Certificates (combined PDF uploaded on their profile)", "", read(p.certificate_storage_key), "application/pdf",
                              p.certificate_original_name or "certificates.pdf"))
        order = {k: i for i, k in enumerate(DOCUMENTS)}
        docs = (await self.session.execute(select(PersonDocument).where(PersonDocument.owner_type == owner_type, PersonDocument.owner_id == owner_id,
                                                                        PersonDocument.archived_at.is_(None)))).scalars().all()
        for d in sorted(docs, key=lambda d: (order.get(d.kind, 99), d.created_at)):
            label = DOCUMENTS.get(d.kind, d.kind)
            title = label if d.title == label else f"{label}: {d.title}"
            note = f"uploaded {d.created_at:%d %b %Y} · " + ("checked against the original" if d.verified_at else "not checked yet")
            parts.append(Part(title, note, read(d.storage_key), d.content_type, d.original_name))
        person = {"name": f"{p.first_name} {p.last_name}".strip(), "code": (p.roll_number if owner_type == "STUDENT" else p.employee_code) or "",
                  "kind": "Student" if owner_type == "STUDENT" else "Staff", "institute": settings.institute_name}
        return person, parts

    async def profile_files(self, owner_type: str, owner_id: uuid.UUID) -> dict:
        """The photo and combined certificate PDF kept on the person's profile (not in the documents list)."""
        p = await self.person(owner_type, owner_id)
        base = f"/api/v1/{'students' if owner_type == 'STUDENT' else 'employees'}/{owner_id}"
        return {"photo": {"name": p.photo_original_name, "url": base + "/photo"} if p.photo_storage_key else None,
                "certificate": {"name": p.certificate_original_name, "url": base + "/certificate"} if p.certificate_storage_key else None}