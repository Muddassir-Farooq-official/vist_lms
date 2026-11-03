"""The institute's identity as every part of the system sees it: pages (Jinja `brand`), PDFs (the header, footer,
signature and bank-detail helpers below), reports and SMS.

Values live in `settings` and are refreshed from Settings -> Institute (app.branding.service.apply_from_db).
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from app.core.config import settings


# ====================================================================== plain values
def name() -> str:
    return settings.institute_name or "Institute"


def short_name() -> str:
    return settings.app_name or name()


def address_line() -> str:
    return ", ".join(x for x in (settings.institute_address, settings.institute_city) if x)


def contact_line() -> str:
    return "  ·  ".join(x for x in (settings.institute_phone, settings.institute_email, settings.institute_website) if x)


def logo_path(dark: bool = False) -> Path | None:
    p = Path(settings.institute_logo_dark_path if dark and settings.institute_logo_dark_path else settings.institute_logo_path or "")
    return p if str(p) and p.is_file() else None


def signature_path() -> Path | None:
    p = Path(settings.institute_signature_path or "")
    return p if str(p) and p.is_file() else None


def color(default: str = "#14307a") -> str:
    return settings.brand_color or default


def initials() -> str:
    words = [w for w in short_name().replace("-", " ").split() if w[:1].isalnum()]
    return (words[0][:1] if words else "I").upper()


def bank_lines() -> list[str]:
    out = []
    if settings.bank_name or settings.bank_branch:
        out.append(" — ".join(x for x in (settings.bank_name, settings.bank_branch) if x))
    if settings.bank_account_title:
        out.append(f"Account title: {settings.bank_account_title}")
    if settings.bank_account_no:
        out.append(f"Account no.: {settings.bank_account_no}")
    return out


# ====================================================================== pages
def _mix(hex_color: str, other: tuple[int, int, int], t: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r, g, b = (round(a + (o - a) * t) for a, o in zip((r, g, b), other))
    return f"#{r:02x}{g:02x}{b:02x}"


def theme_css() -> str:
    """CSS variables for the institute's main colour (empty when none is set, so the standard theme stays)."""
    c = settings.brand_color
    if not c or len(c) != 7:
        return ""
    black, white = (0, 0, 0), (255, 255, 255)
    return (":root{"
            f"--blue-600:{c};--blue-700:{_mix(c, black, .2)};--blue-500:{_mix(c, white, .15)};"
            f"--blue-100:{_mix(c, white, .82)};--blue-50:{_mix(c, white, .92)};"
            f"--sidebar-bg:{_mix(c, black, .62)};--sidebar-bg-alt:{_mix(c, black, .5)};"
            f"--drawer-top:{c};--drawer-bottom:{_mix(c, black, .4)};"
            f"--accent-blue-icon:{c};--accent-blue-bg:{_mix(c, white, .9)};"
            "}")


class BrandView:
    """`brand` in every template: always the current values (attributes are read on each use)."""

    @property
    def name(self) -> str:
        return name()

    @property
    def short_name(self) -> str:
        return short_name()

    @property
    def tagline(self) -> str:
        return settings.institute_tagline or ""

    @property
    def address(self) -> str:
        return address_line()

    @property
    def contact(self) -> str:
        return contact_line()

    @property
    def initials(self) -> str:
        return initials()

    @property
    def has_logo(self) -> bool:
        return logo_path() is not None

    @property
    def logo_url(self) -> str:
        return f"/branding/logo.png?v={settings.brand_version}"

    @property
    def has_logo_dark(self) -> bool:
        return bool(settings.institute_logo_dark_path) and Path(settings.institute_logo_dark_path).is_file()

    @property
    def logo_dark_url(self) -> str:
        return f"/branding/logo-dark.png?v={settings.brand_version}"

    @property
    def favicon_url(self) -> str:
        return f"/branding/favicon.png?v={settings.brand_version}"

    @property
    def theme_css(self) -> str:
        return theme_css()

    @property
    def footer(self) -> str:
        return settings.document_footer or ""


# ====================================================================== PDFs
def _colors():
    from reportlab.lib import colors

    return colors.HexColor(color()), colors.HexColor("#5b6785")


