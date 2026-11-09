"""The principal's monthly report: fees, class attendance, results and staff, for one calendar month.

Always worked out fresh from the records (nothing is stored but the fact that a month's report was sent, table
monthly_report_runs), and every figure comes from the same calculations the other screens use:
  * fees      -- money received in the month (the collection report / day book rules), what is still owed and overdue today
                 (the fee position and defaulter lists)
  * classes   -- per class and per teacher: classes held in the month (a class counts once attendance was taken) and the
                 average attendance in them (approved leave is not counted for or against a student)
  * results   -- result sheets published in the month: pass rate and average per course
  * staff     -- each person's month from staff attendance (present, leave, absent, late), and leave applied for in the month
On the 1st of each month (from 8 AM) everyone who can see the institute overview is told the last month's report is ready,
with a link to the PDF; the report stays on the Monthly report page for any month.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import institute_today
from app.core.exceptions import ValidationAppError

logger = structlog.get_logger(__name__)
ATTENDANCE_MIN = 75.0
PAGE = "/dashboard/principal/monthly"


def parse_month(value: str | None) -> date:
    today = institute_today()
    if not value:
        prev = (today.replace(day=1) - (date.resolution * 1))
        return prev.replace(day=1)                                      # last month: the one the report is about
    try:
        m = date.fromisoformat(str(value)[:7] + "-01")
    except ValueError:
        raise ValidationAppError("Choose a month like 2026-10.")
    if m > today.replace(day=1) or m.year < 2000:
        raise ValidationAppError("Choose a month up to this one.")
    return m


def _pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


class MonthlyReport:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(self, month: date) -> dict:
        from app.timekeeping.service import month_bounds

        first, last = month_bounds(month)
        today = institute_today()
        return {"month": first, "month_label": first.strftime("%B %Y"), "from_date": first, "to_date": min(last, today),
                "complete": last < today, "generated_on": today,
                "fees": await self._fees(first, min(last, today)), "classes": await self._classes(first, last),
                "results": await self._results(first, last), "staff": await self._staff(first, last)}

    # ------------------------------------------------------------------ fees
    async def _fees(self, first: date, last: date) -> dict:
        from app.reports.center import ReportCenter
        from app.reports.finance_service import FinanceReportService

        fin = FinanceReportService(self.session)
        col = await fin.collection(first, last)
        position = await ReportCenter(self.session).fee_position({"group_by": "fee_head"}, None)
        d = await fin.defaulters()
        defaulters = [p for p in d["students"] if p["overdue"] > 0.005]
        t = position["totals"]
        return {"received": col["received"], "taken_back": col["taken_back"], "net": col["total"], "by_method": col["by_method"],
                "payments": sum(g["count"] for g in col["rows"]),
                "payable": t.get("payable", 0.0), "paid": t.get("paid", 0.0), "outstanding": t.get("outstanding", 0.0), "overdue": t.get("overdue", 0.0),
                "collected_pct": t.get("collected_pct"),
                "by_head": [{"head": r["group"], "students": r["students"], "payable": r["payable"], "paid": r["paid"], "outstanding": r["outstanding"],
                             "overdue": r["overdue"], "collected_pct": r["collected_pct"]} for r in position["rows"]],
                "defaulters": len(defaulters), "defaulters_amount": round(sum(p["overdue"] for p in defaulters), 2),
                "top_defaulters": [{"name": p.get("name") or "", "roll_number": p.get("roll_number") or "", "overdue": p["overdue"],
                                    "days_overdue": p["days_overdue"]} for p in defaulters[:10]]}

    # ------------------------------------------------------------------ class attendance
    async def _classes(self, first: date, last: date) -> dict:
        from app.academics.models import ClassSession, Course, CourseOffering
        from app.attendance.models import AttendanceRecord, AttendanceStatus
        from app.employees.models import Employee

        rows = (await self.session.execute(
            select(ClassSession.course_offering_id, func.count(func.distinct(ClassSession.id)),
                   func.count().filter(AttendanceRecord.status == AttendanceStatus.PRESENT),
                   func.count().filter(AttendanceRecord.status == AttendanceStatus.LEAVE), func.count())
            .join(AttendanceRecord, AttendanceRecord.class_session_id == ClassSession.id)
            .where(ClassSession.session_date >= first, ClassSession.session_date <= last)
            .group_by(ClassSession.course_offering_id))).all()
        if not rows:
            return {"classes": [], "teachers": [], "held": 0, "average": None, "low": 0}
        # per student, per class: below the minimum in this month
        low_rows = (await self.session.execute(
            select(ClassSession.course_offering_id, AttendanceRecord.student_id,
                   func.count().filter(AttendanceRecord.status == AttendanceStatus.PRESENT),
                   func.count().filter(AttendanceRecord.status != AttendanceStatus.LEAVE))
            .join(AttendanceRecord, AttendanceRecord.class_session_id == ClassSession.id)
            .where(ClassSession.session_date >= first, ClassSession.session_date <= last)
            .group_by(ClassSession.course_offering_id, AttendanceRecord.student_id))).all()
        low: dict = {}
        for oid, _sid, present, counted in low_rows:
            if counted and 100 * present / counted < ATTENDANCE_MIN:
                low[oid] = low.get(oid, 0) + 1
        ids = [r[0] for r in rows]
        info = {o.id: (o, c, t) for o, c, t in (await self.session.execute(
            select(CourseOffering, Course, Employee).join(Course, Course.id == CourseOffering.course_id)
            .outerjoin(Employee, Employee.id == CourseOffering.assigned_teacher_id).where(CourseOffering.id.in_(ids)))).all()}
        classes, teachers = [], {}
        all_present = all_counted = held_total = 0
        for oid, held, present, leave, total in rows:
            o, c, t = info[oid]
            counted = total - leave
            teacher = f"{t.first_name} {t.last_name}" if t else "No teacher"
            classes.append({"course": f"{c.code} — {c.title}", "section": o.section_label, "teacher": teacher, "held": held,
                            "average": _pct(present, counted), "low": low.get(oid, 0)})
            tt = teachers.setdefault(teacher, {"teacher": teacher, "classes": 0, "held": 0, "present": 0, "counted": 0, "low": 0})
            tt["classes"] += 1
            tt["held"] += held
            tt["present"] += present
            tt["counted"] += counted
            tt["low"] += low.get(oid, 0)
            all_present += present
            all_counted += counted
            held_total += held
        classes.sort(key=lambda r: (r["average"] if r["average"] is not None else 101, r["course"]))
        trows = [{"teacher": v["teacher"], "classes": v["classes"], "held": v["held"], "average": _pct(v["present"], v["counted"]), "low": v["low"]}
                 for v in teachers.values()]
        trows.sort(key=lambda r: r["teacher"])
        return {"classes": classes, "teachers": trows, "held": held_total, "average": _pct(all_present, all_counted), "low": sum(low.values())}

    # ------------------------------------------------------------------ results
    async def _results(self, first: date, last: date) -> dict:
        from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentResult, ResultSheet
        from app.core.clock import local_date

        sheets = (await self.session.execute(select(ResultSheet).where(ResultSheet.published_at.is_not(None)))).scalars().all()
        ids = [s.course_offering_id for s in sheets if first <= local_date(s.published_at) <= last]
        if not ids:
            return {"courses": [], "results": 0, "passed": 0, "pass_rate": None}
        res = (await self.session.execute(select(Enrollment.course_offering_id, EnrollmentResult)
                                          .join(EnrollmentResult, EnrollmentResult.enrollment_id == Enrollment.id)
                                          .where(Enrollment.course_offering_id.in_(ids)))).all()
        info = {o.id: (o, c) for o, c in (await self.session.execute(select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id)
                                                                         .where(CourseOffering.id.in_(ids)))).all()}
        per: dict = {}
        for oid, r in res:
            p = per.setdefault(oid, {"n": 0, "pass": 0, "pct": 0.0, "att": 0})
            p["n"] += 1
            p["pass"] += 1 if r.outcome == "PASSED" else 0
            p["att"] += 1 if r.failed_for_attendance else 0
            p["pct"] += float(r.percentage)
        courses = []
        for oid in ids:
            o, c = info[oid]
            p = per.get(oid, {"n": 0, "pass": 0, "pct": 0.0, "att": 0})
            courses.append({"course": f"{c.code} — {c.title}", "section": o.section_label, "term": o.term_label, "results": p["n"], "passed": p["pass"],
                            "failed": p["n"] - p["pass"], "failed_attendance": p["att"], "pass_rate": _pct(p["pass"], p["n"]),
                            "average": round(p["pct"] / p["n"], 1) if p["n"] else None})
        courses.sort(key=lambda r: (r["pass_rate"] if r["pass_rate"] is not None else 101, r["course"]))
        n, passed = sum(r["results"] for r in courses), sum(r["passed"] for r in courses)
        return {"courses": courses, "results": n, "passed": passed, "pass_rate": _pct(passed, n)}

    # ------------------------------------------------------------------ staff
    async def _staff(self, first: date, last: date) -> dict:
        from app.employees.models import Employee
        from app.timekeeping.models import LeaveRequest
        from app.timekeeping.service import TimekeepingService

        people = await TimekeepingService(self.session).month_report(first)
        rows = [{"name": r["name"], "code": r["employee_code"], "job": r["job_title"], "working_days": r.get("scheduled", 0), "present": r.get("present", 0),
                 "leave": r.get("excused", 0), "absent": len(r.get("absent_dates") or []), "late": r.get("late", 0), "early": r.get("early", 0)} for r in people]
        rows.sort(key=lambda r: (-r["absent"], -r["late"], r["name"]))
        leaves = (await self.session.execute(select(LeaveRequest, Employee).join(Employee, Employee.id == LeaveRequest.employee_id)
                                             .where(LeaveRequest.first_day <= last, LeaveRequest.last_day >= first))).all()
        by_kind: dict = {}
        pending = 0
        for lr, _e in leaves:
            if lr.status == "APPROVED":
                k = by_kind.setdefault(lr.kind, {"kind": lr.kind.title(), "requests": 0, "days": 0})
                k["requests"] += 1
                k["days"] += lr.days
            elif lr.status == "REQUESTED":
                pending += 1
        sched = sum(r["working_days"] for r in rows)
        return {"people": rows, "staff": len(rows), "present_rate": _pct(sum(r["present"] for r in rows), sched),
                "absent_days": sum(r["absent"] for r in rows), "late": sum(r["late"] for r in rows), "leave_days": sum(r["leave"] for r in rows),
                "leave_by_kind": sorted(by_kind.values(), key=lambda k: k["kind"]), "leave_pending": pending}


# ====================================================================== PDF
def _money(v) -> str:
    return f"Rs. {float(v or 0):,.0f}"


def _p(v) -> str:
    return "—" if v is None else f"{v:g}%"


def render_pdf(r: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from app.core import brand

    ink, muted, line = colors.HexColor("#0f1b3d"), colors.HexColor("#5b6785"), colors.HexColor("#d5dbe8")
    accent = colors.HexColor(brand.color())
    buf = io.BytesIO()
    width = A4[0] - 30 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"Monthly report {r['month_label']}", author=brand.name())
    base = getSampleStyleSheet()
    sub = ParagraphStyle("sub", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=11, textColor=accent, alignment=1, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=11.5, textColor=ink, spaceBefore=10, spaceAfter=4)
    small = ParagraphStyle("small", parent=base["Normal"], fontName="Helvetica", fontSize=8, textColor=muted, leading=10.5)
    cell = ParagraphStyle("cell", parent=base["Normal"], fontName="Helvetica", fontSize=8, textColor=ink, leading=10)
    story = brand.platypus_header(width) + [Paragraph(f"MONTHLY REPORT — {r['month_label'].upper()}", sub)]
    if not r["complete"]:
        story.append(Paragraph(f"The month is not over yet: figures up to {r['to_date']:%d %b %Y}.", ParagraphStyle("c", parent=small, alignment=1)))

    def kpis(items: list[tuple[str, str]]) -> Table:
        t = Table([[Paragraph(f"<font size=7 color='#5b6785'>{k.upper()}</font><br/><b>{v}</b>", cell) for k, v in items]],
                  colWidths=[width / len(items)] * len(items))
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, line), ("INNERGRID", (0, 0), (-1, -1), 0.6, line),
                               ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fc")), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        return t

    def grid(head: list[str], rows: list[list], widths: list[float], right_from: int = 1) -> Table:
        data = [head] + [[Paragraph(str(v), cell) if i < right_from else str(v) for i, v in enumerate(row)] for row in rows]
        t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), accent),
                               ("FONT", (0, 1), (-1, -1), "Helvetica", 8), ("TEXTCOLOR", (0, 1), (-1, -1), ink), ("ALIGN", (right_from, 0), (-1, -1), "RIGHT"),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOX", (0, 0), (-1, -1), 0.6, line), ("LINEBELOW", (0, 1), (-1, -1), 0.3, line),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return t

    def none(text: str) -> Paragraph:
        return Paragraph(text, small)

    f = r["fees"]
    story += [Paragraph("1. Fees", h2),
              kpis([("Received this month", _money(f["received"])), ("Payments", str(f["payments"])), ("Still owed today", _money(f["outstanding"])),
                    ("Overdue today", _money(f["overdue"])), ("Students overdue", str(f["defaulters"]))])]
    if f["taken_back"]:
        story.append(Paragraph(f"Received {_money(f['received'])}, less {_money(f['taken_back'])} paid back or reversed: net {_money(f['net'])}.", small))
    if f["by_head"]:
        story += [Spacer(1, 5), grid(["Fee", "Students", "Payable", "Paid", "Still owed", "Overdue", "Collected"],
                                     [[h["head"], h["students"], _money(h["payable"]), _money(h["paid"]), _money(h["outstanding"]), _money(h["overdue"]), _p(h["collected_pct"])]
                                      for h in f["by_head"]], [46, 17, 25, 25, 25, 25, 17])]
    if f["top_defaulters"]:
        story += [Spacer(1, 5), Paragraph("Largest overdue amounts", small),
                  grid(["Student", "Roll no.", "Overdue", "Days overdue"], [[d["name"], d["roll_number"], _money(d["overdue"]), d["days_overdue"]] for d in f["top_defaulters"]],
                       [70, 40, 35, 35], right_from=2)]

    c = r["classes"]
    story += [Paragraph("2. Class attendance", h2),
              kpis([("Classes held", str(c["held"])), ("Average attendance", _p(c["average"])), (f"Students below {ATTENDANCE_MIN:g}% in a class", str(c["low"]))])]
    if c["teachers"]:
        story += [Spacer(1, 5), grid(["Teacher", "Classes", "Held", "Average", f"Below {ATTENDANCE_MIN:g}%"],
                                     [[t["teacher"], t["classes"], t["held"], _p(t["average"]), t["low"]] for t in c["teachers"]], [80, 25, 25, 25, 25])]
        story += [Spacer(1, 5), grid(["Class", "Section", "Teacher", "Held", "Average", f"Below {ATTENDANCE_MIN:g}%"],
                                     [[x["course"], x["section"], x["teacher"], x["held"], _p(x["average"]), x["low"]] for x in c["classes"]], [62, 16, 42, 18, 22, 20], right_from=3)]
    else:
        story.append(none("No attendance was taken this month."))

    res = r["results"]
    block = [Paragraph("3. Results", h2), kpis([("Results published", str(res["results"])), ("Passed", str(res["passed"])), ("Pass rate", _p(res["pass_rate"]))])]
    if res["courses"]:
        block += [Spacer(1, 5), grid(["Course", "Section", "Results", "Passed", "Failed", "Pass rate", "Average"],
                                     [[x["course"], x["section"], x["results"], x["passed"], x["failed"], _p(x["pass_rate"]), _p(x["average"])] for x in res["courses"]],
                                     [62, 16, 20, 20, 20, 21, 21], right_from=2)]
    else:
        block.append(none("No results were published this month."))
    story.append(KeepTogether(block[:2]))
    story += block[2:]

    s = r["staff"]
    story += [Paragraph("4. Staff attendance and leave", h2),
              kpis([("Staff", str(s["staff"])), ("Present", _p(s["present_rate"])), ("Days absent", str(s["absent_days"])), ("Late arrivals", str(s["late"])),
                    ("Leave days", str(s["leave_days"]))])]
    if s["leave_by_kind"] or s["leave_pending"]:
        txt = "; ".join(f"{k['kind']}: {k['requests']} request(s), {k['days']} day(s)" for k in s["leave_by_kind"]) or "No leave approved"
        story.append(Paragraph(f"Leave approved: {txt}." + (f" Waiting for a decision: {s['leave_pending']}." if s["leave_pending"] else ""), small))
    if s["people"]:
        story += [Spacer(1, 5), grid(["Name", "Job", "Working days", "Present", "Leave", "Absent", "Late", "Left early"],
                                     [[p["name"], p["job"], p["working_days"], p["present"], p["leave"], p["absent"], p["late"], p["early"]] for p in s["people"]],
                                     [46, 38, 18, 16, 16, 16, 14, 16], right_from=2)]

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.3)
        canvas.setFillColor(muted)
        canvas.drawString(15 * mm, 9 * mm, f"Made on {r['generated_on']:%d %b %Y} from the records in the portal." + (f"  ·  {brand.platypus_footer_text()}" if brand.platypus_footer_text() else ""))
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ====================================================================== on the 1st
async def send_on_the_first(session: AsyncSession) -> int:
    """From 8 AM on the 1st: tell everyone who sees the institute overview that last month's report is ready. Once per month. Never raises."""
    try:
        async with session.begin_nested():
            from app.core.clock import institute_now
            from app.core.config import settings
            from app.records.models import MonthlyReportRun

            now = institute_now()
            if not int(getattr(settings, "monthly_report_notice", 1)) or now.day != 1 or now.hour < 8:
                return 0
            month = parse_month(None)
            if (await session.execute(select(MonthlyReportRun).where(MonthlyReportRun.month == month))).scalar_one_or_none() is not None:
                return 0
            from app.approvals.service import _users_with_permission
            from app.auth.permissions import Permissions
            from app.notifications.models import NotificationCategory
            from app.notifications.service import create_notification

            r = await MonthlyReport(session).build(month)
            f, c, res, s = r["fees"], r["classes"], r["results"], r["staff"]
            msg = (f"{r['month_label']}: {_money(f['received'])} received, {_money(f['overdue'])} overdue; average class attendance {_p(c['average'])}; "
                   + (f"pass rate {_p(res['pass_rate'])} in {res['results']} results; " if res["results"] else "")
                   + f"staff present {_p(s['present_rate'])}. Open it to download the PDF.")
            users = await _users_with_permission(session, Permissions.INSTITUTE_OVERVIEW)
            for uid in users:
                await create_notification(session, user_id=uid, category=NotificationCategory.GENERAL, title=f"Monthly report for {r['month_label']} is ready",
                                          message=msg, link=PAGE)
            session.add(MonthlyReportRun(month=month, sent_to=len(users)))
            await session.flush()
            return len(users)
    except Exception as exc:  # noqa: BLE001 - a report problem must not stop the other jobs
        logger.warning("monthly_report_failed", error=str(exc)[:300])
        return 0
