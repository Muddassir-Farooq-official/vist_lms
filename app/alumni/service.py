"""Graduation lists, degree / transcript tracking, alumni details, directory and surveys (models.py)."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alumni.models import (AlumniProfile, AlumniSurvey, AlumniSurveyResponse, DocumentIssue, GraduationEntry, GraduationList)
from app.audit.service import AuditService
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

DOC_FLOW = {"REQUESTED": {"PREPARING", "CANCELLED"}, "PREPARING": {"READY", "CANCELLED"}, "READY": {"COLLECTED", "POSTED", "PREPARING"},
            "COLLECTED": set(), "POSTED": set(), "CANCELLED": set()}
DOC_NAMES = {"REQUESTED": "Requested", "PREPARING": "Being prepared", "READY": "Ready to collect", "COLLECTED": "Collected", "POSTED": "Posted",
             "CANCELLED": "Cancelled"}
PROFILE_FIELDS = ("employer", "job_title", "city", "country", "further_study", "email", "phone", "linkedin")


def _now():
    return datetime.now(timezone.utc)


class AlumniService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================ graduation lists
    async def _audit_one(self, student_id: uuid.UUID) -> tuple[list[str], float | None, int | None]:
        from app.records.service import RecordsService

        rs = RecordsService(self.session)
        a = await rs.degree_audit(student_id)
        c = await rs.clearance("STUDENT", student_id)
        problems = list(a["problems"]) + [p for p in c.get("problems", []) if p not in a["problems"]]
        from app.internship.service import graduation_problem

        intern = await graduation_problem(self.session, student_id)
        if intern:
            problems.append(intern)
        return problems, a.get("cgpa"), a.get("credits_passed")

    async def draw_up(self, *, program_id: uuid.UUID, title: str, graduation_date: date, actor_id: uuid.UUID) -> dict:
        from app.programs.models import Program
        from app.students.models import Student, StudentStatus

        if await self.session.get(Program, program_id) is None:
            raise ResourceNotFoundError("Program not found.")
        if (await self.session.execute(select(GraduationList.id).where(GraduationList.program_id == program_id, GraduationList.status == "DRAFT"))).first():
            raise BusinessRuleViolationError("This program already has a list being prepared. Finish or delete it first.")
        gl = GraduationList(title=(title or "").strip()[:120] or "Graduation list", program_id=program_id, graduation_date=graduation_date, created_by=actor_id)
        self.session.add(gl)
        await self.session.flush()
        students = (await self.session.execute(select(Student).where(Student.program_id == program_id, Student.status == StudentStatus.ACTIVE)
                                               .order_by(Student.roll_number))).scalars().all()
        for s in students:
            problems, cgpa, credits = await self._audit_one(s.id)
            self.session.add(GraduationEntry(list_id=gl.id, student_id=s.id, cgpa=cgpa, credits=credits, problems=problems, included=not problems))
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="graduation.list_drawn", entity_type="graduation_list", entity_id=gl.id,
                                             metadata={"program": str(program_id), "students": len(students)})
        return await self.list_detail(gl.id)

    async def lists(self) -> list[dict]:
        from app.programs.models import Program

        rows = (await self.session.execute(select(GraduationList, Program.code).join(Program, Program.id == GraduationList.program_id)
                                           .order_by(GraduationList.created_at.desc()).limit(100))).all()
        counts = dict((await self.session.execute(select(GraduationEntry.list_id, func.count()).where(GraduationEntry.included.is_(True))
                                                  .group_by(GraduationEntry.list_id))).all())
        return [{"id": g.id, "title": g.title, "program": code, "graduation_date": g.graduation_date, "status": g.status, "included": int(counts.get(g.id, 0))}
                for g, code in rows]

    async def list_detail(self, list_id: uuid.UUID) -> dict:
        from app.programs.models import Program
        from app.students.models import Student

        g = await self.session.get(GraduationList, list_id)
        if g is None:
            raise ResourceNotFoundError("List not found.")
        p = await self.session.get(Program, g.program_id)
        rows = (await self.session.execute(select(GraduationEntry, Student).join(Student, Student.id == GraduationEntry.student_id)
                                           .where(GraduationEntry.list_id == g.id).order_by(Student.roll_number))).all()
        return {"id": g.id, "title": g.title, "program": p.code if p else "", "program_name": p.name if p else "", "graduation_date": g.graduation_date,
                "status": g.status, "entries": [{"id": e.id, "student_id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}",
                                                  "father_name": getattr(s, "father_name", None), "cgpa": float(e.cgpa) if e.cgpa is not None else None,
                                                  "credits": e.credits, "problems": e.problems or [], "included": e.included, "result": e.result,
                                                  "note": e.result_note} for e, s in rows]}

    async def _draft(self, list_id: uuid.UUID) -> GraduationList:
        g = await self.session.get(GraduationList, list_id)
        if g is None:
            raise ResourceNotFoundError("List not found.")
        if g.status != "DRAFT":
            raise BusinessRuleViolationError("This list is already confirmed.")
        return g

    async def set_included(self, list_id: uuid.UUID, entry_id: uuid.UUID, *, included: bool, note: str | None) -> dict:
        await self._draft(list_id)
        e = await self.session.get(GraduationEntry, entry_id)
        if e is None or e.list_id != list_id:
            raise ResourceNotFoundError("Student not on this list.")
        if included and e.problems and len((note or "").strip()) < 10:
            raise ValidationAppError("This student is not clear (" + " ".join(e.problems) + "). Give a reason of at least 10 letters to include them anyway.")
        e.included = included
        e.result_note = (note or "").strip()[:300] or None if included else None
        await self.session.flush()
        return {"id": e.id, "included": e.included}

    async def refresh(self, list_id: uuid.UUID) -> dict:
        """Check every student again (after fees are collected or a result comes out)."""
        await self._draft(list_id)
        for e in (await self.session.execute(select(GraduationEntry).where(GraduationEntry.list_id == list_id))).scalars().all():
            problems, cgpa, credits = await self._audit_one(e.student_id)
            became_clear = not problems and e.problems
            e.problems, e.cgpa, e.credits = problems, cgpa, credits
            if became_clear:
                e.included = True
            if problems and e.included and not e.result_note:
                e.included = False
        await self.session.flush()
        return await self.list_detail(list_id)

    async def delete_draft(self, list_id: uuid.UUID) -> None:
        g = await self._draft(list_id)
        await self.session.delete(g)
        await self.session.flush()

    async def confirm(self, list_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        """Mark every ticked student graduated (the usual checks still apply: a student who fails one is skipped and says
        why) and give each an alumni login in place of their student one."""
        from app.students.models import StudentStatus
        from app.students.service import StudentService

        g = await self._draft(list_id)
        done, skipped = 0, 0
        entries = (await self.session.execute(select(GraduationEntry).where(GraduationEntry.list_id == g.id, GraduationEntry.included.is_(True)))).scalars().all()
        if not entries:
            raise BusinessRuleViolationError("Tick at least one student.")
        for e in entries:
            try:
                async with self.session.begin_nested():
                    await StudentService(self.session).change_status(e.student_id, new_status=StudentStatus.GRADUATED,
                                                                      reason=e.result_note or None, actor_id=actor_id)
                    await self.make_alumnus(e.student_id)
                e.result = "GRADUATED"
                done += 1
            except Exception as exc:                 # noqa: BLE001 - one student's problem never stops the rest
                e.result, e.result_note = "SKIPPED", str(getattr(exc, "message", None) or exc)[:300]
                skipped += 1
        g.status, g.confirmed_by, g.confirmed_at = "CONFIRMED", actor_id, _now()
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="graduation.confirmed", entity_type="graduation_list", entity_id=g.id,
                                             metadata={"graduated": done, "skipped": skipped})
        return {"graduated": done, "skipped": skipped, **(await self.list_detail(g.id))}

    async def make_alumnus(self, student_id: uuid.UUID) -> bool:
        """The graduate keeps their login: the student role is replaced by the alumni role. False if they have no login."""
        from app.auth.models import Role, User, UserRole
        from app.auth.permissions import Roles
        from app.students.models import Student, StudentStatus

        s = await self.session.get(Student, student_id)
        if s is None or s.status != StudentStatus.GRADUATED:
            raise BusinessRuleViolationError("Only a graduate can have an alumni login.")
        if await self.session.get(AlumniProfile, s.id) is None:
            self.session.add(AlumniProfile(student_id=s.id))
        if s.user_id is None:
            return False
        roles = {r.code: r.id for r in (await self.session.execute(select(Role).where(Role.code.in_([Roles.STUDENT, Roles.ALUMNUS])))).scalars()}
        if Roles.ALUMNUS not in roles:
            raise BusinessRuleViolationError("The alumni role does not exist yet. Run the permission seed script.")
        if Roles.STUDENT in roles:
            await self.session.execute(delete(UserRole).where(UserRole.user_id == s.user_id, UserRole.role_id == roles[Roles.STUDENT]))
        if await self.session.get(UserRole, (s.user_id, roles[Roles.ALUMNUS])) is None:
            self.session.add(UserRole(user_id=s.user_id, role_id=roles[Roles.ALUMNUS]))
        u = await self.session.get(User, s.user_id)
        u.is_active = True
        await self.session.flush()
        try:
            from app.core.dependencies import forget_session

            forget_session(s.user_id)
        except Exception:                            # noqa: BLE001 - the cache expires on its own
            pass
        return True

    async def list_pdf(self, list_id: uuid.UUID) -> bytes:
        from xml.sax.saxutils import escape

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        from app.core import brand

        d = await self.list_detail(list_id)
        rows = [e for e in d["entries"] if (e["result"] == "GRADUATED" if d["status"] == "CONFIRMED" else e["included"])]
        buf = io.BytesIO()
        width = A4[0] - 30 * mm
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=d["title"], author=brand.name())
        base = getSampleStyleSheet()
        body = ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica", fontSize=10, leading=13)
        head = ParagraphStyle("h", parent=body, fontName="Helvetica-Bold", fontSize=12.5, spaceAfter=4)
        story = brand.platypus_header(width) + [Spacer(1, 4), Paragraph(escape(d["title"]), head),
                                               Paragraph(f"{escape(d['program_name'])} ({escape(d['program'])}) · graduating {d['graduation_date']:%d %B %Y}" +
                                                         ("" if d["status"] == "CONFIRMED" else " · DRAFT"), body), Spacer(1, 6)]
        t = Table([["#", "Roll no.", "Name", "CGPA", "Credits"]] + [[str(i + 1), e["roll_number"] or "", e["name"], f"{e['cgpa']:.2f}" if e["cgpa"] is not None else "",
                                                                    str(e["credits"] or "")] for i, e in enumerate(rows)],
                  colWidths=[10 * mm, 35 * mm, width - 85 * mm, 20 * mm, 20 * mm], repeatRows=1)
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 9.5),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d0e0")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f9"))]))
        story += [t, Spacer(1, 30), Paragraph(f"{len(rows)} graduate(s).", body), Spacer(1, 30), Paragraph("Controller of Examinations &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Principal", body)]
        doc.build(story)
        return buf.getvalue()

    # ================================================================ degrees and transcripts
    async def open_document(self, *, student_id: uuid.UUID, kind: str, copies: int, note: str | None, actor_id: uuid.UUID, by_student: bool = False) -> dict:
        from app.students.models import Student, StudentStatus

        if kind not in ("DEGREE", "TRANSCRIPT"):
            raise ValidationAppError("Degree or transcript.")
        s = await self.session.get(Student, student_id)
        if s is None:
            raise ResourceNotFoundError("Student not found.")
        if kind == "DEGREE" and s.status != StudentStatus.GRADUATED:
            raise BusinessRuleViolationError("A degree is prepared once the student has graduated.")
        open_ = (await self.session.execute(select(DocumentIssue.id).where(DocumentIssue.student_id == s.id, DocumentIssue.kind == kind,
                                                                           DocumentIssue.status.in_(("REQUESTED", "PREPARING", "READY"))))).first()
        if open_:
            raise BusinessRuleViolationError(f"A {kind.lower()} for this student is already on its way.")
        if kind == "DEGREE" and (await self.session.execute(select(DocumentIssue.id).where(DocumentIssue.student_id == s.id, DocumentIssue.kind == "DEGREE",
                                                                                          DocumentIssue.status.in_(("COLLECTED", "POSTED"))))).first():
            raise BusinessRuleViolationError("This graduate's degree was already handed over. A duplicate is a separate application.")
        d = DocumentIssue(student_id=s.id, kind=kind, copies=max(1, min(copies or 1, 10)), note=(note or "").strip()[:300] or None, requested_by_student=by_student,
                          history=[{"status": "REQUESTED", "at": _now().isoformat(), "by": str(actor_id)}])
        self.session.add(d)
        await self.session.flush()
        return {"id": d.id, "status": d.status}

    async def advance(self, doc_id: uuid.UUID, *, status: str, serial_no: str | None, handed_to: str | None, note: str | None, actor_id: uuid.UUID) -> dict:
        d = await self.session.get(DocumentIssue, doc_id)
        if d is None:
            raise ResourceNotFoundError("Not found.")
        if status not in DOC_FLOW.get(d.status, set()):
            raise BusinessRuleViolationError(f"{DOC_NAMES[d.status]} cannot go to {DOC_NAMES.get(status, status).lower()}.")
        if status == "READY" and not (serial_no or d.serial_no):
            raise ValidationAppError("Give its serial number.")
        if status in ("COLLECTED", "POSTED") and len((handed_to or "").strip()) < 3:
            raise ValidationAppError("Say who collected it, or the courier and tracking number.")
        d.status = status
        if serial_no:
            d.serial_no = serial_no.strip()[:40]
        if handed_to:
            d.handed_to = handed_to.strip()[:120]
        if note:
            d.note = note.strip()[:300]
        d.history = (d.history or []) + [{"status": status, "at": _now().isoformat(), "by": str(actor_id)}]
        d.updated_at = _now()
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="documents.status", entity_type="document_issue", entity_id=d.id,
                                             metadata={"kind": d.kind, "status": status, "serial_no": d.serial_no})
        if status == "READY":
            await self._tell_ready(d)
        return {"id": d.id, "status": d.status}

    async def _tell_ready(self, d: DocumentIssue) -> None:
        try:
            async with self.session.begin_nested():
                from app.notifications.models import NotificationCategory
                from app.notifications.service import create_notification
                from app.students.models import Student

                s = await self.session.get(Student, d.student_id)
                if s and s.user_id:
                    await create_notification(self.session, user_id=s.user_id, category=NotificationCategory.GENERAL,
                                              title=f"Your {d.kind.lower()} is ready to collect", message="Bring your CNIC to the office.", link="/dashboard/alumni")
        except Exception:                          # noqa: BLE001
            pass

    async def documents(self, *, status: str | None = None, q: str | None = None) -> list[dict]:
        from app.programs.models import Program
        from app.students.models import Student

        stmt = (select(DocumentIssue, Student, Program.code).join(Student, Student.id == DocumentIssue.student_id).outerjoin(Program, Program.id == Student.program_id)
                .order_by(DocumentIssue.created_at.desc()).limit(500))
        if status == "OPEN":
            stmt = stmt.where(DocumentIssue.status.in_(("REQUESTED", "PREPARING", "READY")))
        elif status:
            stmt = stmt.where(DocumentIssue.status == status)
        if q and q.strip():
            n = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(Student.first_name + " " + Student.last_name).like(n) | func.lower(Student.roll_number).like(n))
        return [{"id": d.id, "student_id": s.id, "name": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number, "program": code, "kind": d.kind,
                 "status": d.status, "status_name": DOC_NAMES[d.status], "serial_no": d.serial_no, "copies": d.copies, "handed_to": d.handed_to, "note": d.note,
                 "by_student": d.requested_by_student, "created_at": d.created_at, "next": sorted(DOC_FLOW[d.status])} for d, s, code in (await self.session.execute(stmt)).all()]

    # ================================================================ the alumni portal
    async def me(self, student) -> dict:
        from app.programs.models import Program

        prof = await self.session.get(AlumniProfile, student.id) or AlumniProfile(student_id=student.id)
        p = await self.session.get(Program, student.program_id) if student.program_id else None
        docs = (await self.session.execute(select(DocumentIssue).where(DocumentIssue.student_id == student.id).order_by(DocumentIssue.created_at.desc()))).scalars().all()
        year = await self._grad_year(student.id)
        return {"name": f"{student.first_name} {student.last_name}", "roll_number": student.roll_number, "program": p.code if p else None,
                "program_name": p.name if p else None, "graduated": year,
                "profile": {**{f: getattr(prof, f) for f in PROFILE_FIELDS}, "in_directory": bool(prof.in_directory), "share_contact": bool(prof.share_contact)},
                "documents": [{"id": d.id, "kind": d.kind, "status": d.status, "status_name": DOC_NAMES[d.status], "serial_no": d.serial_no,
                               "created_at": d.created_at} for d in docs]}

    async def _grad_year(self, student_id) -> int | None:
        from app.students.models import StudentStatus, StudentStatusHistory

        at = (await self.session.execute(select(func.max(StudentStatusHistory.changed_at)).where(StudentStatusHistory.student_id == student_id,
                                                                                               StudentStatusHistory.new_status == StudentStatus.GRADUATED))).scalar_one()
        return at.year if at else None

    async def save_profile(self, student, data: dict) -> dict:
        prof = await self.session.get(AlumniProfile, student.id)
        if prof is None:
            prof = AlumniProfile(student_id=student.id)
            self.session.add(prof)
        for f in PROFILE_FIELDS:
            if f in data:
                v = (data[f] or "").strip() or None
                setattr(prof, f, v)
        prof.in_directory = bool(data.get("in_directory", prof.in_directory))
        prof.share_contact = bool(data.get("share_contact", prof.share_contact)) and prof.in_directory
        prof.updated_at = _now()
        await self.session.flush()
        return await self.me(student)

    async def directory(self, *, q: str | None = None, program_id: uuid.UUID | None = None, everyone: bool = False) -> list[dict]:
        """Alumni who chose to be listed (the office sees everyone, with contact details)."""
        from app.programs.models import Program
        from app.students.models import Student, StudentStatus

        stmt = (select(Student, AlumniProfile, Program.code).outerjoin(AlumniProfile, AlumniProfile.student_id == Student.id)
                .outerjoin(Program, Program.id == Student.program_id).where(Student.status == StudentStatus.GRADUATED).order_by(Student.first_name).limit(2000))
        if not everyone:
            stmt = stmt.where(AlumniProfile.in_directory.is_(True))
        if program_id:
            stmt = stmt.where(Student.program_id == program_id)
        if q and q.strip():
            n = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(Student.first_name + " " + Student.last_name).like(n) | func.lower(func.coalesce(AlumniProfile.employer, "")).like(n) |
                              func.lower(func.coalesce(AlumniProfile.city, "")).like(n))
        rows = (await self.session.execute(stmt)).all()
        years = {}
        if rows:
            from app.students.models import StudentStatusHistory

            years = {sid: at.year for sid, at in (await self.session.execute(
                select(StudentStatusHistory.student_id, func.max(StudentStatusHistory.changed_at)).where(
                    StudentStatusHistory.student_id.in_([s.id for s, _p, _c in rows]), StudentStatusHistory.new_status == StudentStatus.GRADUATED)
                .group_by(StudentStatusHistory.student_id))).all() if at}
        out = []
        for s, p, code in rows:
            contact = everyone or bool(p and p.share_contact)
            out.append({"student_id": s.id, "name": f"{s.first_name} {s.last_name}", "program": code, "year": years.get(s.id),
                        "employer": p.employer if p else None, "job_title": p.job_title if p else None, "city": p.city if p else None,
                        "country": p.country if p else None, "further_study": p.further_study if p else None,
                        "email": (p.email if p else None) if contact else None, "phone": (p.phone if p else None) if contact else None,
                        "linkedin": (p.linkedin if p else None) if contact else None,
                        **({"roll_number": s.roll_number, "in_directory": bool(p and p.in_directory), "has_login": s.user_id is not None} if everyone else {})})
        return out

    @staticmethod
    def directory_csv(rows: list[dict]) -> str:
        buf = io.StringIO()
        cols = ["roll_number", "name", "program", "year", "employer", "job_title", "city", "country", "further_study", "email", "phone", "linkedin"]
        w = csv.writer(buf)
        w.writerow([c.replace("_", " ").title() for c in cols])
        for r in rows:
            w.writerow([("'" + str(r.get(c))) if str(r.get(c) or "").startswith(("=", "+", "-", "@")) else (r.get(c) if r.get(c) is not None else "") for c in cols])
        return buf.getvalue()

    # ================================================================ surveys
    @staticmethod
    def _clean_questions(qs: list[dict]) -> list[dict]:
        out = []
        for i, q in enumerate(qs or []):
            text = (q.get("text") or "").strip()
            kind = str(q.get("kind") or "").upper()
            if len(text) < 3 or kind not in ("RATING", "CHOICE", "TEXT"):
                raise ValidationAppError(f"Question {i + 1}: give its text and kind (rating 1-5, choice or text).")
            choices = [str(c).strip() for c in (q.get("choices") or []) if str(c).strip()]
            if kind == "CHOICE" and not (2 <= len(choices) <= 10):
                raise ValidationAppError(f"Question {i + 1}: a choice needs 2 to 10 options.")
            out.append({"id": f"q{i + 1}", "text": text[:300], "kind": kind, **({"choices": choices} if kind == "CHOICE" else {})})
        if not out:
            raise ValidationAppError("Add at least one question.")
        return out

    async def save_survey(self, data: dict, *, survey_id: uuid.UUID | None, actor_id: uuid.UUID) -> dict:
        title = (data.get("title") or "").strip()
        if len(title) < 3:
            raise ValidationAppError("Give the survey a title.")
        sv = await self.session.get(AlumniSurvey, survey_id) if survey_id else None
        if survey_id and sv is None:
            raise ResourceNotFoundError("Survey not found.")
        if sv is not None and (await self.session.execute(select(AlumniSurveyResponse.id).where(AlumniSurveyResponse.survey_id == sv.id))).first() \
                and "questions" in data:
            raise BusinessRuleViolationError("Alumni have answered this survey; its questions stay as they are.")
        if sv is None:
            sv = AlumniSurvey(title=title, questions=self._clean_questions(data.get("questions")), created_by=actor_id)
            self.session.add(sv)
        else:
            sv.title = title
            if "questions" in data:
                sv.questions = self._clean_questions(data["questions"])
        sv.intro = (data.get("intro") or "").strip() or None
        if "is_open" in data:
            was = sv.is_open
            sv.is_open = bool(data["is_open"])
            await self.session.flush()
            if sv.is_open and not was:
                await self._tell_alumni(sv)
        await self.session.flush()
        return {"id": sv.id, "is_open": sv.is_open}

    async def _tell_alumni(self, sv: AlumniSurvey) -> None:
        try:
            async with self.session.begin_nested():
                from app.auth.models import Role, UserRole
                from app.auth.permissions import Roles
                from app.notifications.models import NotificationCategory
                from app.notifications.service import create_notification

                users = (await self.session.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.ALUMNUS))).scalars().all()
                for uid in users:
                    await create_notification(self.session, user_id=uid, category=NotificationCategory.GENERAL, title=f"Alumni survey: {sv.title}",
                                              message="A few minutes of your time helps the institute.", link="/dashboard/alumni#survey")
        except Exception:                          # noqa: BLE001
            pass

    async def surveys(self, *, open_only: bool = False, student_id: uuid.UUID | None = None) -> list[dict]:
        q = select(AlumniSurvey).order_by(AlumniSurvey.created_at.desc())
        if open_only:
            q = q.where(AlumniSurvey.is_open.is_(True))
        rows = (await self.session.execute(q)).scalars().all()
        counts = dict((await self.session.execute(select(AlumniSurveyResponse.survey_id, func.count()).group_by(AlumniSurveyResponse.survey_id))).all())
        answered = set()
        if student_id:
            answered = set((await self.session.execute(select(AlumniSurveyResponse.survey_id).where(AlumniSurveyResponse.student_id == student_id))).scalars())
        return [{"id": s.id, "title": s.title, "intro": s.intro, "questions": s.questions, "is_open": s.is_open, "responses": int(counts.get(s.id, 0)),
                 "answered": s.id in answered} for s in rows]

    async def answer(self, student, survey_id: uuid.UUID, answers: dict) -> dict:
        sv = await self.session.get(AlumniSurvey, survey_id)
        if sv is None or not sv.is_open:
            raise BusinessRuleViolationError("This survey is closed.")
        if (await self.session.execute(select(AlumniSurveyResponse.id).where(AlumniSurveyResponse.survey_id == sv.id,
                                                                             AlumniSurveyResponse.student_id == student.id))).first():
            raise BusinessRuleViolationError("You have already answered this survey. Thank you!")
        clean = {}
        for q in sv.questions:
            v = answers.get(q["id"])
            if v in (None, ""):
                continue
            if q["kind"] == "RATING":
                if not (isinstance(v, (int, float)) and 1 <= int(v) <= 5):
                    raise ValidationAppError(f"“{q['text']}”: a rating from 1 to 5.")
                clean[q["id"]] = int(v)
            elif q["kind"] == "CHOICE":
                if v not in q["choices"]:
                    raise ValidationAppError(f"“{q['text']}”: choose one of the options.")
                clean[q["id"]] = v
            else:
                clean[q["id"]] = str(v).strip()[:2000]
        if not clean:
            raise ValidationAppError("Answer at least one question.")
        self.session.add(AlumniSurveyResponse(survey_id=sv.id, student_id=student.id, answers=clean))
        await self.session.flush()
        return {"answered": True}

    async def results(self, survey_id: uuid.UUID) -> dict:
        sv = await self.session.get(AlumniSurvey, survey_id)
        if sv is None:
            raise ResourceNotFoundError("Survey not found.")
        rows = (await self.session.execute(select(AlumniSurveyResponse.answers).where(AlumniSurveyResponse.survey_id == sv.id))).scalars().all()
        out = []
        for q in sv.questions:
            vals = [r.get(q["id"]) for r in rows if r.get(q["id"]) not in (None, "")]
            item = {"id": q["id"], "text": q["text"], "kind": q["kind"], "answers": len(vals)}
            if q["kind"] == "RATING":
                item["average"] = round(sum(vals) / len(vals), 2) if vals else None
                item["counts"] = {str(k): vals.count(k) for k in range(1, 6)}
            elif q["kind"] == "CHOICE":
                item["counts"] = {c: vals.count(c) for c in q["choices"]}
            else:
                item["texts"] = vals[:500]
            out.append(item)
        return {"id": sv.id, "title": sv.title, "responses": len(rows), "questions": out}

    async def assert_alumnus(self, user):
        from app.students.models import Student, StudentStatus

        s = (await self.session.execute(select(Student).where(Student.user_id == user.id))).scalar_one_or_none()
        if s is None or s.status != StudentStatus.GRADUATED:
            raise AuthorizationError("This is for graduates.")
        return s
