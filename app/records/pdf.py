"""Certificates and ID cards as PDFs. Every one carries a number and a QR code that opens the public check page.

ID cards show the institute's logo and full name, the person's photo (a neutral silhouette when there is none), their details
and the QR code. The logo is read from `settings.institute_logo_path` (default static/img/logo.png); until that file exists a
simple lettered badge is drawn instead, so cards still print.
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core.config import settings

INK, MUTED, LINE = colors.HexColor("#0f1b3d"), colors.HexColor("#5b6785"), colors.HexColor("#c5cde0")
NAVY, TEAL = colors.HexColor("#14307a"), colors.HexColor("#0b6b5d")
TITLES = {"BONAFIDE": "BONAFIDE CERTIFICATE", "CHARACTER": "CHARACTER CERTIFICATE", "COMPLETION": "COURSE COMPLETION CERTIFICATE", "PROVISIONAL": "PROVISIONAL CERTIFICATE",
          "MIGRATION": "MIGRATION CERTIFICATE / NOC", "FEE_CLEARANCE": "FEE CLEARANCE CERTIFICATE", "EXPERIENCE": "EXPERIENCE CERTIFICATE"}
PHOTO_PX = (330, 420)            # 22 x 28 mm at about 380 dpi


def _qr(c: canvas.Canvas, url: str, x: float, y: float, size: float) -> None:
    widget = qr.QrCodeWidget(url)
    x0, y0, x1, y1 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, x, y)


def _wrap(c: canvas.Canvas, text: str, font: str, size: float, width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if c.stringWidth(trial, font, size) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit(c: canvas.Canvas, text: str, font: str, size: float, width: float) -> str:
    """Shorten `text` with an ellipsis so it fits `width`."""
    if c.stringWidth(text, font, size) <= width:
        return text
    while text and c.stringWidth(text + "\u2026", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "\u2026"


def _logo_reader() -> ImageReader | None:
    from app.core import brand

    path = brand.logo_path() or Path(settings.institute_logo_path)       # Settings -> Institute -> Logo
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    try:
        return ImageReader(str(path)) if path.is_file() else None
    except Exception:  # noqa: BLE001 - a bad logo file must not stop cards printing
        return None


def _draw_logo(c: canvas.Canvas, x: float, y: float, size: float, logo: ImageReader | None) -> None:
    """The logo, kept in proportion and centred in a `size` square. A lettered badge stands in until a logo file is added."""
    if logo is not None:
        iw, ih = logo.getSize()
        scale = min(size / iw, size / ih)
        w, h = iw * scale, ih * scale
        c.drawImage(logo, x + (size - w) / 2, y + (size - h) / 2, w, h, mask="auto", preserveAspectRatio=True)
        return
    from app.core import brand

    c.saveState()
    c.setFillColor(colors.HexColor(brand.color("#14307a")))
    c.circle(x + size / 2, y + size / 2, size / 2, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", size * 0.8)
    c.drawCentredString(x + size / 2, y + size * 0.3, brand.initials())
    c.restoreState()


def _photo_reader(storage_key: str | None) -> ImageReader | None:
    """The person's stored photo, cropped to the card's portrait shape. None when missing or unreadable."""
    if not storage_key:
        return None
    try:
        from PIL import Image, ImageOps

        from app.core.storage import get_file_path

        path = get_file_path(storage_key)
        if path is None:
            return None
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = ImageOps.fit(im, PHOTO_PX, method=Image.LANCZOS, centering=(0.5, 0.35))     # keep the face: crop towards the top
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
        buf.seek(0)
        return ImageReader(buf)
    except Exception:  # noqa: BLE001
        return None


def _draw_photo(c: canvas.Canvas, x: float, y: float, w: float, h: float, photo: ImageReader | None) -> None:
    if photo is not None:
        c.drawImage(photo, x, y, w, h)
    else:                                                                       # neutral silhouette
        c.saveState()
        c.setFillColor(colors.HexColor("#e6eaf3"))
        c.rect(x, y, w, h, stroke=0, fill=1)
        c.setFillColor(colors.HexColor("#b4bdd3"))
        c.circle(x + w / 2, y + h * 0.64, w * 0.2, stroke=0, fill=1)
        p = c.beginPath()
        p.moveTo(x + w * 0.14, y)
        p.curveTo(x + w * 0.14, y + h * 0.36, x + w * 0.86, y + h * 0.36, x + w * 0.86, y)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.restoreState()
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(x, y, w, h, stroke=1, fill=0)


def render_certificate_pdf(*, institute: str, kind: str, reference: str, code: str, verify_url: str, paragraphs: list[str], issued_on: date, signatory: str = "Principal / Registrar") -> bytes:
    buf = io.BytesIO()
    w, h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{TITLES.get(kind, kind)} {reference}")
    c.setStrokeColor(INK)
    c.setLineWidth(1.4)
    c.rect(10 * mm, 10 * mm, w - 20 * mm, h - 20 * mm)
    c.setStrokeColor(LINE)
    c.rect(13 * mm, 13 * mm, w - 26 * mm, h - 26 * mm)
    _draw_logo(c, w / 2 - 12 * mm, h - 40 * mm, 24 * mm, _logo_reader())
    from app.core import brand

    c.setFillColor(INK)
    size = 19
    while size > 12 and c.stringWidth(institute.upper(), "Helvetica-Bold", size) > w - 40 * mm:
        size -= 0.5
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(w / 2, h - 50 * mm, institute.upper())
    c.setFont("Helvetica", 8.5)
    c.setFillColor(MUTED)
    sub = "  ·  ".join(x for x in (brand.address_line(), brand.contact_line()) if x)
    if sub:
        c.drawCentredString(w / 2, h - 55 * mm, _fit(c, sub, "Helvetica", 8.5, w - 40 * mm))
    if settings.institute_registration_no:
        c.drawCentredString(w / 2, h - 59 * mm, _fit(c, settings.institute_registration_no, "Helvetica", 8.5, w - 40 * mm))
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(w / 2, h - 63 * mm, TITLES.get(kind, kind))
    c.setStrokeColor(INK)
    c.line(w / 2 - 35 * mm, h - 66 * mm, w / 2 + 35 * mm, h - 66 * mm)
    c.setFont("Helvetica", 9.5)
    c.setFillColor(MUTED)
    c.drawString(24 * mm, h - 78 * mm, f"No. {reference}")
    c.drawRightString(w - 24 * mm, h - 78 * mm, f"Date: {issued_on.strftime('%d %B %Y')}")
    y = h - 98 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica", 12)
    for para in paragraphs:
        for line in _wrap(c, para, "Helvetica", 12, w - 48 * mm):
            c.drawString(24 * mm, y, line)
            y -= 7.5 * mm
        y -= 4 * mm
    _qr(c, verify_url, 24 * mm, 30 * mm, 28 * mm)
    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    c.drawString(24 * mm, 26 * mm, f"Check this certificate online: code {code}")
    # The signatory's signature image (when uploaded), name and title (Settings -> Institute).
    brand.draw_signature(c, x=w - 80 * mm, y=38 * mm, width=56, label=settings.institute_signatory_title or signatory)
    brand.draw_footer(c, x=24 * mm, y=17 * mm, width=w - 48 * mm)
    c.showPage()
    c.save()
    return buf.getvalue()


def _card_back(c: canvas.Canvas, x: float, y: float, cw: float, ch: float, back: dict, logo, sig, accent) -> None:
    """The back of a card: the institute's logo (faint, in the middle), numbered instructions, where to return a found
    card, and the signatory."""
    c.setStrokeColor(LINE)
    c.setFillColor(colors.white)
    c.roundRect(x, y, cw, ch, 3 * mm, stroke=1, fill=1)
    size, wy = 26 * mm, y + 19 * mm                                         # the logo, faint, behind the instructions
    _draw_logo(c, x + (cw - size) / 2, wy, size, logo)
    c.saveState()
    c.setFillColor(colors.white)
    c.setFillAlpha(0.86)
    c.rect(x + (cw - size) / 2 - 1, wy - 1, size + 2, size + 2, stroke=0, fill=1)
    c.restoreState()
    # title band
    c.saveState()
    clip = c.beginPath()
    clip.roundRect(x, y, cw, ch, 3 * mm)
    c.clipPath(clip, stroke=0, fill=0)
    c.setFillColor(accent)
    c.rect(x, y + ch - 6.5 * mm, cw, 6.5 * mm, stroke=0, fill=1)
    c.restoreState()
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(x + cw / 2, y + ch - 4.4 * mm, "INSTRUCTIONS")
    # instructions, numbered, wrapped
    c.setFillColor(INK)
    top, floor = y + ch - 10 * mm, y + 18.5 * mm
    size = 6.6
    lines = []
    for i, text in enumerate(back.get("instructions") or [], 1):
        parts = _wrap(c, text, "Helvetica", size, cw - 10 * mm)
        lines += [(f"{i}.", parts[0])] + [("", p) for p in parts[1:]]
    if len(lines) * 3.2 * mm > top - floor:                                 # many lines: a little smaller
        size = 5.4
        lines = []
        for i, text in enumerate(back.get("instructions") or [], 1):
            parts = _wrap(c, text, "Helvetica", size, cw - 10 * mm)
            lines += [(f"{i}.", parts[0])] + [("", p) for p in parts[1:]]
    step = (3.2 if size > 6 else 2.5) * mm
    yy = top
    for num, text in lines:
        if yy < floor:
            break
        c.setFont("Helvetica-Bold", size)
        c.drawString(x + 3.5 * mm, yy, num)
        c.setFont("Helvetica", size)
        c.drawString(x + 6.5 * mm, yy, _fit(c, text, "Helvetica", size, cw - 10 * mm))
        yy -= step
    # return address + signature
    c.setStrokeColor(LINE)
    c.setLineWidth(0.4)
    c.line(x + 3.5 * mm, y + 16.5 * mm, x + cw - 3.5 * mm, y + 16.5 * mm)
    _draw_logo(c, x + 3.5 * mm, y + 4.5 * mm, 9 * mm, logo)            # the logo (or the lettered badge)
    tx = x + 14 * mm
    tw = cw - (tx - x) - 27 * mm
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 5.6)
    c.drawString(tx, y + 13.3 * mm, "If found, please return to:")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 6.2)
    c.drawString(tx, y + 10.5 * mm, _fit(c, back.get("institute") or "", "Helvetica-Bold", 6.2, tw))
    c.setFont("Helvetica", 5.6)
    row = 7.9
    for part in _wrap(c, back.get("address") or "", "Helvetica", 5.6, tw)[:2]:
        c.drawString(tx, y + row * mm, part)
        row -= 2.4
    contact = "  ".join(v for v in ((f"Ph: {back['phone']}" if back.get("phone") else ""), back.get("website") or "") if v)
    if contact:
        c.drawString(tx, y + row * mm, _fit(c, contact, "Helvetica", 5.6, tw))
    sx = x + cw - 25.5 * mm
    if sig is not None:
        try:
            c.drawImage(sig, sx + 1 * mm, y + 6.6 * mm, 20 * mm, 7 * mm, preserveAspectRatio=True, mask="auto")
        except Exception:  # noqa: BLE001 - a bad signature file must not stop the cards
            pass
    c.line(sx, y + 6 * mm, sx + 22 * mm, y + 6 * mm)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 5.6)
    c.drawCentredString(sx + 11 * mm, y + 3.6 * mm, _fit(c, back.get("signatory") or "Principal", "Helvetica", 5.6, 22 * mm))


def _signature_reader():
    from app.core import brand

    p = brand.signature_path()
    try:
        return ImageReader(str(p)) if p else None
    except Exception:  # noqa: BLE001
        return None


def render_id_cards_pdf(*, institute: str, cards: list[dict], back: dict | None = None) -> bytes:
    """Credit-card sized cards (85.6 x 54 mm), eight to an A4 page, with cut marks.

    Each card: logo + full institute name in the header, photo, name, details, QR code, number and validity.
    Students get a navy header, staff a green one.
    With `back`, every page of fronts is followed by a page of backs placed for double-sided printing (flip on the long
    edge): the back of the card on the left is on the right, so each back lands behind its own front.
    """
    buf = io.BytesIO()
    w, h = A4
    cw, ch = 85.6 * mm, 54 * mm
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("ID cards")
    from app.core import brand

    logo = _logo_reader()
    words = institute.upper().split()
    half = (len(words) + 1) // 2
    head1, head2 = " ".join(words[:half]), " ".join(words[half:])           # "VERTEX INSTITUTE OF" / "SCIENCE & TECHNOLOGY"
    sig = _signature_reader() if back else None

    def backs(page_cards: list[dict]) -> None:
        c.showPage()
        for j, pc in enumerate(page_cards):
            bx = 14 * mm + (1 - j % 2) * (cw + 6 * mm)                     # mirrored: left front -> right back
            by = h - 14 * mm - ch - (j // 2) * (ch + 6 * mm)
            accent_b = TEAL if pc.get("kind") == "STAFF" else colors.HexColor(brand.color("#14307a"))
            _card_back(c, bx, by, cw, ch, back, logo, sig, accent_b)

    for i, card in enumerate(cards):
        pos = i % 8
        if i and pos == 0:
            if back:
                backs(cards[i - 8:i])
            c.showPage()
        x = 14 * mm + (pos % 2) * (cw + 6 * mm)
        y = h - 14 * mm - ch - (pos // 2) * (ch + 6 * mm)
        staff = card.get("kind") == "STAFF"
        accent = TEAL if staff else colors.HexColor(brand.color("#14307a"))

        c.setStrokeColor(LINE)
        c.setFillColor(colors.white)
        c.roundRect(x, y, cw, ch, 3 * mm, stroke=1, fill=1)

        # header band: logo on a white tile, full institute name
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y, cw, ch, 3 * mm)
        c.clipPath(clip, stroke=0, fill=0)
        c.setFillColor(accent)
        c.rect(x, y + ch - 14 * mm, cw, 14 * mm, stroke=0, fill=1)
        c.restoreState()
        c.setFillColor(colors.white)
        c.roundRect(x + 2.5 * mm, y + ch - 12.5 * mm, 11 * mm, 11 * mm, 1.6 * mm, stroke=0, fill=1)
        _draw_logo(c, x + 3 * mm, y + ch - 12 * mm, 10 * mm, logo)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 8.2)
        c.drawString(x + 16 * mm, y + ch - 6.2 * mm, _fit(c, head1, "Helvetica-Bold", 8.2, cw - 18 * mm))
        c.drawString(x + 16 * mm, y + ch - 10.4 * mm, _fit(c, head2, "Helvetica-Bold", 8.2, cw - 18 * mm))

        # photo
        _draw_photo(c, x + 3.5 * mm, y + 8.5 * mm, 21 * mm, 27 * mm, _photo_reader(card.get("photo_key")))

        # role tag, name, details
        tx, tw = x + 28 * mm, cw - 28 * mm - 20 * mm            # leave the right edge for the QR code
        c.setFillColor(accent)
        c.setFont("Helvetica-Bold", 6.5)
        c.drawString(tx, y + ch - 18 * mm, "FACULTY / STAFF" if staff else "STUDENT")
        c.setFillColor(INK)
        size = 10.5
        while size > 7 and c.stringWidth(card["name"], "Helvetica-Bold", size) > tw + 18 * mm:
            size -= 0.5
        c.setFont("Helvetica-Bold", size)
        c.drawString(tx, y + ch - 22.5 * mm, _fit(c, card["name"], "Helvetica-Bold", size, tw + 18 * mm))
        c.setFillColor(MUTED)
        # "Label : value" rows. Anything not on file prints as N/A, so a missing CNIC or phone is visible rather than a blank.
        offset = 26.6                                                         # mm from the top of the card
        fields = card.get("fields") or [("", ln) for ln in card.get("lines", [])]
        for label, value in fields[:5]:
            text = str(value).strip() if value not in (None, "") and str(value).strip() else "N/A"
            head = f"{label} : " if label else ""
            c.setFont("Helvetica-Bold", 6.8)
            hw = c.stringWidth(head, "Helvetica-Bold", 6.8)
            c.setFillColor(MUTED)
            c.drawString(tx, y + ch - offset * mm, head)
            c.setFillColor(INK)
            vsize = 7.0
            if c.stringWidth(text, "Helvetica", vsize) <= tw - hw:
                c.setFont("Helvetica", vsize)
                c.drawString(tx + hw, y + ch - offset * mm, text)
                offset += 3.9
                continue
            vsize = 6.4                                                       # too long for one line: up to two smaller lines
            parts = _wrap(c, text, "Helvetica", vsize, tw - hw)
            c.setFont("Helvetica", vsize)
            for part in parts[:2]:
                c.drawString(tx + hw, y + ch - offset * mm, _fit(c, part, "Helvetica", vsize, tw - hw))
                offset += 3.2
            offset += 0.7

        # QR + footer
        _qr(c, card["verify_url"], x + cw - 20.5 * mm, y + 8 * mm, 17.5 * mm)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.4)
        c.line(x + 3.5 * mm, y + 6.4 * mm, x + cw - 3.5 * mm, y + 6.4 * mm)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 6)
        c.drawString(x + 3.5 * mm, y + 3.2 * mm, f"{card['reference']}  \u00b7  valid to {card['valid_to']}")
        c.drawRightString(x + cw - 3.5 * mm, y + 3.2 * mm, "Scan to verify")
    if back and cards:
        backs(cards[(len(cards) - 1) // 8 * 8:])
    c.showPage()
    c.save()
    return buf.getvalue()