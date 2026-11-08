"""Link previews: when the portal's address (or the "what's new" address) is shared on WhatsApp, Facebook, email...,
the app shows a card with a picture, a title and a line of text. Those come from the Open Graph tags in the page
(templates/_share_meta.html) and the picture drawn here (1200 x 630, the size every app expects).

WhatsApp and the others fetch the page without signing in, so only public pages carry the tags: the sign-in page and
the status page (/health). The picture needs the site's full public address (PUBLIC_URL in .env).
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from app.core import brand
from app.core.config import settings

W, H = 1200, 630
_FONT = Path(__file__).resolve().parents[2] / "static" / "fonts" / "outfit-latin-variable.woff2"


def site_url(request=None) -> str:
    """The address people use, without a trailing slash: PUBLIC_URL, else the first CORS origin, else this request's."""
    url = (settings.public_url or "").strip() or next((o.strip() for o in (settings.cors_origins or "").split(",") if o.strip()), "")
    if not url and request is not None:
        url = str(request.base_url)
    return url.rstrip("/")


def _font(px: int, weight: str = "Bold"):
    from PIL import ImageFont

    try:
        f = ImageFont.truetype(str(_FONT), px)
        try:
            f.set_variation_by_name(weight)
        except Exception:  # noqa: BLE001 - a static font has no named weights
            pass
        return f
    except OSError:
        return ImageFont.load_default(size=px)


def _hex(c: str) -> tuple[int, int, int]:
    c = (c or "#14307a").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return 20, 48, 122


def _fit(draw, text: str, max_w: int, px: int, weight: str = "Bold"):
    while px > 24:
        f = _font(px, weight)
        if draw.textlength(text, font=f) <= max_w:
            return f
        px -= 4
    return _font(px, weight)


@lru_cache(maxsize=16)
def preview_png(kind: str, version: str, brand_version: str) -> bytes:
    from PIL import Image, ImageDraw

    base = _hex(brand.color("#14307a"))
    img = Image.new("RGB", (W, H), base)
    # soft diagonal light, so the card is not a flat block of colour
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    for i in range(0, 520, 8):
        gd.ellipse((W - 520 + i // 2, -420 + i // 2, W + 420 - i // 2, 420 - i // 2), fill=int(i / 520 * 60))
    img.paste(Image.new("RGB", (W, H), (255, 255, 255)), (0, 0), glow)
    d = ImageDraw.Draw(img)

    # logo (or the initials) in a white rounded square
    box = 150
    x0, y0 = 80, 80
    d.rounded_rectangle((x0, y0, x0 + box, y0 + box), radius=34, fill=(255, 255, 255))
    logo = brand.logo_path()
    drawn = False
    if logo is not None:
        try:
            mark = Image.open(logo).convert("RGBA")
            mark.thumbnail((box - 34, box - 34), Image.LANCZOS)
            img.paste(mark, (x0 + (box - mark.width) // 2, y0 + (box - mark.height) // 2), mark)
            drawn = True
        except Exception:  # noqa: BLE001 - a broken logo file falls back to the initials
            drawn = False
    if not drawn:
        letters = brand.initials()
        f = _font(72)
        b = d.textbbox((0, 0), letters, font=f)
        d.text((x0 + (box - (b[2] - b[0])) / 2 - b[0], y0 + (box - (b[3] - b[1])) / 2 - b[1]), letters, font=f, fill=base)

    white, soft = (255, 255, 255), (220, 228, 245)
    d.text((x0 + box + 36, y0 + 22), brand.short_name(), font=_fit(d, brand.short_name(), W - x0 - box - 116, 64), fill=white)
    d.text((x0 + box + 38, y0 + 100), brand.name(), font=_fit(d, brand.name(), W - x0 - box - 116, 32, "Medium"), fill=soft)

    if kind == "version":
        head, sub = f"Version {version}", "See what's new"
    else:
        head, sub = "Student & staff portal", "Sign in to your account"
    d.text((80, 360), head, font=_fit(d, head, W - 160, 84), fill=white)
    d.text((82, 470), sub, font=_font(40, "Medium"), fill=soft)
    # a thin accent line at the bottom
    d.rectangle((0, H - 14, W, H), fill=(245, 180, 0))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def meta(request, *, kind: str, title: str, description: str, path: str) -> dict:
    """What templates/_share_meta.html needs."""
    from app.core.releases import current_version

    url = site_url(request)
    v = current_version()
    return {"title": title, "description": description, "url": url + path,
            "image": f"{url}/share/preview.png?kind={kind}&v={v}&b={settings.brand_version}", "site_name": brand.short_name()}
