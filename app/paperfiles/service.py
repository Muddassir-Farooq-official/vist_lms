"""Exam paper files (models.py)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.paperfiles.models import PaperFile

ALLOWED = {"application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
STATUS_NAMES = {"SENT": "Sent", "PRINTED": "Printed", "RETURNED": "Returned", "REPLACED": "Replaced"}


def _now():
    return datetime.now(timezone.utc)


class PaperFileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _employee_id(self, user) -> uuid.UUID | None:
        from app.employees.models import Employee

        return (await self.session.execute(select(Employee.id).where(Employee.user_id == user.id))).scalar_one_or_none()

    async def _paper(self, paper_id: uuid.UUID):
        from app.academics.models import CourseOffering
        from app.exams.models import ExamPaper, ExamSchedule

        row = (await self.session.execute(select(ExamPaper, ExamSchedule, CourseOffering).join(ExamSchedule, ExamSchedule.id == ExamPaper.schedule_id)
                                          .join(CourseOffering, CourseOffering.id == ExamPaper.course_offering_id).where(ExamPaper.id == paper_id))).first()
        if row is None:
            raise ResourceNotFoundError("Paper not found.")
        return row

    async def _may_send(self, user, offering, *, office: bool) -> None:
        if office:
            return
        emp = await self._employee_id(user)
        if emp is None or offering.assigned_teacher_id != emp:
            raise AuthorizationError("Only the course's teacher, or the exam office, can send this paper.")

    async def _latest(self, paper_ids: list) -> dict:
        if not paper_ids:
            return {}
        rows = (await self.session.execute(select(PaperFile).where(PaperFile.exam_paper_id.in_(paper_ids)).order_by(PaperFile.exam_paper_id,
                                                                                                                 PaperFile.version.desc()))).scalars().all()
        out = {}
        for f in rows:
            out.setdefault(f.exam_paper_id, f)
        return out

    @staticmethod
    def _file(f: PaperFile | None) -> dict | None:
        if f is None:
            return None
        return {"id": f.id, "version": f.version, "file_name": f.file_name, "size_bytes": f.size_bytes, "note": f.note, "status": f.status,
                "status_name": STATUS_NAMES[f.status], "uploaded_at": f.uploaded_at, "decided_at": f.decided_at, "return_note": f.return_note}

    async def _rows(self, where, *, today: date) -> list[dict]:
        from app.academics.models import Course, CourseOffering
        from app.employees.models import Employee
        from app.exams.models import ExamPaper, ExamSchedule

        q = (select(ExamPaper, ExamSchedule, CourseOffering, Course, Employee).join(ExamSchedule, ExamSchedule.id == ExamPaper.schedule_id)
             .join(CourseOffering, CourseOffering.id == ExamPaper.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
             .outerjoin(Employee, Employee.id == CourseOffering.assigned_teacher_id).where(*where).order_by(ExamPaper.exam_date, ExamPaper.start_time, Course.code))
        rows = (await self.session.execute(q)).all()
        latest = await self._latest([p.id for p, *_ in rows])
        return [{"paper_id": p.id, "schedule": f"{s.name} {s.term_label}", "schedule_id": s.id, "draft": s.status.value == "DRAFT",
                 "course": f"{c.code} {c.title}", "section": getattr(o, "section", None), "exam_date": p.exam_date, "start_time": p.start_time.strftime("%H:%M"),
                 "days_left": (p.exam_date - today).days, "teacher": f"{e.first_name} {e.last_name}" if e else None, "file": self._file(latest.get(p.id))}
                for p, s, o, c, e in rows]

    # ------------------------------------------------------------------ the teacher
    async def mine(self, user) -> list[dict]:
        from app.academics.models import CourseOffering
        from app.core.clock import institute_today
        from app.exams.models import ExamPaper

        emp = await self._employee_id(user)
        if emp is None:
            return []
        today = institute_today()
        return await self._rows([CourseOffering.assigned_teacher_id == emp, ExamPaper.exam_date >= today], today=today)

    async def send(self, paper_id: uuid.UUID, *, content: bytes, file_name: str, content_type: str | None, note: str | None, user, office: bool) -> dict:
        from app.core.storage import FileTooLargeError, UnsupportedFileTypeError, save_attachment, validate_attachment

        paper, schedule, offering = await self._paper(paper_id)
        await self._may_send(user, offering, office=office)
        try:
            ctype, _ext = validate_attachment(content=content, original_filename=file_name, declared_content_type=content_type)
        except (UnsupportedFileTypeError, FileTooLargeError) as exc:
            raise ValidationAppError(str(exc)) from exc
        if ctype not in ALLOWED:
            raise ValidationAppError("Send the paper as a PDF or Word file.")
        latest = (await self._latest([paper.id])).get(paper.id)
        if latest is not None and latest.status == "PRINTED" and not office:
            raise BusinessRuleViolationError("This paper is already printed. Ask the exam office if it must change.")
        key, name, ctype = save_attachment(entity_id=paper.id, content=content, original_filename=file_name, declared_content_type=ctype, folder="exam-papers")
        if latest is not None and latest.status in ("SENT", "PRINTED"):
            latest.status = "REPLACED"
        f = PaperFile(exam_paper_id=paper.id, version=(latest.version + 1) if latest else 1, storage_key=key, file_name=name, content_type=ctype,
                      size_bytes=len(content), note=(note or "").strip()[:500] or None, uploaded_by=user.id)
        self.session.add(f)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=user.id, action="exam_paper_file.sent", entity_type="exam_paper", entity_id=paper.id,
                                             metadata={"version": f.version, "file": name, "size": len(content)})
        return self._file(f)

    # ------------------------------------------------------------------ the exam office
    async def schedules(self) -> list[dict]:
        from app.exams.models import ExamPaper, ExamSchedule

        rows = (await self.session.execute(select(ExamSchedule, func.count(ExamPaper.id), func.min(ExamPaper.exam_date)).outerjoin(ExamPaper, ExamPaper.schedule_id == ExamSchedule.id)
                                           .group_by(ExamSchedule.id).order_by(func.min(ExamPaper.exam_date).desc().nulls_last()).limit(60))).all()
        return [{"id": s.id, "name": f"{s.name} {s.term_label}", "status": s.status.value, "papers": int(n), "starts": first} for s, n, first in rows]

    async def overview(self, schedule_id: uuid.UUID) -> dict:
        from app.core.clock import institute_today
        from app.exams.models import ExamPaper

        rows = await self._rows([ExamPaper.schedule_id == schedule_id], today=institute_today())
        count = {"missing": 0, "SENT": 0, "PRINTED": 0, "RETURNED": 0}
        for r in rows:
            count["missing" if r["file"] is None else r["file"]["status"]] = count.get("missing" if r["file"] is None else r["file"]["status"], 0) + 1
        return {"papers": rows, "count": count}

    async def history(self, paper_id: uuid.UUID, user, *, office: bool) -> list[dict]:
        _p, _s, offering = await self._paper(paper_id)
        await self._may_send(user, offering, office=office)
        rows = (await self.session.execute(select(PaperFile).where(PaperFile.exam_paper_id == paper_id).order_by(PaperFile.version.desc()))).scalars().all()
        return [self._file(f) for f in rows]

    async def decide(self, file_id: uuid.UUID, *, printed: bool, note: str | None, actor_id: uuid.UUID) -> dict:
        f = await self.session.get(PaperFile, file_id)
        if f is None:
            raise ResourceNotFoundError("File not found.")
        if f.status != "SENT":
            raise BusinessRuleViolationError("Only the latest file sent can be printed or returned.")
        if not printed and len((note or "").strip()) < 5:
            raise ValidationAppError("Say what must be corrected (the teacher sees this).")
        f.status, f.decided_by, f.decided_at = ("PRINTED" if printed else "RETURNED"), actor_id, _now()
        f.return_note = None if printed else note.strip()[:500]
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="exam_paper_file." + ("printed" if printed else "returned"), entity_type="exam_paper",
                                             entity_id=f.exam_paper_id, metadata={"version": f.version, "note": f.return_note})
        await self._tell_teacher(f)
        return self._file(f)

    async def _tell_teacher(self, f: PaperFile) -> None:
        """Never raises."""
        try:
            async with self.session.begin_nested():
                from app.academics.models import Course
                from app.notifications.models import NotificationCategory
                from app.notifications.service import create_notification

                paper, _s, offering = await self._paper(f.exam_paper_id)
                c = await self.session.get(Course, offering.course_id)
                if f.uploaded_by:
                    title = (f"Your {c.code} paper was printed" if f.status == "PRINTED" else f"Your {c.code} paper was returned: please correct and send again")
                    await create_notification(self.session, user_id=f.uploaded_by, category=NotificationCategory.GENERAL, title=title,
                                              message=f.return_note or f"Exam on {paper.exam_date:%d %b}.", link="/dashboard/faculty/exam-papers")
        except Exception:                         # noqa: BLE001
            pass

    async def file_for_download(self, file_id: uuid.UUID, user, *, office: bool) -> PaperFile:
        f = await self.session.get(PaperFile, file_id)
        if f is None:
            raise ResourceNotFoundError("File not found.")
        _p, _s, offering = await self._paper(f.exam_paper_id)
        await self._may_send(user, offering, office=office)
        if office:
            await AuditService(self.session).log(actor_user_id=user.id, action="exam_paper_file.downloaded", entity_type="exam_paper",
                                                 entity_id=f.exam_paper_id, metadata={"version": f.version})
        return f
