from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings

MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_CERTIFICATE_PDF_BYTES = 20 * 1024 * 1024  # 20 MB

ALLOWED_PHOTO_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_PDF_CONTENT_TYPES = {"application/pdf"}

# NOTE: X-Content-Type-Options: nosniff is already applied to every
# response globally by request_context_middleware in app/main.py, so this
# does not newly close a gap. It's kept explicit here anyway, at the
# file-serving call sites specifically, as defense-in-depth that doesn't
# depend on the global middleware staying wired up — the actual fix for
# the "client-declared content-type was trusted with no check against the
# real bytes" finding is the signature verification below
# (_verify_file_signature), which runs at upload time before anything is
# written to disk.
SAFE_SERVE_HEADERS = {"X-Content-Type-Options": "nosniff"}


class UnsupportedFileTypeError(ValueError):
    pass


class FileTooLargeError(ValueError):
    pass


def _extension_for(original_filename: str, content_type: str) -> str:
    if content_type == "application/pdf":
        return ".pdf"
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    return ".jpg"


# --------------------------------------------------------------------------
# File-signature ("magic bytes") verification.
#
# Security fix: `content_type` here is whatever the client's multipart
# request declared in its Content-Type field — it is NOT derived from the
# actual file bytes and is entirely attacker-controlled. Previously this
# module trusted that label outright, so a request could label arbitrary
# bytes (e.g. HTML containing a <script> tag) as "image/jpeg" and have them
# accepted, stored with a .jpg extension, and later served back inline with
# a server-forced "image/jpeg" media type. Forcing the response media type
# closes most of that, but relying on a label the attacker chose — with no
# check that the bytes actually are what they claim — is the wrong control
# to depend on by itself. These signature checks verify the bytes actually
# match the claimed type before anything is written to disk.
# --------------------------------------------------------------------------
def _looks_like_jpeg(content: bytes) -> bool:
    return content[:3] == b"\xff\xd8\xff"


def _looks_like_png(content: bytes) -> bool:
    return content[:8] == b"\x89PNG\r\n\x1a\n"


def _looks_like_webp(content: bytes) -> bool:
    return content[:4] == b"RIFF" and content[8:12] == b"WEBP"


def _looks_like_pdf(content: bytes) -> bool:
    # The %PDF- header is required to appear within the first 1024 bytes
    # per the PDF spec (some generators pad before it); checking a short
    # prefix window is both spec-correct and cheap.
    return b"%PDF-" in content[:1024]


_SIGNATURE_CHECKS = {
    "image/jpeg": _looks_like_jpeg,
    "image/png": _looks_like_png,
    "image/webp": _looks_like_webp,
    "application/pdf": _looks_like_pdf,
}


def _verify_file_signature(content: bytes, content_type: str) -> None:
    check = _SIGNATURE_CHECKS.get(content_type)
    if check is None:
        # Shouldn't happen — content_type is already validated against an
        # allowlist by the caller before this runs — but fail closed rather
        # than silently accepting an unrecognized type.
        raise UnsupportedFileTypeError(f"Unrecognized file type '{content_type}'.")
    if not check(content):
        raise UnsupportedFileTypeError(
            f"File contents do not match the declared type '{content_type}'. "
            "The uploaded file may be corrupted, mislabeled, or not a genuine "
            f"{content_type} file."
        )


# --------------------------------------------------------------------------
# Smaller pictures. Phone cameras send 3-5 MB photos; nobody needs that on a profile picture or a scanned slip.
# Pictures are scaled down and re-saved in the SAME format (so stored types, extensions and links never change), with
# the phone's rotation applied and its hidden details (location, camera) dropped. If anything goes wrong, or the result
# would not be smaller, the original is kept: a picture is never refused because of this.
# --------------------------------------------------------------------------
PROFILE_PHOTO_MAX_SIDE = 600     # student / staff pictures
DOCUMENT_IMAGE_MAX_SIDE = 1600   # scans and photos of documents: text stays readable
IMAGE_QUALITY = 80
_SHRINK_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def max_side_for(category: str) -> int:
    return PROFILE_PHOTO_MAX_SIDE if category.endswith("photos") else DOCUMENT_IMAGE_MAX_SIDE


def shrink_image(content: bytes, content_type: str, max_side: int) -> bytes:
    fmt = _SHRINK_FORMATS.get(content_type)
    if fmt is None or not content:
        return content
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(content)) as im:
            if getattr(im, "is_animated", False) or im.format != fmt:
                return content
            img = ImageOps.exif_transpose(im)
            img.thumbnail((max_side, max_side), Image.LANCZOS)
            out = io.BytesIO()
            if fmt == "JPEG":
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(out, format="JPEG", quality=IMAGE_QUALITY, optimize=True, progressive=True)
            elif fmt == "WEBP":
                img.save(out, format="WEBP", quality=IMAGE_QUALITY, method=6)
            else:
                img.save(out, format="PNG", optimize=True)
        data = out.getvalue()
    except Exception:  # noqa: BLE001 - a damaged or unusual picture is stored as it came
        return content
    return data if 0 < len(data) < len(content) else content


