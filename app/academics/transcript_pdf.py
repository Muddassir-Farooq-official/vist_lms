"""The printable result card (transcript) as a PDF."""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK, MUTED, BLUE, RED, GREEN, LINE = colors.HexColor("#0f1b3d"), colors.HexColor("#5b6785"), colors.HexColor("#2454d6"), colors.HexColor("#c62828"), colors.HexColor("#17803d"), colors.HexColor("#d5dbe8")


def _f(v, places=2) -> str:
    return "\u2014" if v is None else f"{v:.{places}f}"


def render_transcript_pdf(data: dict, *, institute_name: str = "Vertex Institute of Science & Technology") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm, title="Result card", author=institute_name)
    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=17, textColor=INK, spaceAfter=1)
    sub = ParagraphStyle("sub", parent=base["Normal"], fontName="Helvetica", fontSize=9, textColor=MUTED, alignment=1, spaceAfter=8)
    term_style = ParagraphStyle("term", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, textColor=BLUE, spaceBefore=9, spaceAfter=3)
    small = ParagraphStyle("small", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, textColor=MUTED, leading=10)
    cell = ParagraphStyle("cell", parent=base["Normal"], fontName="Helvetica", fontSize=8.5, leading=10.5, textColor=INK)
    story = [Paragraph(institute_name, h1), Paragraph("RESULT CARD (TRANSCRIPT)", sub)]

    s, summ = data["student"], data["summary"]
    info = [["Name", s["name"], "Roll number", s["roll_number"] or "\u2014"],
            ["Father / guardian", s["guardian_name"] or "\u2014", "Program", f"{s['program_name'] or '\u2014'}" + (f" ({s['program_code']})" if s["program_code"] else "")],
            ["Admission session", s["admission_session"] or "\u2014", "Current semester", str(s["current_semester"] or "\u2014")]]
    t = Table(info, colWidths=[30 * mm, 55 * mm, 30 * mm, 55 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8.7), ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.7), ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.7),
                           ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("TEXTCOLOR", (0, 0), (0, -1), MUTED), ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
                           ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fc")), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [t, Spacer(1, 4)]

    head = ["Code", "Course", "Cr", "Marks", "%", "Grade", "GP", "QP"]
    for term in data["terms"]:
        story.append(Paragraph(term["label"], term_style))
        rows = [head]
        for c in term["courses"]:
            title = c["course_title"] + ("  (repeated later)" if c["replaced"] else "") + ("  (attempt %d)" % c["attempt"] if c["attempt"] > 1 and not c["replaced"] else "") + ("  \u2014 failed: attendance" if c["failed_for_attendance"] else "")
            rows.append([c["course_code"], Paragraph(title, cell), str(c["credit_hours"]), f"{c['total_obtained']:g}/{c['total_max']:g}", f"{c['percentage']:.1f}", c["letter_grade"], _f(c["grade_points"]), _f(c["quality_points"])])
        rows.append(["", Paragraph("<b>Term total</b>", cell), str(term["credits_attempted"]), "", "", "GPA", _f(term["gpa"]), ""])
        rows.append(["", Paragraph(f"<b>Cumulative (CGPA)</b>  \u2014  {term['credits_earned_cumulative']} credit hours earned so far", cell), "", "", "", "CGPA", _f(term["cgpa_after"]), ""])
        tbl = Table(rows, colWidths=[20 * mm, 71 * mm, 9 * mm, 20 * mm, 14 * mm, 14 * mm, 14 * mm, 14 * mm], repeatRows=1)
        style = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                 ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5), ("TEXTCOLOR", (0, 1), (-1, -1), INK), ("ALIGN", (2, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("LINEBELOW", (0, 0), (-1, -3), 0.3, LINE), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("BACKGROUND", (0, -2), (-1, -1), colors.HexColor("#eef2fb")),
                 ("FONT", (5, -2), (6, -1), "Helvetica-Bold", 8.5), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        for i, c in enumerate(term["courses"], start=1):
            if c["outcome"] != "PASSED":
                style.append(("TEXTCOLOR", (5, i), (5, i), RED))
            if c["replaced"]:
                style.append(("TEXTCOLOR", (0, i), (4, i), MUTED))
        tbl.setStyle(TableStyle(style))
        story.append(tbl)

    story.append(Spacer(1, 10))
    box = Table([[f"Cumulative GPA (CGPA): {_f(summ['cgpa'])}", f"Credit hours attempted: {summ['credits_attempted']}", f"Credit hours earned: {summ['credits_earned']}",
                  (f"Courses still to clear: {summ['courses_to_clear']}" if summ["courses_to_clear"] else "All courses cleared")]], colWidths=[48 * mm, 44 * mm, 44 * mm, 46 * mm])
    box.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica-Bold", 8.6), ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("BOX", (0, 0), (-1, -1), 1, BLUE), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef2fb")),
                             ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story.append(box)
    if not data["terms"]:
        story.append(Paragraph("No results have been published yet.", small))
    story += [Spacer(1, 8), Paragraph("<b>Grading scale</b>: " + "  \u00b7  ".join(f"{e['letter']} {e['min_percent']:g}%+ = {e['grade_points']:.2f}" for e in data["scale"]), small),
              Paragraph("Cr = credit hours, GP = grade points, QP = quality points (GP \u00d7 Cr). GPA = total quality points \u00f7 total credit hours. A repeated course replaces its earlier attempt in the CGPA.", small)]

    issued = data["issued_at"]
    stamp = issued.strftime("%d %b %Y") if isinstance(issued, datetime) else str(issued)

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.3); canvas.setFillColor(MUTED)
        canvas.drawString(15 * mm, 9 * mm, f"Computer-generated on {stamp}. Valid without signature.")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()