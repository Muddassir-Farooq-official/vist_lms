"""Getting people going and keeping them on track (owner decisions, 1.69.0).

  * setup(): the administrator's "Set up your LMS" checklist, in order. Each step is done when the data says so (terms,
    programs, courses, staff, students, sections with teachers, periods and rooms, timetable, fees, salaries).
  * me(): what this person still has to do: their own profile (only what they can do themselves: a photo, contact
    details, guardian / emergency contact, blood group, CNIC and certificate copies), phone notifications, and for
    teachers today's classes without attendance, marks still missing and work to check; for students fees due,
    assignments due and unread important notices; for parents their children's fees due.
    A 7-day grace period starts the first time someone is reminded (users.nudge_started_on). After it, while their
    profile is still incomplete, must_finish is true: their profile page opens first after sign-in (the menu still
    works). Something sent to the office for approval (a photo, a contact change, a document) counts as done by them.
  * page_help(): the manual's own words for a page (its menu label), for the guided tours.
Read only, except nudge_started_on.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.permissions import Roles
from app.core.clock import institute_now, institute_today

GRACE_DAYS = 7
PROFILE_LINK = "/dashboard/my-profile"
# completeness keys a person can complete themselves, and the self-service fields that complete them
SELF_KEYS = {
    "photo": None, "contact_no": {"contact_no"}, "email": {"email"}, "address": {"address_line"},
    "emergency": {"emergency_contact_no", "emergency_contact_name"}, "blood_group": {"blood_group"},
    "guardian": {"guardian_contact_no"}, "marital_status": {"marital_status"},
    "bank": {"bank_name", "bank_account_title", "bank_iban"}, "qualifications": None,
    "doc:CNIC": None, "doc:MATRIC": None, "doc:INTERMEDIATE": None, "doc:DOMICILE": None, "doc:DEGREE": None,
}
HOW = {"photo": "Send a clear photo of your face", "doc:CNIC": "Upload a copy", "doc:MATRIC": "Upload a copy", "doc:INTERMEDIATE": "Upload a copy",
       "doc:DOMICILE": "Upload a copy", "doc:DEGREE": "Upload your degree or certificates", "qualifications": "Add your qualifications",
       "bank": "Add your bank details for salary"}


class OnboardingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ the administrator's checklist
    async def setup(self) -> dict:
        from app.academics.models import Course, CourseOffering, CourseProgram, Period, TimetableSlot
        from app.academics.rooms import Room
        from app.academics.terms import AcademicTerm
        from app.employees.models import Employee, EmployeeSalaryHistory, EmployeeStatus
        from app.fees.models import FeeStructure
        from app.programs.models import Program
        from app.students.models import Student

        async def count(model, *where):
            return int((await self.session.execute(select(func.count()).select_from(model).where(*where))).scalar_one())

        offerings = await count(CourseOffering)
        no_teacher = await count(CourseOffering, CourseOffering.assigned_teacher_id.is_(None),
                                 (CourseOffering.term_end_date.is_(None)) | (CourseOffering.term_end_date >= institute_today()))
        staff = await count(Employee, Employee.status != EmployeeStatus.TERMINATED)
        A = "/dashboard/admin"
        steps = [
            ("institute", "Institute details", "Name, logo, address and the kind of institute (degree-awarding, affiliated college or school).",
             await self._branding_set(), f"{A}/settings", "Settings"),
            ("programs", "Programs", "BS Nursing, BS Anesthesia... with their years and semesters.", await count(Program) > 0, f"{A}/programs", "Programs"),
            ("terms", "Academic terms", "One term per batch (e.g. Fall 2026 BSN 1st) with start and end dates, grouped into a session.",
             await count(AcademicTerm) > 0, f"{A}/courses#terms", "Courses"),
            ("courses", "Courses and curriculum", "The subjects, and which semester of which program each belongs to.",
             await count(Course) > 0 and await count(CourseProgram) > 0, f"{A}/courses", "Courses"),
            ("staff", "Staff", "Add or import teachers and other staff (Employees → Import), with their job and login.",
             staff > 1, f"{A}/employees", "Employees"),
            ("salaries", "Salaries", "Each person's basic salary, or a rate per lecture for visiting faculty.",
             await count(EmployeeSalaryHistory) > 0, f"{A}/employees", "Employees"),
            ("students", "Students", "Add or import students (CSV Imports) into their program and semester.", await count(Student) > 0,
             f"{A}/imports", "CSV Imports"),
            ("sections", "Course sections with teachers", "Open each course for each batch term and give it a teacher.",
             offerings > 0 and no_teacher == 0, f"{A}/courses#offerings", "Courses"),
            ("periods", "Periods and rooms", "The periods of the day and every classroom and lab with its seats.",
             await count(Period) > 0 and await count(Room) > 0, f"{A}/timetable#periods", "Timetable"),
            ("timetable", "Timetable", "Make the weekly timetable with the Timetable wizard, then publish it.", await count(TimetableSlot) > 0,
             f"{A}/timetable", "Timetable"),
            ("fees", "Fees", "Fee structures for each program and term, then assign them to the students.", await count(FeeStructure) > 0,
             f"{A}/fees", "Fees"),
        ]
        out = [{"key": k, "title": t, "text": x, "done": bool(d), "link": link, "page": page} for k, t, x, d, link, page in steps]
        if no_teacher and offerings:
            out[7]["text"] += f" {no_teacher} running section{'s' if no_teacher != 1 else ''} still without a teacher."
        return {"steps": out, "done": sum(1 for s in out if s["done"]), "total": len(out)}

    async def _branding_set(self) -> bool:
        """The institute's own name and address have been saved (Settings → Institute)."""
        from app.branding.models import InstituteProfile

        p = await self.session.get(InstituteProfile, "main")
        return p is not None and bool((p.name or "").strip()) and bool((p.address or "").strip() or (p.city or "").strip())

    # ------------------------------------------------------------------ what I still have to do
    async def me(self, user_id: uuid.UUID, roles: list[str]) -> dict:
        user = await self.session.get(User, user_id)
        roles = list(roles or [])
        todo: list[dict] = []
        profile = await self._profile(user, roles)

        if not await self._has_push(user_id) and Roles.ADMIN not in roles:
            todo.append({"key": "push", "title": "Switch on phone notifications", "text": "Get a message on your phone for classes, fees and notices.",
                         "link": "#push", "count": 1})
        if Roles.TEACHER in roles or Roles.HOD in roles:
            todo += await self._teacher(user_id)
        if Roles.STUDENT in roles:
            todo += await self._student(user_id, roles)
        if Roles.PARENT in roles:
            todo += await self._parent(user_id)

        must_finish, days_left = False, None
        if profile and profile["missing"]:
            today = institute_today()
            if user.nudge_started_on is None:
                user.nudge_started_on = today
                await self.session.flush()
            days_left = max(0, GRACE_DAYS - (today - user.nudge_started_on).days)
            must_finish = days_left == 0
        return {"profile": profile, "todo": todo, "grace_days_left": days_left, "must_finish": must_finish}

    async def _has_push(self, user_id: uuid.UUID) -> bool:
        from app.push.models import PushSubscription

        return (await self.session.execute(select(func.count()).select_from(PushSubscription).where(PushSubscription.user_id == user_id))).scalar_one() > 0

    async def _profile(self, user: User, roles: list[str]) -> dict | None:
        """The profile items this person can complete themselves, and which are still missing."""
        from app.records.completeness import EMPLOYEE_ITEMS, STUDENT_ITEMS, completeness
        from app.records.models import PersonDocument
        from app.selfservice.models import ProfileChangeRequest
        from app.students.models import Student

        student = (await self.session.execute(select(Student).where(Student.user_id == user.id))).scalar_one_or_none()
        if student is not None:
            kind, owner, items = "STUDENT", student.id, STUDENT_ITEMS
        else:
            from app.employees.models import Employee

            emp = (await self.session.execute(select(Employee).where(Employee.user_id == user.id))).scalar_one_or_none()
            if emp is None or roles == [Roles.ADMIN]:
                return None
            kind, owner, items = "EMPLOYEE", emp.id, EMPLOYEE_ITEMS
        c = (await completeness(self.session, kind, [owner])).get(owner)
        if c is None:
            return None
        missing_labels = set(c["missing"])
        pending = list((await self.session.execute(select(ProfileChangeRequest).where(
            ProfileChangeRequest.subject_type == kind, ProfileChangeRequest.subject_id == owner, ProfileChangeRequest.status == "PENDING"))).scalars())
        asked = set().union(*[set((p.changes or {}).keys()) for p in pending]) if pending else set()
        photo_sent = any(p.photo_storage_key for p in pending)
        out, missing = [], []
        for key, label in items:
            if key not in SELF_KEYS:
                continue                       # the office provides it (e.g. the appointment letter)
            done = label not in missing_labels
            waiting = (not done) and ((key == "photo" and photo_sent) or (SELF_KEYS[key] is not None and SELF_KEYS[key] & asked))
            out.append({"key": key, "label": label, "done": done or bool(waiting), "waiting": bool(waiting), "how": HOW.get(key, "Fill it in")})
            if not done and not waiting:
                missing.append(label)
        # documents uploaded and waiting to be checked are already on file (completeness counts them)
        _ = PersonDocument
        return {"items": out, "missing": missing, "done": sum(1 for x in out if x["done"]), "total": len(out),
                "percent": round(100 * sum(1 for x in out if x["done"]) / len(out)) if out else 100, "link": PROFILE_LINK}

    async def _teacher(self, user_id: uuid.UUID) -> list[dict]:
        from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering, ResultSheet, ResultSheetStatus
        from app.academics.result_sheet_service import ResultSheetService
        from app.assignments.models import Assignment, AssignmentSubmission, SubmissionStatus
        from app.employees.models import Employee

        emp = (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalar_one_or_none()
        if emp is None:
            return []
        out = []
        now = institute_now()
        rows = (await self.session.execute(select(ClassSession, Course).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
                                           .join(Course, Course.id == CourseOffering.course_id).where(
            CourseOffering.assigned_teacher_id == emp.id, ClassSession.session_date == now.date(), ClassSession.status == ClassSessionStatus.SCHEDULED,
            ClassSession.start_time <= now.time(), ClassSession.attendance_taken_at.is_(None)).order_by(ClassSession.start_time))).all()
        if rows:
            names = ", ".join(sorted({c.code for _s, c in rows}))
            out.append({"key": "attendance", "title": f"Take attendance: {len(rows)} class{'es' if len(rows) != 1 else ''} today",
                        "text": f"{names}. Mark it within the time allowed after the start, or ask the office.", "link": "/dashboard/faculty/attendance",
                        "count": len(rows)})
        # marks still missing on courses ending within three weeks (sheet not yet sent for approval)
        today = institute_today()
        offs = list((await self.session.execute(select(CourseOffering).where(
            CourseOffering.assigned_teacher_id == emp.id, CourseOffering.term_end_date.is_not(None),
            CourseOffering.term_end_date >= today - timedelta(days=30), CourseOffering.term_end_date <= today + timedelta(days=21)))).scalars())
        svc = ResultSheetService(self.session)
        missing = 0
        for o in offs:
            st = (await self.session.execute(select(ResultSheet.status).where(ResultSheet.course_offering_id == o.id))).scalar_one_or_none()
            if st not in (None, ResultSheetStatus.DRAFT, ResultSheetStatus.RETURNED):
                continue
            c = await svc.completeness(o.id)
            if c["missing_students"]:
                missing += 1
        if missing:
            out.append({"key": "marks", "title": f"Enter marks: {missing} course{'s' if missing != 1 else ''} ending soon",
                        "text": "Every student needs every mark (0 for an absentee) before the result can be sent for approval.",
                        "link": "/dashboard/faculty/grading", "count": missing})
        waiting = int((await self.session.execute(select(func.count()).select_from(AssignmentSubmission).join(
            Assignment, Assignment.id == AssignmentSubmission.assignment_id).join(CourseOffering, CourseOffering.id == Assignment.course_offering_id).where(
            CourseOffering.assigned_teacher_id == emp.id, AssignmentSubmission.status == SubmissionStatus.SUBMITTED))).scalar_one())
        if waiting:
            out.append({"key": "assignments", "title": f"Check work: {waiting} submission{'s' if waiting != 1 else ''} waiting",
                        "text": "Accept the work or ask for changes, with feedback.", "link": "/dashboard/faculty/assignments", "count": waiting})
        return out

    async def _fees(self, student_id: uuid.UUID, link: str, who: str = "") -> list[dict]:
        from app.fees.account_service import AccountService
        from app.fees.models import PayerType

        try:
            s = (await AccountService(self.session).build(PayerType.STUDENT, student_id, staff_view=False))["summary"]
        except Exception:                                     # noqa: BLE001 - no fee account: nothing due
            return []
        if s.get("overdue"):
            return [{"key": "fees", "title": f"Fees overdue{who}: Rs. {s['overdue']:,.0f}", "text": "Pay to avoid a late fee and a hold on your result card.",
                     "link": link, "count": 1}]
        nxt = s.get("next_due")
        if nxt and nxt.get("date") and nxt["date"] <= institute_today() + timedelta(days=7):
            return [{"key": "fees", "title": f"Fee due{who} on {nxt['date']:%d %b}: Rs. {nxt['amount']:,.0f}", "text": "Download the challan and pay before the due date.",
                     "link": link, "count": 1}]
        return []

    async def _student(self, user_id: uuid.UUID, roles: list[str]) -> list[dict]:
        from app.academics.models import CourseOffering, Enrollment
        from app.academics.repository import attending_filter
        from app.announcements.models import AnnouncementPriority
        from app.announcements.service import AnnouncementService
        from app.assignments.models import Assignment, AssignmentSubmission
        from app.students.models import Student

        s = (await self.session.execute(select(Student).where(Student.user_id == user_id))).scalar_one_or_none()
        if s is None:
            return []
        out = await self._fees(s.id, "/dashboard/student/fees")
        now = datetime.now(timezone.utc)
        mine = select(Enrollment.course_offering_id).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id).where(
            Enrollment.student_id == s.id, attending_filter(institute_today()))
        done = select(AssignmentSubmission.assignment_id).where(AssignmentSubmission.student_id == s.id)
        due = int((await self.session.execute(select(func.count()).select_from(Assignment).where(
            Assignment.course_offering_id.in_(mine), Assignment.deadline > now, Assignment.deadline <= now + timedelta(days=3),
            Assignment.id.not_in(done)))).scalar_one())
        if due:
            out.append({"key": "assignments", "title": f"Assignments due: {due} in the next 3 days", "text": "Submit your work before the deadline.",
                        "link": "/dashboard/student/assignments", "count": due})
        try:
            svc = AnnouncementService(self.session)
            viewer = await svc.viewer_for(user_id, roles)
            read = await svc.read_ids(user_id)
            unread = [a for a in await svc.visible_to(viewer) if a.priority != AnnouncementPriority.NORMAL and a.id not in read]
        except Exception:                                     # noqa: BLE001 - notices are a nice-to-have here
            unread = []
        if unread:
            out.append({"key": "notices", "title": f"Important notices: {len(unread)} unread", "text": unread[0].title[:120],
                        "link": "/dashboard/student/announcements", "count": len(unread)})
        return out

    async def _parent(self, user_id: uuid.UUID) -> list[dict]:
        from app.parents.models import ParentStudentLink
        from app.students.models import Student

        out = []
        for s in (await self.session.execute(select(Student).join(ParentStudentLink, ParentStudentLink.student_id == Student.id).where(
                ParentStudentLink.user_id == user_id))).scalars():
            out += await self._fees(s.id, "/dashboard/parent/fees", f" for {s.first_name}")
        return out


