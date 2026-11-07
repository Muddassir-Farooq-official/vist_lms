"""Weekly timetable in the familiar "sheet" layout: times down the side, weekdays across.

One builder serves everybody, and WHO is asking decides what they can see:
  * admin   -> every course; can narrow by term / class (program) / semester / section / teacher / room
  * teacher -> only courses they teach; can narrow by term / class / semester / section
  * student -> only courses they are enrolled in right now; can pick the term

The PDF is rendered from exactly the same data, so what is on screen is what is printed.
"""
from __future__ import annotations

import io
import uuid
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.batch import load_by_ids
from app.academics.models import Course, CourseProgram, DayOfWeek, EnrollmentStatus, TimetableSlot
from app.academics.repository import (
    CourseOfferingRepository,
    CourseProgramRepository,
    CourseRepository,
    EnrollmentRepository,
    PeriodRepository,
    TimetableSlotRepository,
)
from app.core.clock import institute_today
from app.employees.models import Employee
from app.programs.repository import ProgramRepository

WEEK_ORDER = [
    DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY,
    DayOfWeek.FRIDAY, DayOfWeek.SATURDAY, DayOfWeek.SUNDAY,
]
CORE_DAYS = WEEK_ORDER[:5]


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def _row_key(start: time, end: time) -> str:
    return f"{_hhmm(start)}-{_hhmm(end)}"


