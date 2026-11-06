"""What makes the portal installable as an app: the web app manifest, the app icons (made from the institute's logo,
or its initial in the institute's colour), and the service worker that shows push notifications.

All public (they are fetched before sign-in, by the browser itself) and contain nothing private.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from app.core import brand
from app.core.config import settings

router = APIRouter(include_in_schema=False)
SIZES = (192, 512)
SW_PATH = Path(__file__).resolve().parents[2] / "static" / "js" / "service-worker.js"


def _font(px: int):
    from PIL import ImageFont

    import reportlab

    bold = Path(reportlab.__file__).parent / "fonts" / "VeraBd.ttf"            # shipped with reportlab, so always there
    try:
        return ImageFont.truetype(str(bold), px)
    except OSError:
        return ImageFont.load_default()


@lru_cache(maxsize=16)
def _icon(size: int, maskable: bool, version: str) -> bytes:
    """A square PNG. Maskable icons keep everything inside the middle 80% (phones cut the corners into a circle or squircle)."""
    from PIL import Image, ImageDraw

    colour = brand.color("#1d4ed8")
    logo = brand.logo_path()
    if logo is not None:
        img = Image.new("RGBA", (size, size), "#ffffff")
        try:
            mark = Image.open(logo).convert("RGBA")
            room = int(size * (0.62 if maskable else 0.78))
            mark.thumbnail((room, room), Image.LANCZOS)
            img.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
        except Exception:  # noqa: BLE001 - a broken logo file falls back to the initial
            logo = None
    if logo is None:
        img = Image.new("RGBA", (size, size), colour)
        draw = ImageDraw.Draw(img)
        letter = brand.initials()
        font = _font(int(size * (0.42 if maskable else 0.52)))
        box = draw.textbbox((0, 0), letter, font=font)
        draw.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), letter, font=font, fill="#ffffff")
    if not maskable:                                      # plain icons get rounded corners; maskable ones are cut by the phone
        from PIL import ImageDraw as D

        mask = Image.new("L", (size, size), 0)
        D.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=255)
        img.putalpha(mask)
    out = io.BytesIO()
    img.convert("RGBA").save(out, "PNG", optimize=True)
    return out.getvalue()


@router.get("/branding/app-icon-{size}.png")
async def app_icon(size: int, maskable: bool = False) -> Response:
    if size not in SIZES and size != 180:
        return Response(status_code=404)
    body = _icon(size, maskable, str(settings.brand_version))
    return Response(content=body, media_type="image/png", headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.get("/manifest.webmanifest")
async def manifest() -> JSONResponse:
    v = settings.brand_version
    name = brand.short_name()
    icons = [{"src": f"/branding/app-icon-{s}.png?v={v}", "sizes": f"{s}x{s}", "type": "image/png", "purpose": "any"} for s in SIZES]
    icons += [{"src": f"/branding/app-icon-{s}.png?maskable=1&v={v}", "sizes": f"{s}x{s}", "type": "image/png", "purpose": "maskable"} for s in SIZES]
    body = {
        "name": brand.name() if brand.name() != name else name, "short_name": name[:12],
        "description": f"{brand.name()} portal: classes, attendance, results, fees, applications and notices.",
        "id": "/", "start_url": "/?source=app", "scope": "/", "display": "standalone", "orientation": "portrait-primary",
        "background_color": "#f2f4f8", "theme_color": brand.color("#1d4ed8"), "icons": icons,
        "categories": ["education", "productivity"],
    }
    return JSONResponse(body, media_type="application/manifest+json", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sw.js")
async def service_worker() -> Response:
    # Served from the site root so it can control every page (a worker only reaches pages under its own folder).
    return Response(content=SW_PATH.read_text(encoding="utf-8"), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/", "X-Content-Type-Options": "nosniff"})


@router.get("/offline")
async def offline_page() -> Response:
    """The page the app shows when the phone has no connection, or the portal is not answering (e.g. during an update).
    The service worker keeps a copy of it; it holds nothing private, only the institute's name and colour."""
    import html

    colour = html.escape(brand.color("#1d4ed8"))
    name, short = html.escape(brand.name()), html.escape(brand.short_name())
    initials = html.escape(brand.initials())
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="{colour}"><title>Offline &mdash; {short}</title>
<style>
  :root {{ --brand: {colour}; --bg: #f2f4f8; --card: #ffffff; --text: #0f172a; --muted: #475569; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0b1220; --card: #111a2e; --text: #e2e8f0; --muted: #94a3b8; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: var(--bg); color: var(--text);
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  .card {{ width: 100%; max-width: 420px; background: var(--card); border-radius: 20px; padding: 32px 24px; text-align: center;
          box-shadow: 0 10px 30px rgba(15, 23, 42, .08); }}
  .chip {{ width: 64px; height: 64px; border-radius: 18px; background: var(--brand); color: #fff; display: grid; place-items: center;
          font-weight: 800; font-size: 24px; margin: 0 auto 16px; }}
  h1 {{ font-size: 1.35rem; margin: 0 0 8px; }}
  p {{ color: var(--muted); margin: 0 0 20px; }}
  button {{ appearance: none; border: 0; border-radius: 12px; background: var(--brand); color: #fff; font: inherit; font-weight: 700;
           padding: 12px 22px; min-width: 160px; cursor: pointer; }}
  small {{ display: block; margin-top: 18px; color: var(--muted); }}
</style></head>
<body><main class="card" role="main">
  <div class="chip" aria-hidden="true">{initials}</div>
  <h1 id="off-title">You're offline</h1>
  <p id="off-text">{short} needs an internet connection. Check your mobile data or Wi-Fi; the page will open by itself as soon as you are back online.</p>
  <button type="button" id="off-retry">Try again</button>
  <small>{name}</small>
</main><script src="/static/js/offline.js"></script></body></html>"""
    return Response(content=body, media_type="text/html", headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})