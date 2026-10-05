from __future__ import annotations

import uuid

import pytest

from app.core.storage import (
    ALLOWED_PDF_CONTENT_TYPES,
    ALLOWED_PHOTO_CONTENT_TYPES,
    UnsupportedFileTypeError,
    save_uploaded_file,
)

# Minimal-but-genuine file signatures. We don't need full valid files —
# only the header bytes the signature check actually inspects — since the
# check is deliberately a cheap magic-byte test, not a full decode.
REAL_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
REAL_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
REAL_WEBP_BYTES = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"\x00" * 32
REAL_PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 32

# An attacker-controlled payload wearing a fake label.
HTML_WITH_SCRIPT = b"<html><body><script>alert(document.cookie)</script></body></html>"


@pytest.fixture(autouse=True)
def _use_tmp_storage(tmp_path, monkeypatch):
    """Redirect local storage to a throwaway directory for these tests."""
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "storage_local_path", str(tmp_path))


class TestSignatureVerificationAcceptsGenuineFiles:
    def test_genuine_jpeg_with_correct_label_is_accepted(self):
        storage_key, safe_name = save_uploaded_file(
            category="student-photos", entity_id=uuid.uuid4(), content=REAL_JPEG_BYTES,
            original_filename="photo.jpg", content_type="image/jpeg",
            allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=5 * 1024 * 1024,
        )
        assert storage_key.endswith(".jpg")
        assert safe_name == "photo.jpg"

    def test_genuine_png_with_correct_label_is_accepted(self):
        storage_key, _ = save_uploaded_file(
            category="student-photos", entity_id=uuid.uuid4(), content=REAL_PNG_BYTES,
            original_filename="photo.png", content_type="image/png",
            allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=5 * 1024 * 1024,
        )
        assert storage_key.endswith(".png")

    def test_genuine_webp_with_correct_label_is_accepted(self):
        storage_key, _ = save_uploaded_file(
            category="student-photos", entity_id=uuid.uuid4(), content=REAL_WEBP_BYTES,
            original_filename="photo.webp", content_type="image/webp",
            allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=5 * 1024 * 1024,
        )
        assert storage_key.endswith(".webp")

    def test_genuine_pdf_with_correct_label_is_accepted(self):
        storage_key, _ = save_uploaded_file(
            category="student-certificates", entity_id=uuid.uuid4(), content=REAL_PDF_BYTES,
            original_filename="cert.pdf", content_type="application/pdf",
            allowed_content_types=ALLOWED_PDF_CONTENT_TYPES, max_bytes=20 * 1024 * 1024,
        )
        assert storage_key.endswith(".pdf")


class TestSignatureVerificationRejectsSpoofedContentType:
    """The core regression test for the fix: a request that lies about
    what it's uploading (declares an allowed content-type while sending
    different bytes) must be rejected, not silently accepted and stored."""

    def test_html_labeled_as_jpeg_is_rejected(self):
        with pytest.raises(UnsupportedFileTypeError):
            save_uploaded_file(
                category="student-photos", entity_id=uuid.uuid4(), content=HTML_WITH_SCRIPT,
                original_filename="totally-a-photo.jpg", content_type="image/jpeg",
                allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=5 * 1024 * 1024,
            )

    def test_html_labeled_as_pdf_is_rejected(self):
        with pytest.raises(UnsupportedFileTypeError):
            save_uploaded_file(
                category="student-certificates", entity_id=uuid.uuid4(), content=HTML_WITH_SCRIPT,
                original_filename="totally-a-cert.pdf", content_type="application/pdf",
                allowed_content_types=ALLOWED_PDF_CONTENT_TYPES, max_bytes=20 * 1024 * 1024,
            )

    def test_pdf_bytes_labeled_as_jpeg_is_rejected(self):
        """Cross-family mismatch: real bytes, but not the type they claim to be."""
        with pytest.raises(UnsupportedFileTypeError):
            save_uploaded_file(
                category="student-photos", entity_id=uuid.uuid4(), content=REAL_PDF_BYTES,
                original_filename="photo.jpg", content_type="image/jpeg",
                allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=5 * 1024 * 1024,
            )

    def test_empty_content_is_rejected(self):
        with pytest.raises(UnsupportedFileTypeError):
            save_uploaded_file(
                category="student-photos", entity_id=uuid.uuid4(), content=b"",
                original_filename="photo.jpg", content_type="image/jpeg",
                allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=5 * 1024 * 1024,
            )

    def test_declared_type_outside_allowlist_still_rejected_before_signature_check(self):
        """Pre-existing behavior must be unaffected: an outright disallowed
        content-type is still rejected at the allowlist stage."""
        with pytest.raises(UnsupportedFileTypeError):
            save_uploaded_file(
                category="student-photos", entity_id=uuid.uuid4(), content=REAL_JPEG_BYTES,
                original_filename="photo.gif", content_type="image/gif",
                allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=5 * 1024 * 1024,
            )