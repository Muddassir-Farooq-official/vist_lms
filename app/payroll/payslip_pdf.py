"""One employee's salary slip as a PDF."""
from __future__ import annotations

import io
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.fees.challan_pdf import amount_in_words

INK, MUTED, BLUE, GREEN, RED, LINE = colors.HexColor("#0f1b3d"), colors.HexColor("#5b6785"), colors.HexColor("#2454d6"), colors.HexColor("#17803d"), colors.HexColor("#c62828"), colors.HexColor("#d5dbe8")
METHOD = {"CASH": "Cash", "BANK_TRANSFER": "Bank transfer", "CHEQUE": "Cheque", "ONLINE": "Online"}


def _m(v) -> str:
    return f"{float(v):,.2f}"


def render_payslip_pdf(p: dict, *, institute_name: str | None = None) -> bytes:
    from app.core import brand

    institute_name = institute_name or brand.name()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title=f"Salary slip {p['month_label']}", author=institute_name)
    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=17, textColor=INK, spaceAfter=1)
    sub = ParagraphStyle("sub", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=10, textColor=BLUE, alignment=1, spaceAfter=10)
    small = ParagraphStyle("small", parent=base["Normal"], fontName="Helvetica", fontSize=8, textColor=MUTED, leading=11)
    story = brand.platypus_header(A4[0] - 36 * mm) + [Paragraph(f"SALARY SLIP \u2014 {p['month_label'].upper()}", sub)]
    _ = h1
    info = [["Name", p["employee_name"], "Employee code", p["employee_code"]], ["Designation", p["job_title"] or "\u2014", "Department", p.get("department") or "\u2014"],
            ["Days paid", f"{p['days_payable']} of {p['days_in_month']}", "Unpaid days", f"{p['unpaid_days']:g}"]]
    t = Table(info, colWidths=[28 * mm, 58 * mm, 30 * mm, 58 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9), ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 9), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                           ("TEXTCOLOR", (0, 0), (0, -1), MUTED), ("TEXTCOLOR", (2, 0), (2, -1), MUTED), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fc")),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [t, Spacer(1, 8)]
    earn, ded = p["earnings"], p["deduction_lines"]
    rows = [["EARNINGS", "Rs.", "DEDUCTIONS", "Rs."]]
    for i in range(max(len(earn), len(ded), 1)):
        e, d = (earn[i] if i < len(earn) else None), (ded[i] if i < len(ded) else None)
        rows.append([Paragraph(e["label"], small) if e else "", _m(e["amount"]) if e else "", Paragraph(d["label"], small) if d else "", _m(d["amount"]) if d else ""])
    rows.append(["Total earnings", _m(p["gross"]), "Total deductions", _m(p["deductions"])])
    tbl = Table(rows, colWidths=[62 * mm, 24 * mm, 62 * mm, 26 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), BLUE), ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                             ("TEXTCOLOR", (0, 1), (-1, -1), INK), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("LINEAFTER", (1, 0), (1, -1), 0.6, LINE), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LINEBELOW", (0, 1), (-1, -3), 0.3, LINE), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eef2fb")),
                             ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [tbl, Spacer(1, 10)]
    net = Table([[f"NET PAY: Rs. {_m(p['net'])}"], [Paragraph(f"Rupees {amount_in_words(float(p['net']))}", small)]], colWidths=[174 * mm])
    net.setStyle(TableStyle([("FONT", (0, 0), (0, 0), "Helvetica-Bold", 14), ("TEXTCOLOR", (0, 0), (0, 0), GREEN), ("BOX", (0, 0), (-1, -1), 1, GREEN), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0faf3")),
                             ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story.append(net)
    pd = p.get("paid_date")
    story += [Spacer(1, 8), Paragraph(f"Paid on {pd:%d %b %Y} by {METHOD.get(getattr(p.get('payment_method'), 'value', p.get('payment_method')), '')}." if isinstance(pd, date) else "", small)]

    def footer(canvas, document):
        canvas.saveState(); canvas.setFont("Helvetica", 7.3); canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm, brand.fit_text(canvas, f"Computer-generated on {datetime.now():%d %b %Y}. Valid without signature."
                                                          + (f"  \u00b7  {brand.platypus_footer_text()}" if brand.platypus_footer_text() else ""), "Helvetica", 7.3, A4[0] - 60 * mm))
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, "Confidential")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()