def save_uploaded_file(
    *, category: str, entity_id: uuid.UUID, content: bytes, original_filename: str, content_type: str,
    allowed_content_types: set[str], max_bytes: int,
) -> tuple[str, str]:
    """Saves an uploaded file to local disk under a server-generated
    path (never the user-supplied filename — path traversal prevention,
    same discipline as every other upload previously in this system)
    and returns (storage_key, safe_filename_to_display). `category` is
    a short fixed label like 'student-photos' or 'employee-certificates'
    so different upload types don't collide on disk.

    Local-disk-only for now (STORAGE_BACKEND=local, the only backend
    actually implemented) — swapping to S3/R2 later means changing only
    this function, callers are unaffected."""
    if content_type not in allowed_content_types:
        raise UnsupportedFileTypeError(f"Unsupported file type '{content_type}'. Allowed: {', '.join(sorted(allowed_content_types))}.")
    if len(content) > max_bytes:
        raise FileTooLargeError(f"File is too large — max {max_bytes // (1024 * 1024)} MB.")
    # Bytes must actually match the declared type — see _verify_file_signature above.
    _verify_file_signature(content, content_type)
    content = shrink_image(content, content_type, max_side_for(category))

    ext = _extension_for(original_filename, content_type)
    storage_key = f"{category}/{entity_id}/{uuid.uuid4().hex}{ext}"

    full_path = Path(settings.storage_local_path) / storage_key
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)

    safe_name = Path(original_filename).name[:255] or f"file{ext}"
    return storage_key, safe_name


def get_file_path(storage_key: str | None) -> Path | None:
    if not storage_key:
        return None
    path = Path(settings.storage_local_path) / storage_key
    return path if path.exists() else None


def archive_file(storage_key: str | None) -> str | None:
    """Keep a file that is being replaced or removed: it is moved under archive/ (same relative path) instead of being deleted,
    and the new key is returned. Returns None when there was nothing to keep."""
    if not storage_key:
        return None
    path = Path(settings.storage_local_path) / storage_key
    if not path.exists():
        return None
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    key = f"archive/{stamp}/{storage_key}"
    target = Path(settings.storage_local_path) / key
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
    except OSError:
        return None
    return key


def delete_file(storage_key: str | None) -> None:
    if not storage_key:
        return
    path = Path(settings.storage_local_path) / storage_key
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass  # best-effort — a stale DB pointer to a missing file shouldn't break the request


# --------------------------------------------------------------------------
# Announcement attachments: PDF, images and Office documents.
#
# Same discipline as above: the file's real bytes must match what it claims to be, and the stored name is generated by
# the server. Office files (docx/xlsx/pptx) are zip containers, so they are opened and checked for the parts every genuine
# document has -- an arbitrary zip renamed to .docx is refused. Old binary Office files (doc/xls/ppt) must carry the OLE
# signature. Nothing executable, no archives, no HTML.
# --------------------------------------------------------------------------
import io  # noqa: E402
import zipfile  # noqa: E402

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MB per file
MAX_ATTACHMENTS_PER_ANNOUNCEMENT = 8

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _looks_like_ooxml(prefix: str):
    def check(content: bytes) -> bool:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = z.namelist()
        except (zipfile.BadZipFile, OSError):
            return False
        return "[Content_Types].xml" in names and any(n.startswith(prefix) for n in names)

    return check


def _looks_like_ole(content: bytes) -> bool:
    return content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# content type -> (extension, signature check, may be shown inside the browser)
ATTACHMENT_TYPES = {
    "application/pdf": (".pdf", _looks_like_pdf, True),
    "image/jpeg": (".jpg", _looks_like_jpeg, True),
    "image/png": (".png", _looks_like_png, True),
    "image/webp": (".webp", _looks_like_webp, True),
    _DOCX: (".docx", _looks_like_ooxml("word/"), False),
    _XLSX: (".xlsx", _looks_like_ooxml("xl/"), False),
    _PPTX: (".pptx", _looks_like_ooxml("ppt/"), False),
    "application/msword": (".doc", _looks_like_ole, False),
    "application/vnd.ms-excel": (".xls", _looks_like_ole, False),
    "application/vnd.ms-powerpoint": (".ppt", _looks_like_ole, False),
}
_EXTENSION_TO_TYPE = {
    ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".docx": _DOCX, ".xlsx": _XLSX, ".pptx": _PPTX, ".doc": "application/msword", ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}
ATTACHMENT_HELP = "PDF, JPG/PNG/WebP images, or Word / Excel / PowerPoint files"


def validate_attachment(*, content: bytes, original_filename: str, declared_content_type: str | None) -> tuple[str, str]:
    """Checks a file without storing it. Returns (content_type, extension) or raises a plain-language error."""
    suffix = Path(original_filename or "").suffix.lower()
    content_type = declared_content_type if declared_content_type in ATTACHMENT_TYPES else _EXTENSION_TO_TYPE.get(suffix)
    if content_type is None or content_type not in ATTACHMENT_TYPES:
        raise UnsupportedFileTypeError(f"This kind of file is not allowed. Attach {ATTACHMENT_HELP}.")
    if not content:
        raise UnsupportedFileTypeError("The file is empty.")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise FileTooLargeError(f"File is too large \u2014 max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB each.")
    ext, check, _inline = ATTACHMENT_TYPES[content_type]
    if not check(content):
        raise UnsupportedFileTypeError("The file's contents do not match its type. It may be damaged or not a genuine document.")
    return content_type, ext


def save_attachment(
    *, entity_id: uuid.UUID, content: bytes, original_filename: str, declared_content_type: str | None, folder: str = "announcements"
) -> tuple[str, str, str]:
    """Stores an attachment (announcements, applications...). Returns (storage_key, display_name, content_type)."""
    content_type, ext = validate_attachment(content=content, original_filename=original_filename, declared_content_type=declared_content_type)
    content = shrink_image(content, content_type, DOCUMENT_IMAGE_MAX_SIDE)
    storage_key = f"{folder}/{entity_id}/{uuid.uuid4().hex}{ext}"
    full_path = Path(settings.storage_local_path) / storage_key
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    safe_name = Path(original_filename).name[:255] or f"file{ext}"
    return storage_key, safe_name, content_type


def attachment_is_inline(content_type: str) -> bool:
    """PDF and images may open inside the browser; Office files are always downloaded."""
    return ATTACHMENT_TYPES.get(content_type, (None, None, False))[2]