# ---------------------------------------------------------------------- the manual's words for a page
def page_help(portal: str, label: str) -> dict | None:
    """The manual's explanation of a menu item: its bullet ("- **Label**: ...", with its sub-points) or its section."""
    from app.help import chapters_for

    want = re.sub(r"\s+", " ", label.replace("&amp;", "&")).strip().lower()
    for c in chapters_for(portal):
        lines = c["text"].splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^- \*\*(.+?)\*\*(?: \([^)]*\))?:\s*(.*)$", line)
            if m and m.group(1).strip().lower() == want:
                more = []
                for nxt in lines[i + 1:]:
                    if re.match(r"^\s{2,}- ", nxt):
                        more.append(_plain(re.sub(r"^\s+- ", "", nxt)))
                    elif nxt.strip() == "":
                        break
                    else:
                        break
                return {"title": m.group(1), "text": _plain(m.group(2)), "more": more[:8]}
            h = re.match(r"^#{2,3} (.+)$", line)
            if h and h.group(1).strip().lower() == want:
                para = []
                for nxt in lines[i + 1:]:
                    if nxt.startswith("#"):
                        break
                    if nxt.strip():
                        para.append(nxt.strip())
                    elif para:
                        break
                return {"title": h.group(1), "text": _plain(" ".join(para)), "more": []}
    return None


def _plain(s: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", re.sub(r"\*(.+?)\*", r"\1", s)).strip()