class WeeklyTimetableService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.offering_repo = CourseOfferingRepository(session)
        self.course_repo = CourseRepository(session)
        self.link_repo = CourseProgramRepository(session)
        self.slot_repo = TimetableSlotRepository(session)
        self.period_repo = PeriodRepository(session)
        self.enrollment_repo = EnrollmentRepository(session)
        self.program_repo = ProgramRepository(session)

    # ------------------------------------------------------------------ terms
    async def _all_offerings(self):
        terms: dict[str, list] = {}
        for term in await self.offering_repo.list_distinct_terms():
            terms[term] = await self.offering_repo.list_by_term(term)
        return terms

    @staticmethod
    def _is_current(offerings: list, today: date) -> bool:
        return any(o.term_start_date and o.term_end_date and o.term_start_date <= today <= o.term_end_date for o in offerings)

    async def term_options(self, allowed_offering_ids: set[uuid.UUID] | None = None) -> tuple[list[dict], str | None]:
        """Terms newest-first, with the current one flagged. If allowed_offering_ids is given,
        only terms containing at least one of those offerings are listed."""
        today = institute_today()
        rows = []
        for term, offerings in (await self._all_offerings()).items():
            if allowed_offering_ids is not None and not any(o.id in allowed_offering_ids for o in offerings):
                continue
            ends = [o.term_end_date for o in offerings if o.term_end_date]
            rows.append({"label": term, "is_current": self._is_current(offerings, today), "_sort": max(ends) if ends else date.min})
        rows.sort(key=lambda r: r["_sort"], reverse=True)
        default = next((r["label"] for r in rows if r["is_current"]), rows[0]["label"] if rows else None)
        return [{"label": r["label"], "is_current": r["is_current"]} for r in rows], default

    # ---------------------------------------------------------------- scoping
    async def _student_offering_ids(self, student_id: uuid.UUID) -> set[uuid.UUID]:
        return {e.course_offering_id for e in await self.enrollment_repo.list_current_for_student(student_id)}

    async def _teacher_offering_ids(self, employee_id: uuid.UUID) -> set[uuid.UUID]:
        return {o.id for o in await self.offering_repo.list_by_teacher(employee_id)}

    # ------------------------------------------------------------------ build
    async def build(
        self, *, scope: str, student_id: uuid.UUID | None = None, employee_id: uuid.UUID | None = None,
        term_label: str | None = None, program_id: uuid.UUID | None = None, semester_number: int | None = None,
        section_label: str | None = None, teacher_id: uuid.UUID | None = None, room: str | None = None,
    ) -> dict:
        allowed_ids: set[uuid.UUID] | None = None
        if scope == "student":
            allowed_ids = await self._student_offering_ids(student_id)
        elif scope == "teacher":
            allowed_ids = await self._teacher_offering_ids(employee_id)
        if scope != "admin":
            teacher_id = None          # only the admin may look at another teacher's week
            room = None

        terms, default_term = await self.term_options(allowed_ids)
        term = await self.offering_repo.canonical_term_label(term_label) if term_label else default_term

        program_codes: dict[uuid.UUID, str] = {p.id: p.code for p in await self.program_repo.list_all()}
        entries_by_cell: dict[tuple[str, str], list[dict]] = {}
        row_times: dict[str, tuple[time, time]] = {}
        used_days: set[DayOfWeek] = set()
        semester_courses: dict[int, set[uuid.UUID]] = {}
        count = 0

        offerings = await self.offering_repo.list_by_term(term) if term else []
        # Filters that need no database first, so we only fetch details for the offerings that matter.
        candidates = [
            o for o in offerings
            if (allowed_ids is None or o.id in allowed_ids)
            and not (section_label and o.section_label.strip().lower() != section_label.strip().lower())
            and not (teacher_id is not None and o.assigned_teacher_id != teacher_id)
        ]
        # Everything the loop needs, fetched together (one query each) instead of per course.
        course_cache = await load_by_ids(self.session, Course, [o.course_id for o in candidates])
        teacher_rows = await load_by_ids(self.session, Employee, [o.assigned_teacher_id for o in candidates])
        teacher_cache = {tid: f"{t.first_name} {t.last_name}" for tid, t in teacher_rows.items()}
        links_by_course: dict[uuid.UUID, list] = {}
        course_ids = {o.course_id for o in candidates}
        if course_ids:
            for link in (await self.session.execute(select(CourseProgram).where(CourseProgram.course_id.in_(course_ids)))).scalars():
                links_by_course.setdefault(link.course_id, []).append(link)
        slots_by_offering: dict[uuid.UUID, list] = {}
        if candidates:
            offering_ids = [o.id for o in candidates]
            for sl in (await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.in_(offering_ids)))).scalars():
                slots_by_offering.setdefault(sl.course_offering_id, []).append(sl)

        for o in candidates:
            course = course_cache.get(o.course_id)
            links = links_by_course.get(o.course_id, [])
            # The semesters this course sits at in the curriculum (of the chosen class, if one is chosen).
            relevant = [l for l in links if program_id is None or l.program_id == program_id]
            if program_id is not None and not relevant:
                continue
            course_semesters = {l.semester_number for l in relevant}
            placements = [{"program_code": program_codes[l.program_id], "semester": l.semester_number}
                          for l in sorted(links, key=lambda l: (program_codes.get(l.program_id, ""), l.semester_number))
                          if l.program_id in program_codes]

            teacher_name = teacher_cache.get(o.assigned_teacher_id) or None
            codes = sorted({program_codes[l.program_id] for l in links if l.program_id in program_codes})

            slots = [sl for sl in slots_by_offering.get(o.id, [])
                     if not room or room.strip().lower() in sl.room.lower()]
            if slots:
                for sem in course_semesters:
                    semester_courses.setdefault(sem, set()).add(o.id)
            if semester_number is not None and semester_number not in course_semesters:
                continue

            for slot in slots:
                key = _row_key(slot.start_time, slot.end_time)
                row_times[key] = (slot.start_time, slot.end_time)
                used_days.add(slot.day_of_week)
                entries_by_cell.setdefault((slot.day_of_week.value, key), []).append({
                    "offering_id": o.id, "course_code": course.code if course else "?",
                    "course_title": course.title if course else "?", "teacher_name": teacher_name,
                    "room": slot.room, "section_label": o.section_label, "program_codes": codes,
                    "placements": placements,
                })
                count += 1

        # Rows: the institute's defined periods (so the sheet keeps its familiar shape), plus
        # any time that slots still use after a period was deleted.
        periods = await self.period_repo.list_all()
        rows: dict[str, dict] = {}
        for p in periods:
            rows[_row_key(p.start_time, p.end_time)] = {
                "key": _row_key(p.start_time, p.end_time), "label": f"{_hhmm(p.start_time)}\u2013{_hhmm(p.end_time)}",
                "start_time": p.start_time, "end_time": p.end_time, "period_number": p.period_number,
            }
        for key, (st, et) in row_times.items():
            rows.setdefault(key, {"key": key, "label": f"{_hhmm(st)}\u2013{_hhmm(et)}", "start_time": st, "end_time": et, "period_number": None})
        ordered_rows = sorted(rows.values(), key=lambda r: (r["start_time"], r["end_time"]))

        days = [d for d in WEEK_ORDER if d in CORE_DAYS or d in used_days]
        cells = [
            {"day": day, "row_key": key, "entries": sorted(entries, key=lambda e: (e["course_code"], e["section_label"]))}
            for (day, key), entries in entries_by_cell.items()
        ]

        # A readable heading that says exactly which view this is.
        parts = [term] if term else []
        if program_id is not None:
            parts.append(program_codes.get(program_id, "Class"))
        if semester_number is not None:
            parts.append(f"Semester {semester_number}")
        if section_label:
            parts.append(f"Section {section_label}")
        note = None
        if teacher_id is not None:
            teacher = await self.session.get(Employee, teacher_id)
            if teacher:
                parts.append(f"{teacher.first_name} {teacher.last_name}")
            mine = [o for o in candidates if o.assigned_teacher_id == teacher_id]
            hours = sum(int(course_cache[o.course_id].credit_hours) for o in mine if course_cache.get(o.course_id))
            note = f"Teaching {len(mine)} course section(s), {hours} credit hours in {term}" if term else None
        if room:
            parts.append(f"Room {room}")
        return {
            "term_label": term, "title": " \u00b7 ".join(parts) if parts else "Weekly timetable",
            "scope": scope, "generated_on": institute_today(), "days": days, "rows": ordered_rows,
            "cells": cells, "entries_count": count, "note": note,
            "semester_counts": [{"semester": sem, "courses": len(ids)} for sem, ids in sorted(semester_courses.items())],
        }

    async def book(self, *, term_label: str | None, by: str) -> list[dict]:
        """Every class (program, semester, section) or every teacher of a term, each as its own timetable, for one
        printable PDF (admin)."""
        term = await self.offering_repo.canonical_term_label(term_label) if term_label else (await self.term_options(None))[1]
        if not term:
            return []
        offerings = await self.offering_repo.list_by_term(term)
        with_slots = set((await self.session.execute(select(TimetableSlot.course_offering_id).where(
            TimetableSlot.course_offering_id.in_([o.id for o in offerings] or [uuid.uuid4()])))).scalars())
        offerings = [o for o in offerings if o.id in with_slots]
        pages: list[dict] = []
        if by == "teacher":
            teachers = await load_by_ids(self.session, Employee, [o.assigned_teacher_id for o in offerings if o.assigned_teacher_id])
            for tid, t in sorted(teachers.items(), key=lambda kv: (kv[1].first_name, kv[1].last_name)):
                pages.append(await self.build(scope="admin", term_label=term, teacher_id=tid))
            return pages
        programs = {p.id: p.code for p in await self.program_repo.list_all()}
        keys: set[tuple] = set()
        course_ids = {o.course_id for o in offerings}
        links: dict = {}
        if course_ids:
            for link in (await self.session.execute(select(CourseProgram).where(CourseProgram.course_id.in_(course_ids)))).scalars():
                links.setdefault(link.course_id, []).append(link)
        for o in offerings:
            for link in links.get(o.course_id, []):
                keys.add((link.program_id, link.semester_number, o.section_label))
        for program_id, sem, section in sorted(keys, key=lambda k: (programs.get(k[0], ""), k[1], k[2])):
            pages.append(await self.build(scope="admin", term_label=term, program_id=program_id, semester_number=sem, section_label=section))
        return pages

    # ------------------------------------------------------------ filter lists
    async def filter_options(
        self, *, scope: str, student_id: uuid.UUID | None = None, employee_id: uuid.UUID | None = None
    ) -> dict:
        allowed_ids: set[uuid.UUID] | None = None
        if scope == "student":
            allowed_ids = await self._student_offering_ids(student_id)
        elif scope == "teacher":
            allowed_ids = await self._teacher_offering_ids(employee_id)
        terms, default_term = await self.term_options(allowed_ids)
        out: dict = {"allowed": ["term"], "terms": terms, "default_term": default_term,
                     "programs": [], "sections": [], "teachers": []}
        if scope == "student":
            return out
        out["allowed"] = ["term", "program", "semester", "section"] + (["teacher", "room"] if scope == "admin" else [])
        sections: set[str] = set()
        program_ids: set[uuid.UUID] = set()
        teacher_ids: set[uuid.UUID] = set()
        for offerings in (await self._all_offerings()).values():
            for o in offerings:
                if allowed_ids is not None and o.id not in allowed_ids:
                    continue
                sections.add(o.section_label)
                if o.assigned_teacher_id:
                    teacher_ids.add(o.assigned_teacher_id)
                if scope == "teacher":
                    program_ids.update(l.program_id for l in await self.link_repo.list_for_course(o.course_id))
        programs = await self.program_repo.list_all()
        out["programs"] = [
            {"id": p.id, "code": p.code, "name": p.name, "total_semesters": p.total_semesters}
            for p in programs if scope == "admin" or p.id in program_ids
        ]
        out["sections"] = sorted(sections)
        if scope == "admin":
            for tid in teacher_ids:
                t = await self.session.get(Employee, tid)
                if t:
                    out["teachers"].append({"id": tid, "name": f"{t.first_name} {t.last_name}"})
            out["teachers"].sort(key=lambda t: t["name"])
        return out


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _hue(code: str) -> int:
    """Same rule as static/js/weekly-timetable.js, so a course is the same colour on screen and in print."""
    h = 0
    for i, c in enumerate(code):
        h = (h * 31 + ord(c) * (i + 3)) % 360
    return h