def draw_header(c, *, x: float, y_top: float, width: float, title: str | None = None, subtitle: str | None = None,
                logo_h: float | None = None, center: bool = True, compact: bool = False) -> float:
    """Logo + institute name (+ address, contact) + an optional document title, on a reportlab canvas.
    Returns the y below the header."""
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader

    ink, muted = _colors()
    logo_h = logo_h or (10 * mm if compact else 16 * mm)
    lp = logo_path()
    name_size = 10.5 if compact else 15
    text_x, text_w = x, width
    if lp is not None:
        img = ImageReader(str(lp))
        iw, ih = img.getSize()
        lw = logo_h * iw / ih
        if center:
            c.drawImage(img, x + (width - lw) / 2, y_top - logo_h, lw, logo_h, mask="auto")
            # The name's baseline sits a cap height below the logo, so the letters never touch it.
            y = y_top - logo_h - 1.5 * mm - name_size * 0.75
        else:
            c.drawImage(img, x, y_top - logo_h, lw, logo_h, mask="auto")
            text_x, text_w = x + lw + 3 * mm, width - lw - 3 * mm
            y = y_top - (4.5 * mm if compact else 6 * mm)
    else:
        y = y_top - (4 * mm if compact else 6 * mm)
    n = fit_text(c, name(), "Helvetica-Bold", name_size, text_w)
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", name_size)
    (c.drawCentredString(text_x + text_w / 2, y, n) if center else c.drawString(text_x, y, n))
    y -= (4 if compact else 5.2) * mm
    c.setFillColor(muted)
    for line, size in ((settings.institute_tagline, 7.5 if compact else 9), (address_line(), 7 if compact else 8.5), (contact_line(), 7 if compact else 8.5)):
        if not line:
            continue
        c.setFont("Helvetica", size)
        t = fit_text(c, line, "Helvetica", size, text_w)
        (c.drawCentredString(text_x + text_w / 2, y, t) if center else c.drawString(text_x, y, t))
        y -= (3.4 if compact else 4.2) * mm
    if lp is not None and not center:
        y = min(y, y_top - logo_h - 2 * mm)
    if title:
        y -= 2 * mm
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 9.5 if compact else 12)
        (c.drawCentredString(x + width / 2, y, title) if center else c.drawString(x, y, title))
        y -= 5 * mm
    if subtitle:
        c.setFillColor(muted)
        c.setFont("Helvetica", 8)
        (c.drawCentredString(x + width / 2, y, subtitle) if center else c.drawString(x, y, subtitle))
        y -= 4 * mm
    return y


def draw_footer(c, *, x: float, y: float, width: float, extra: str | None = None, size: float = 7) -> None:
    _ink, muted = _colors()
    text = "  ·  ".join(t for t in (settings.document_footer, extra) if t)
    if not text:
        return
    c.setFillColor(muted)
    c.setFont("Helvetica", size)
    c.drawCentredString(x + width / 2, y, fit_text(c, text, "Helvetica", size, width))


def draw_signature(c, *, x: float, y: float, width: float = 50, label: str | None = None) -> None:
    """The signatory's signature image (when uploaded) above a line with their name and title. (x, y) = left end of the line."""
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader

    ink, muted = _colors()
    sp = signature_path()
    w = width * mm if width < 200 else width
    if sp is not None:
        img = ImageReader(str(sp))
        iw, ih = img.getSize()
        h = min(14 * mm, w * ih / iw)
        c.drawImage(img, x + (w - h * iw / ih) / 2, y + 1 * mm, h * iw / ih, h, mask="auto")
    c.setStrokeColor(muted)
    c.line(x, y, x + w, y)
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 8)
    who = settings.institute_signatory_name or ""
    title = label or settings.institute_signatory_title or "Authorised signatory"
    if who:
        c.drawCentredString(x + w / 2, y - 4 * mm, who)
        c.setFont("Helvetica", 7.5)
        c.setFillColor(muted)
        c.drawCentredString(x + w / 2, y - 7.5 * mm, title)
    else:
        c.drawCentredString(x + w / 2, y - 4 * mm, title)


def fit_text(c, text: str, font: str, size: float, width: float) -> str:
    text = text or ""
    if c.stringWidth(text, font, size) <= width:
        return text
    while text and c.stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def platypus_header(width: float, *, title: str | None = None, subtitle: str | None = None) -> list:
    """The same header for SimpleDocTemplate documents (payslip, transcript, admit card, merit list, timetable)."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

    ink, muted = _colors()
    h1 = ParagraphStyle("bh1", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=ink, alignment=1)
    small = ParagraphStyle("bsm", fontName="Helvetica", fontSize=8.5, leading=11, textColor=muted, alignment=1)
    ttl = ParagraphStyle("bt", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=ink, alignment=1, spaceBefore=3)
    text = [Paragraph(escape(name()), h1)]
    for line in (settings.institute_tagline, address_line(), contact_line()):
        if line:
            text.append(Paragraph(escape(line), small))
    lp = logo_path()
    out: list = []
    if lp is not None:
        from reportlab.lib.utils import ImageReader

        iw, ih = ImageReader(str(lp)).getSize()
        lh = 16 * mm
        lw = min(lh * iw / ih, 40 * mm)
        logo = Image(str(lp), width=lw, height=lw * ih / iw)
        t = Table([[logo, text, ""]], colWidths=[lw + 3 * mm, width - 2 * (lw + 3 * mm), lw + 3 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        out.append(t)
    else:
        out += text
    if title:
        out.append(Paragraph(escape(title), ttl))
    if subtitle:
        out.append(Paragraph(escape(subtitle), small))
    out.append(Spacer(1, 3 * mm))
    return out


def platypus_footer_text() -> str:
    return settings.document_footer or ""