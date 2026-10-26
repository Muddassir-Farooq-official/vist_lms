"""Merit list for the notice board (A4): rank, application no., name, father/guardian, percentages, aggregate. No CNICs."""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def render_merit_list_pdf(m: dict, institute: str | None = None) -> bytes:
    from app.core import brand

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Merit list {m['list_no']} {m['program']} {m['term_label']}")
    ss = getSampleStyleSheet()
    w = m["weights"]
    story = brand.platypus_header(A4[0] - 28 * mm) + [
             Paragraph(f"Merit list {m['list_no']} — {m['program_name']} ({m['program']}), {m['term_label']}", ss["Heading2"]),
             Paragraph(f"Aggregate = matric {w['matric']:g}% + intermediate {w['inter']:g}% + entry test {w['test']:g}%. "
                       f"Selected candidates must complete admission by <b>{m['deadline']:%d %B %Y}</b>, or the seat goes to the next merit list.",
                       ss["BodyText"]), Spacer(1, 6 * mm)]
    rows = [["#", "Application", "Name", "Father / guardian", "Matric %", "Inter %", "Test %", "Aggregate"]]
    for r in m["rows"]:
        rows.append([str(r["merit_rank"]), r["application_no"], r["name"], r["father_or_guardian"] or "",
                     f"{r['matric_percent']:.2f}", f"{r['inter_percent']:.2f}", "" if r["test_percent"] is None else f"{r['test_percent']:.2f}",
                     f"{r['aggregate']:.3f}"])
    t = Table(rows, repeatRows=1, colWidths=[9 * mm, 30 * mm, 38 * mm, 38 * mm, 16 * mm, 16 * mm, 15 * mm, 20 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf5")), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5cde0")),
                           ("ALIGN", (4, 1), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(t)
    doc.build(story)
    return buf.getvalue()