def _hsl_to_hex(h: int, s: float, l: float) -> str:
    import colorsys

    r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def render_pdf(data: dict, *, institute_name: str | None = None) -> bytes:
    """A landscape A4 sheet in the same layout as the on-screen grid.

    A crowded time slot (dozens of classes at the same hour, as in the whole-institute view) cannot be
    laid out with three lines per class, so the sheet automatically steps down to a compact layout and,
    if it must, to a codes-only layout -- it always produces a readable PDF, never an error."""
    from reportlab.platypus.doctemplate import LayoutError

    from app.core import brand

    institute_name = institute_name or brand.name()
    last_error: Exception | None = None
    for density in (0, 1, 2):
        try:
            return _render_pdf(data, institute_name, density)
        except LayoutError as exc:
            last_error = exc
    raise last_error  # pragma: no cover - density 2 always fits


def _render_pdf(data: dict, institute_name: str, density: int) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    page_w, page_h = landscape(A4)
    margin = 12 * mm
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin + 4 * mm,
        title=f"Timetable - {data['title']}", author=institute_name,
    )

    ink, muted = colors.HexColor("#1a2540"), colors.HexColor("#5b6478")
    beige, lavender, cream, line = colors.HexColor("#e9e3d3"), colors.HexColor("#efe7fa"), colors.HexColor("#fbf9f3"), colors.HexColor("#c9bfd9")
    h_title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=20, textColor=ink, leading=24)
    h_sub = ParagraphStyle("s", fontName="Helvetica", fontSize=10.5, textColor=muted, leading=14)
    th = ParagraphStyle("th", fontName="Courier-Bold", fontSize=8.5, textColor=ink, alignment=1, leading=11)
    time_style = ParagraphStyle("time", fontName="Courier-Bold", fontSize=8.5, textColor=ink, alignment=1, leading=11)
    code_style = ParagraphStyle("c", fontName="Helvetica-Bold", fontSize=8.5, textColor=ink, leading=10.5)
    small = ParagraphStyle("m", fontName="Helvetica", fontSize=7.2, textColor=muted, leading=9)
    tiny = ParagraphStyle("t7", fontName="Helvetica", fontSize=6.3, textColor=ink, leading=7.4)

    days = data["days"]
    rows = data["rows"]
    by_cell = {(c["day"].value if hasattr(c["day"], "value") else c["day"], c["row_key"]): c["entries"] for c in data["cells"]}

    story = [
        Paragraph("Weekly Timetable", h_title),
        Paragraph(escape(data["title"]) or "&nbsp;", h_sub),
        Paragraph(f"{escape(institute_name)} &nbsp;|&nbsp; Generated {data['generated_on'].strftime('%d %b %Y')}", h_sub),
        *([Paragraph(escape(data["note"]), h_sub)] if data.get("note") else []),
        Spacer(1, 5 * mm),
    ]

    header = [Paragraph("TIME", th)] + [Paragraph((d.value if hasattr(d, "value") else d).upper(), th) for d in days]
    table_data = [header]
    styles = []
    for r_i, row in enumerate(rows, start=1):
        line_cells = [Paragraph(row["label"].replace("\u2013", "-"), time_style)]
        for c_i, day in enumerate(days, start=1):
            entries = by_cell.get((day.value if hasattr(day, "value") else day, row["key"]), [])
            flow = []
            if density == 0:
                for e in entries:
                    flow.append(Paragraph(escape(e["course_code"]) + (f" ({escape(e['section_label'])})" if e["section_label"] not in ("", "A") else ""), code_style))
                    flow.append(Paragraph(escape(e["course_title"]), small))
                    extra = " - ".join(x for x in (f"Room {escape(e['room'])}" if e["room"] else "", escape(e["teacher_name"]) if e["teacher_name"] else "") if x)
                    flow.append(Paragraph(extra, small))
                    flow.append(Spacer(1, 1.5 * mm))
            elif density == 1:
                for e in entries:
                    sec = f" ({escape(e['section_label'])})" if e["section_label"] not in ("", "A") else ""
                    room = f" - {escape(e['room'])}" if e["room"] else ""
                    flow.append(Paragraph(f"<b>{escape(e['course_code'])}</b>{sec} {escape(e['course_title'][:26])}{room}", tiny))
            else:
                names = ", ".join(escape(e["course_code"]) + (f" ({escape(e['section_label'])})" if e["section_label"] not in ("", "A") else "") for e in entries)
                flow.append(Paragraph(names, tiny))
            line_cells.append(flow or "")
            if entries:
                pastel = colors.HexColor(_hsl_to_hex(_hue(entries[0]["course_code"]), 0.65, 0.92))
                styles.append(("BACKGROUND", (c_i, r_i), (c_i, r_i), pastel))
        table_data.append(line_cells)

    time_w = 26 * mm
    day_w = (page_w - 2 * margin - time_w) / max(len(days), 1)
    table = Table(table_data, colWidths=[time_w] + [day_w] * len(days), repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), beige), ("BACKGROUND", (0, 1), (0, -1), lavender),
        ("BACKGROUND", (1, 1), (-1, -1), cream), ("GRID", (0, 0), (-1, -1), 0.5, line),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("VALIGN", (0, 0), (0, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("MINROWHEIGHT", (0, 1), (-1, -1), 16 * mm), *styles,
    ]))
    story.append(table)
    if not data["entries_count"]:
        story += [Spacer(1, 6 * mm), Paragraph("No classes are scheduled for these filters.", h_sub)]

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(margin, 8 * mm, institute_name)
        canvas.drawRightString(page_w - margin, 8 * mm, f"Page {_doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()

def render_book(pages: list[dict]) -> bytes:
    """Several timetables in one PDF, each starting on its own page (whole institute by class, or every teacher)."""
    from pypdf import PdfReader, PdfWriter

    out = PdfWriter()
    for data in pages:
        for page in PdfReader(io.BytesIO(render_pdf(data))).pages:
            out.add_page(page)
    buf = io.BytesIO()
    out.write(buf)
    return buf.getvalue()
