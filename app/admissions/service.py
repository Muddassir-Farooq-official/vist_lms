r"""Admissions.

ROUND: one per program and term. Seats, merit formula (weights of matric %, intermediate %
and entry-test %, adding up to 100), minimum intermediate % (HEC's usual 45% for BS), closing date.

APPLICATION: the applicant's details are checked with the SAME rules as a student record
(StudentCreateRequest) when they are entered, so admitting can never fail on bad data.
aggregate = matric% x w_matric + inter% x w_inter + test% x w_test   (out of 100)

FLOW:  SUBMITTED --verify--> VERIFIED --merit list--> OFFERED --admit--> ADMITTED (a student)
                  \--reject (reason)--> REJECTED          \--deadline passed--> EXPIRED (seat freed)
A merit list offers the free seats (seats - offered - admitted) to the highest-aggregate
VERIFIED applicants. Ties: higher intermediate %, then earlier application. After expiring
unanswered offers, the next merit list fills the seats they freed.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admissions.models import AdmissionRound, Applicant, ApplicantStatus, RoundStatus
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError

ACADEMIC = ("matric_obtained", "matric_total", "inter_obtained", "inter_total", "test_obtained", "test_total")
CSV_REQUIRED = ["first_name", "last_name", "date_of_birth", "gender", "cnic", "contact_no", "marital_status", "religion", "nationality",
                "guardian_name", "guardian_cnic", "relation_with_guardian", "emergency_contact_no", "present_address", "province", "district",
                "city", "matric_obtained", "matric_total", "inter_obtained", "inter_total"]
CSV_COLUMNS = CSV_REQUIRED + ["guardian_contact_no", "email", "test_obtained", "test_total"]


def _pct(obt, tot) -> float | None:
    return None if obt is None or not tot else round(float(obt) / float(tot) * 100, 3)


def aggregate(r: AdmissionRound, a) -> float:
    test = _pct(a.test_obtained, a.test_total) or 0.0
    return round((_pct(a.matric_obtained, a.matric_total) * float(r.weight_matric) + _pct(a.inter_obtained, a.inter_total) * float(r.weight_inter)
                  + test * float(r.weight_test)) / 100, 3)


async def next_application_no(session: AsyncSession) -> str:
    from app.core.sequences import next_sequence_value

    year = institute_today().year
    return f"APP-{year}-{await next_sequence_value(session, f'application:{year}'):06d}"


class AdmissionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------------- rounds
    async def create_round(self, *, term_label: str, program_id: uuid.UUID, seats: int, weight_matric: float, weight_inter: float,
                           weight_test: float, min_inter_percent: float, closes_on: date | None, actor_id: uuid.UUID) -> AdmissionRound:
        from app.academics.terms import AcademicTermService
        from app.programs.models import Program

        await AcademicTermService(self.session).require_usable(term_label)
        if await self.session.get(Program, program_id) is None:
            raise ResourceNotFoundError("Program not found.")
        if seats < 1:
            raise ValidationAppError("Seats must be at least 1.")
        if min(weight_matric, weight_inter, weight_test) < 0 or abs(weight_matric + weight_inter + weight_test - 100) > 0.001:
            raise ValidationAppError("The merit weights must add up to 100 (e.g. matric 10, intermediate 40, test 50).")
        if (await self.session.execute(select(AdmissionRound).where(AdmissionRound.term_label == term_label,
                                                                    AdmissionRound.program_id == program_id))).scalar_one_or_none():
            raise DuplicateResourceError("This program already has an admission round for that term.")
        r = AdmissionRound(term_label=term_label, program_id=program_id, seats=seats, weight_matric=weight_matric, weight_inter=weight_inter,
                           weight_test=weight_test, min_inter_percent=min_inter_percent, closes_on=closes_on, created_by=actor_id)
        self.session.add(r)
        await self.session.flush()
        return r

    async def get_round(self, round_id: uuid.UUID) -> AdmissionRound:
        r = await self.session.get(AdmissionRound, round_id)
        if r is None:
            raise ResourceNotFoundError("Admission round not found.")
        return r

    async def rounds(self) -> list[dict]:
        from app.programs.models import Program

        out = []
        for r, p in (await self.session.execute(select(AdmissionRound, Program).join(Program, Program.id == AdmissionRound.program_id)
                                                .order_by(AdmissionRound.created_at.desc()))).all():
            counts = {(k.value if hasattr(k, "value") else k): n for k, n in (await self.session.execute(
                select(Applicant.status, func.count()).where(Applicant.round_id == r.id).group_by(Applicant.status))).all()}
            out.append(self._round_dict(r, p.code, counts))
        return out

    @staticmethod
    def _round_dict(r: AdmissionRound, program_code: str, counts: dict) -> dict:
        taken = counts.get("OFFERED", 0) + counts.get("ADMITTED", 0)
        return {"id": r.id, "term_label": r.term_label, "program_id": r.program_id, "program": program_code, "seats": r.seats,
                "weights": {"matric": float(r.weight_matric), "inter": float(r.weight_inter), "test": float(r.weight_test)},
                "min_inter_percent": float(r.min_inter_percent), "closes_on": r.closes_on, "status": r.status.value,
                "merit_lists_published": r.merit_lists_published, "counts": counts, "seats_free": max(r.seats - taken, 0)}

    async def close_round(self, round_id: uuid.UUID) -> AdmissionRound:
        r = await self.get_round(round_id)
        r.status = RoundStatus.CLOSED
        return r

    # ---------------------------------------------------------------- applications
    async def apply(self, round_id: uuid.UUID, data: dict, *, actor_id: uuid.UUID) -> Applicant:
        from app.students.repository import StudentRepository
        from app.students.schemas import StudentCreateRequest

        r = await self.get_round(round_id)
        if r.status != RoundStatus.OPEN:
            raise BusinessRuleViolationError("This admission round is closed.")
        if r.closes_on and institute_today() > r.closes_on:
            raise BusinessRuleViolationError(f"Applications closed on {r.closes_on:%d %b %Y}.")
        academic = {k: data.pop(k, None) for k in ACADEMIC}
        for k in ("matric_obtained", "matric_total", "inter_obtained", "inter_total"):
            if academic[k] is None:
                raise ValidationAppError(f"{k.replace('_', ' ').capitalize()} is required.")
        for part in ("matric", "inter", "test"):
            obt, tot = academic[f"{part}_obtained"], academic[f"{part}_total"]
            if obt is not None and (tot is None or float(tot) <= 0 or not 0 <= float(obt) <= float(tot)):
                raise ValidationAppError(f"{part.capitalize()} marks: obtained must be between 0 and the total.")
        if float(r.weight_test) > 0 and academic["test_obtained"] is None:
            raise ValidationAppError("This program's merit includes the entry test: enter the test score.")
        try:                                     # the same rules as a student record
            req = StudentCreateRequest(**{**data, "program_id": r.program_id, "current_semester": 1, "admission_session": r.term_label})
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValidationAppError(f"{'.'.join(str(x) for x in first['loc'])}: {first['msg']}") from None
        if await StudentRepository(self.session).get_by_cnic(req.cnic):
            raise DuplicateResourceError(f"A student with CNIC {req.cnic} already exists (already admitted earlier).")
        if (await self.session.execute(select(Applicant).where(Applicant.round_id == round_id, Applicant.cnic == req.cnic))).scalar_one_or_none():
            raise DuplicateResourceError(f"An application with CNIC {req.cnic} is already in this round.")
        a = Applicant(round_id=round_id, application_no=await next_application_no(self.session), first_name=req.first_name, last_name=req.last_name,
                      cnic=req.cnic, contact_no=req.contact_no, details=req.model_dump(mode="json", exclude={"program_id", "current_semester"}),
                      created_by=actor_id, **{k: v for k, v in academic.items()}, aggregate=0)
        a.aggregate = aggregate(r, a)
        if _pct(a.inter_obtained, a.inter_total) < float(r.min_inter_percent):
            a.status, a.remarks = ApplicantStatus.REJECTED, f"Intermediate below {float(r.min_inter_percent):g}%"
        self.session.add(a)
        await self.session.flush()
        return a

    async def _applicant(self, applicant_id: uuid.UUID) -> Applicant:
        a = await self.session.get(Applicant, applicant_id)
        if a is None:
            raise ResourceNotFoundError("Application not found.")
        return a

    async def set_status(self, applicant_id: uuid.UUID, *, status: ApplicantStatus, remarks: str | None) -> Applicant:
        a = await self._applicant(applicant_id)
        allowed = {ApplicantStatus.VERIFIED: {ApplicantStatus.SUBMITTED, ApplicantStatus.REJECTED},
                   ApplicantStatus.REJECTED: {ApplicantStatus.SUBMITTED, ApplicantStatus.VERIFIED},
                   ApplicantStatus.WITHDRAWN: {ApplicantStatus.SUBMITTED, ApplicantStatus.VERIFIED, ApplicantStatus.OFFERED}}
        if status not in allowed:
            raise ValidationAppError("Use verify, reject or withdraw here; offers come from merit lists and admission from Admit.")
        if a.status not in allowed[status]:
            raise BusinessRuleViolationError(f"A {a.status.value.lower()} application cannot be marked {status.value.lower()}.")
        if status == ApplicantStatus.REJECTED and not (remarks or "").strip():
            raise ValidationAppError("Give the reason for rejecting.")
        if status == ApplicantStatus.VERIFIED:
            r = await self.get_round(a.round_id)
            if _pct(a.inter_obtained, a.inter_total) < float(r.min_inter_percent):
                raise BusinessRuleViolationError(f"Intermediate {_pct(a.inter_obtained, a.inter_total):.1f}% is below the minimum {float(r.min_inter_percent):g}%.")
        a.status, a.remarks = status, (remarks or "").strip() or a.remarks
        if status == ApplicantStatus.WITHDRAWN:
            a.offer_expires_on = None
        return a

    # ---------------------------------------------------------------- merit lists
    async def ranked(self, round_id: uuid.UUID) -> list[Applicant]:
        rows = list((await self.session.execute(select(Applicant).where(Applicant.round_id == round_id, Applicant.status == ApplicantStatus.VERIFIED))).scalars())
        return sorted(rows, key=lambda a: (-float(a.aggregate), -_pct(a.inter_obtained, a.inter_total), a.created_at))

    async def publish_merit_list(self, round_id: uuid.UUID, *, offer_days: int, actor_id: uuid.UUID) -> dict:
        r = await self.get_round(round_id)
        if r.status != RoundStatus.OPEN:
            raise BusinessRuleViolationError("This admission round is closed.")
        if offer_days < 1:
            raise ValidationAppError("Give at least one day to take up the offer.")
        taken = (await self.session.execute(select(func.count()).select_from(Applicant).where(
            Applicant.round_id == round_id, Applicant.status.in_([ApplicantStatus.OFFERED, ApplicantStatus.ADMITTED])))).scalar_one()
        free = r.seats - taken
        if free <= 0:
            raise BusinessRuleViolationError("No free seats: every seat is offered or taken. Expire unanswered offers first.")
        ranked = await self.ranked(round_id)
        if not ranked:
            raise BusinessRuleViolationError("No verified applicants are waiting for a seat.")
        r.merit_lists_published += 1
        deadline = institute_today() + timedelta(days=offer_days)
        from app.sms.service import SmsService

        sms = SmsService(self.session)
        offered = []
        for rank, a in enumerate(ranked[:free], start=1):
            a.status, a.merit_list_no, a.merit_rank, a.offer_expires_on = ApplicantStatus.OFFERED, r.merit_lists_published, rank, deadline
            offered.append(a)
            await sms.queue_to_number(a.contact_no, event="ADMISSION",
                                      body=f"{settings.app_name}: congratulations, {a.first_name}! You are selected in merit list {r.merit_lists_published} "
                                           f"(application {a.application_no}). Complete admission by {deadline:%d %b %Y}.")
        await self.session.flush()
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="admissions.merit_list_published", entity_type="admission_round",
                                             entity_id=r.id, after={"list": r.merit_lists_published, "offered": len(offered), "deadline": str(deadline)})
        return {"merit_list_no": r.merit_lists_published, "offered": len(offered), "offer_deadline": deadline,
                "lowest_aggregate_offered": float(offered[-1].aggregate) if offered else None, "still_waiting": len(ranked) - len(offered)}

    async def expire_offers(self, round_id: uuid.UUID) -> int:
        today = institute_today()
        rows = list((await self.session.execute(select(Applicant).where(Applicant.round_id == round_id, Applicant.status == ApplicantStatus.OFFERED,
                                                                         Applicant.offer_expires_on < today))).scalars())
        for a in rows:
            a.status, a.remarks = ApplicantStatus.EXPIRED, f"Offer not taken up by {a.offer_expires_on:%d %b %Y}"
        return len(rows)

    # ---------------------------------------------------------------- admit
    async def admit(self, applicant_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        from app.students.schemas import StudentCreateRequest
        from app.students.service import StudentService

        a = await self._applicant(applicant_id)
        if a.status != ApplicantStatus.OFFERED:
            raise BusinessRuleViolationError("Only an applicant with an offer can be admitted.")
        if a.offer_expires_on and institute_today() > a.offer_expires_on:
            raise BusinessRuleViolationError(f"The offer expired on {a.offer_expires_on:%d %b %Y}.")
        r = await self.get_round(a.round_id)
        req = StudentCreateRequest(**{**a.details, "program_id": r.program_id, "current_semester": 1, "admission_session": r.term_label,
                                      "enrollment_date": institute_today()})
        student = await StudentService(self.session).create_student_manually(req, actor_id=actor_id)
        a.status, a.student_id = ApplicantStatus.ADMITTED, student.id
        await self.session.flush()
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="admissions.admitted", entity_type="applicant", entity_id=a.id,
                                             after={"student_id": str(student.id), "application_no": a.application_no})
        return {"student_id": student.id, "roll_number": student.roll_number, "username": getattr(student, "_created_username", None)}

    # ---------------------------------------------------------------- lists
    async def applicants(self, round_id: uuid.UUID, status: ApplicantStatus | None = None) -> list[dict]:
        stmt = select(Applicant).where(Applicant.round_id == round_id)
        if status:
            stmt = stmt.where(Applicant.status == status)
        rows = list((await self.session.execute(stmt)).scalars())
        rows.sort(key=lambda a: (-float(a.aggregate), a.created_at))
        return [self.applicant_dict(a) for a in rows]

    @staticmethod
    def applicant_dict(a: Applicant) -> dict:
        return {"id": a.id, "application_no": a.application_no, "name": f"{a.first_name} {a.last_name}", "cnic": a.cnic, "contact_no": a.contact_no,
                "father_or_guardian": a.details.get("guardian_name"), "matric_percent": _pct(a.matric_obtained, a.matric_total),
                "inter_percent": _pct(a.inter_obtained, a.inter_total), "test_percent": _pct(a.test_obtained, a.test_total),
                "aggregate": float(a.aggregate), "status": a.status.value, "merit_list_no": a.merit_list_no, "merit_rank": a.merit_rank,
                "offer_expires_on": a.offer_expires_on, "remarks": a.remarks, "student_id": a.student_id}

    async def merit_list(self, round_id: uuid.UUID, list_no: int) -> dict:
        from app.programs.models import Program

        r = await self.get_round(round_id)
        rows = list((await self.session.execute(select(Applicant).where(Applicant.round_id == round_id, Applicant.merit_list_no == list_no)
                                                .order_by(Applicant.merit_rank))).scalars())
        if not rows:
            raise ResourceNotFoundError("That merit list does not exist.")
        p = await self.session.get(Program, r.program_id)
        return {"program": p.code, "program_name": p.name, "term_label": r.term_label, "list_no": list_no,
                "weights": {"matric": float(r.weight_matric), "inter": float(r.weight_inter), "test": float(r.weight_test)},
                "deadline": rows[0].offer_expires_on, "rows": [self.applicant_dict(a) for a in rows]}

    # ---------------------------------------------------------------- CSV
    async def import_csv(self, round_id: uuid.UUID, raw: bytes, *, actor_id: uuid.UUID, dry_run: bool) -> dict:
        """Each row is one application (template: CSV_COLUMNS). Each row is checked on its own; with dry_run nothing is saved.
        A row that fails is reported and skipped; the others are saved (applications are independent)."""
        import csv
        import io

        from app.core.exceptions import AppError

        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationAppError("Save the file as CSV UTF-8.") from None
        reader = csv.DictReader(io.StringIO(text))
        missing = [c for c in CSV_REQUIRED if c not in [h.strip() for h in (reader.fieldnames or [])]]
        if missing:
            raise ValidationAppError(f"Missing column(s): {', '.join(missing)}. Use the template.")
        saved, errors = 0, []
        for i, row in enumerate(reader, start=2):
            data = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            data = {k: v for k, v in data.items() if v != ""}
            for k in ACADEMIC:
                if k in data:
                    try:
                        data[k] = float(data[k])
                    except ValueError:
                        errors.append({"row": i, "message": f"{k}: not a number"})
                        data = None
                        break
            if data is None:
                continue
            try:
                async with self.session.begin_nested() as sp:
                    await self.apply(round_id, data, actor_id=actor_id)
                    if dry_run:
                        await sp.rollback()
                saved += 1
            except AppError as exc:
                errors.append({"row": i, "message": str(exc)})
        return {"dry_run": dry_run, "ok_rows": saved, "errors": errors}