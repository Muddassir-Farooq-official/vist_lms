"""Result documents printed with the institute's letterhead AND the affiliating body (from the program's grading table):

  * award list: one subject, every student's marks per assessment, theory / practical %, total, grade, result;
  * semester (year) result cards: one page per student of a program + semester + term;
  * result gazette / notification: the whole class in roll order, position holders first, status and failed subjects.
Annual tables print Year, total marks, % and division instead of Semester and GPA.
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.academics.transcript_pdf import BLUE, INK, LINE, MUTED, RED

_base = getSampleStyleSheet()
SUB = ParagraphStyle("rd_sub", parent=_base["Normal"], fontName="Helvetica-Bold", fontSize=10.5, textColor=INK, alignment=1, spaceAfter=2)
AFF = ParagraphStyle("rd_aff", parent=_base["Normal"], fontName="Helvetica", fontSize=8.8, textColor=MUTED, alignment=1, spaceAfter=6)
CELL = ParagraphStyle("rd_cell", parent=_base["Normal"], fontName="Helvetica", fontSize=8, leading=9.6, textColor=INK)
SMALL = ParagraphStyle("rd_small", parent=_base["Normal"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=MUTED)


def _f(v, places=2) -> str:
    return "—" if v is None else f"{v:.{places}f}"


def _header(width: float, title: str, body: str | None) -> list:
    from app.core import brand

    out = brand.platypus_header(width) + [Paragraph(title, SUB)]
    out.append(Paragraph(f"Affiliated with {body}" if body else "&nbsp;", AFF))
    return out


def _grid(rows, widths, *, head_rows=1, extra=None) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=head_rows)
    style = [("FONT", (0, 0), (-1, head_rows - 1), "Helvetica-Bold", 7.8), ("TEXTCOLOR", (0, 0), (-1, head_rows - 1), colors.white),
             ("BACKGROUND", (0, 0), (-1, head_rows - 1), BLUE), ("FONT", (0, head_rows), (-1, -1), "Helvetica", 8), ("TEXTCOLOR", (0, head_rows), (-1, -1), INK),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (2, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.3, LINE),
             ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    t.setStyle(TableStyle(style + (extra or [])))
    return t


def _signatures(width: float, names: list[str]) -> Table:
    t = Table([["" for _ in names], names], colWidths=[width / len(names)] * len(names), rowHeights=[16 * mm, None])
    t.setStyle(TableStyle([("LINEABOVE", (0, 1), (-1, 1), 0.6, INK), ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 8), ("TEXTCOLOR", (0, 1), (-1, 1), INK),
                           ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14)]))
    return t


def _doc(buf, pagesize, title):
    from app.core import brand

    return SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=16 * mm,
                             title=title, author=brand.name())


def _footer(stamp: str):
    def draw(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7); canvas.setFillColor(MUTED)
        canvas.drawString(12 * mm, 8 * mm, f"Computer-generated on {stamp}.")
        canvas.drawRightString(document.pagesize[0] - 12 * mm, 8 * mm, f"Page {document.page}")
        canvas.restoreState()
    return draw


# ---------------------------------------------------------------------- award list (one subject)
def render_award_list(sheet: dict, *, grading: dict, program_label: str | None) -> bytes:
    buf = io.BytesIO()
    size = landscape(A4)
    width = size[0] - 24 * mm
    doc = _doc(buf, size, "Award list")
    stamp = datetime.now().strftime("%d %b %Y")
    story = _header(width, "AWARD LIST", grading.get("body"))
    info = [["Subject", f"{sheet['course_code']} — {sheet['course_title']}", "Term", sheet.get("term_label") or "—"],
            ["Section", sheet.get("section_label") or "—", "Program", program_label or "—"],
            ["Status", "Published" if sheet.get("status") == "PUBLISHED" else "Not published (projection)", "Grading table", grading.get("name") or "—"]]
    t = Table(info, colWidths=[24 * mm, width / 2 - 24 * mm, 26 * mm, width / 2 - 26 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5), ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.5),
                           ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fc"))]))
    story += [t, Spacer(1, 6)]
    assessments = sheet.get("assessments_list") or []
    parts = any(r.get("theory_percent") is not None or r.get("practical_percent") is not None for r in sheet["rows"])
    head = ["#", "Roll no.", "Name"] + [f"{a['name']}\n/{a['max_marks']:g}" for a in assessments] + (["Theory %", "Practical %"] if parts else []) + ["%", "Grade", "Result"]
    rows = [head]
    extra = []
    for i, r in enumerate(sheet["rows"], start=1):
        result = {"PASSED": "Pass", "FAILED": "Fail", "SHORT_ATTENDANCE": "Short attendance"}.get(r.get("outcome"), "—")
        if r.get("outcome") == "FAILED" and r.get("failed_parts"):
            result = "Fail (" + " & ".join(p.title() for p in r["failed_parts"]) + ")"
        rows.append([str(i), r.get("roll_number") or "", Paragraph(r.get("student_name") or "", CELL)]
                    + [("—" if m is None else f"{m:g}") for m in r["marks"]]
                    + ([_f(r.get("theory_percent"), 1), _f(r.get("practical_percent"), 1)] if parts else [])
                    + [_f(r.get("percentage"), 1), r.get("grade") or "—", result])
        if r.get("outcome") in ("FAILED", "SHORT_ATTENDANCE"):
            extra.append(("TEXTCOLOR", (-1, i), (-1, i), RED))
    fixed = 8 * mm + 22 * mm + 48 * mm + 14 * mm + 14 * mm + 30 * mm + (36 * mm if parts else 0)
    each = max(14 * mm, (width - fixed) / max(1, len(assessments)))
    widths = [8 * mm, 22 * mm, 48 * mm] + [each] * len(assessments) + ([18 * mm, 18 * mm] if parts else []) + [14 * mm, 14 * mm, 30 * mm]
    story.append(_grid(rows, widths, extra=extra))
    st = sheet.get("stats") or {}
    story += [Spacer(1, 5), Paragraph(f"Students: {st.get('students', 0)} · passed: {st.get('passed', 0)} · failed: {st.get('failed', 0)} · "
                                      f"average: {_f(st.get('average'), 1)}%", SMALL), Spacer(1, 10),
              _signatures(width, ["Subject teacher", "Head of department", "Controller of Examinations"])]
    doc.build(story, onFirstPage=_footer(stamp), onLaterPages=_footer(stamp))
    return buf.getvalue()


# ---------------------------------------------------------------------- semester (year) result cards
def render_result_cards(data: dict) -> bytes:
    buf = io.BytesIO()
    width = A4[0] - 24 * mm
    doc = _doc(buf, A4, "Result cards")
    stamp = datetime.now().strftime("%d %b %Y")
    annual, word = data["annual"], data["period_word"]
    story = []
    for n, r in enumerate(data["rows"]):
        if n:
            story.append(PageBreak())
        story += _header(width, f"{word.upper()} RESULT CARD", data["grading"].get("body"))
        info = [["Name", r["name"], "Roll no.", r["roll_number"] or "—"],
                [r.get("guardian_label") or "Father's name", r.get("guardian_name") or "—", "Program", f"{data['program']['name']} ({data['program']['code']})"],
                [word, str(data["semester"]), "Term", data["term_label"]]]
        t = Table(info, colWidths=[30 * mm, width / 2 - 30 * mm, 22 * mm, width / 2 - 22 * mm])
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8.7), ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.7), ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.7),
                               ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fc"))]))
        story += [t, Spacer(1, 6)]
        head = ["Code", "Subject", "Cr", "Theory %", "Practical %", "Marks", "%", "Grade", "GP", "Result"]
        rows, extra = [head], []
        for i, s in enumerate(r["subjects"], start=1):
            if not s["published"]:
                rows.append([s["code"], Paragraph(s["title"], CELL), str(s["credit_hours"]), "", "", "", "", "", "", "Not published"])
                continue
            res = "Pass" + (" (S)" if s.get("supplementary") else "") if s["outcome"] == "PASSED" else (
                "Short attendance" if s["short_attendance"] else ("Fail (" + s["failed_part"].replace("+", " & ").title() + ")" if s["failed_part"] else "Fail"))
            rows.append([s["code"], Paragraph(s["title"], CELL), str(s["credit_hours"]), _f(s["theory_percent"], 1), _f(s["practical_percent"], 1),
                         f"{s['total_obtained']:g}/{s['total_max']:g}", _f(s["percentage"], 1), s["grade"], _f(s["grade_points"]), res])
            if s["outcome"] != "PASSED":
                extra.append(("TEXTCOLOR", (-1, i), (-1, i), RED))
        story.append(_grid(rows, [18 * mm, 52 * mm, 8 * mm, 15 * mm, 17 * mm, 20 * mm, 12 * mm, 12 * mm, 11 * mm, None], extra=extra))
        summary = (f"Total {r['total_obtained']:g} / {r['total_max']:g}  ·  {_f(r['percentage'], 2)}%" + (f"  ·  {r['division']}" if r.get("division") else "")
                   if annual else f"{word} GPA: {_f(r['gpa'])}")
        box = Table([[summary, f"Status: {r['status_label']}" + (f"  ·  Position {r['position']}" if r.get("position") else "")]], colWidths=[width / 2, width / 2])
        box.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica-Bold", 9), ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("BOX", (0, 0), (-1, -1), 1, BLUE),
                                 ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef2fb")), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story += [Spacer(1, 6), box]
        if r["failed"]:
            story.append(Paragraph("To clear: " + ", ".join(r["failed"]), SMALL))
        story += [Spacer(1, 4), Paragraph(f"Grading: {data['grading']['name']} (pass {data['grading']['pass_percent']:g}%). (S) = passed in the supplementary exam.", SMALL),
                  Spacer(1, 14), _signatures(width, ["Controller of Examinations", "Principal"])]
    if not data["rows"]:
        story += _header(width, f"{word.upper()} RESULT CARDS", data["grading"].get("body")) + [Paragraph("No students in this semester.", SMALL)]
    doc.build(story, onFirstPage=_footer(stamp), onLaterPages=_footer(stamp))
    return buf.getvalue()


# ---------------------------------------------------------------------- gazette
def render_gazette(data: dict) -> bytes:
    buf = io.BytesIO()
    size = landscape(A4)
    width = size[0] - 24 * mm
    doc = _doc(buf, size, "Result gazette")
    stamp = datetime.now().strftime("%d %b %Y")
    annual, word = data["annual"], data["period_word"]
    story = _header(width, f"RESULT NOTIFICATION — {data['program']['name'].upper()} ({data['program']['code']}), {word.upper()} {data['semester']}, {data['term_label'].upper()}",
                    data["grading"].get("body"))
    holders = sorted([r for r in data["rows"] if r.get("position")], key=lambda r: r["position"])
    if holders:
        ph = [["Position", "Roll no.", "Name", "Percentage" if annual else "GPA"]] + [
            [{1: "1st", 2: "2nd", 3: "3rd"}[r["position"]], r["roll_number"] or "", r["name"], _f(r["percentage"], 2) if annual else _f(r["gpa"])] for r in holders]
        story += [Paragraph("<b>Position holders</b>", CELL), Spacer(1, 2), _grid(ph, [24 * mm, 30 * mm, 80 * mm, 30 * mm]), Spacer(1, 8)]
    head = ["#", "Roll no.", "Name", "Father / guardian"] + (["Marks", "%", "Division"] if annual else ["GPA"]) + ["Status", "Subjects to clear"]
    rows, extra = [head], []
    for i, r in enumerate(data["rows"], start=1):
        rows.append([str(i), r["roll_number"] or "", Paragraph(r["name"], CELL), Paragraph(r.get("guardian_name") or "—", CELL)]
                    + ([f"{r['total_obtained']:g}/{r['total_max']:g}", _f(r["percentage"], 2), r.get("division") or "—"] if annual else [_f(r["gpa"])])
                    + [r["status_label"], Paragraph(", ".join(r["failed"]) or "—", CELL)])
        if r["status"] in ("NOT_PROMOTED",):
            extra.append(("TEXTCOLOR", (-2, i), (-2, i), RED))
    widths = [8 * mm, 26 * mm, 52 * mm, 52 * mm] + ([24 * mm, 16 * mm, 24 * mm] if annual else [16 * mm]) + [34 * mm, None]
    story.append(_grid(rows, widths, extra=extra))
    c = data["counts"]
    story += [Spacer(1, 5), Paragraph(f"Students: {len(data['rows'])} · promoted: {c['PROMOTED']} · on probation: {c['PROBATION']} · "
                                      f"not promoted: {c['NOT_PROMOTED']} · result pending: {c['PENDING']} · no subjects this term: {c['NO_SUBJECTS']}. Grading: {data['grading']['name']}.", SMALL),
              Spacer(1, 16), _signatures(width, ["Controller of Examinations", "Principal"])]
    doc.build(story, onFirstPage=_footer(stamp), onLaterPages=_footer(stamp))
    return buf.getvalue()
