"""Uploaded pictures are stored smaller, in the same format, never refused; the one-off script shrinks old ones safely."""
from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app.core import storage as st


def _photo(w=4000, h=3000, fmt="JPEG", mode="RGB", exif_orientation=None) -> bytes:
    img = Image.new(mode, (w, h))
    # noisy content so the camera-size file is realistically large
    img.putdata([((x * 7) % 256, (x * 13) % 256, (x * 3) % 256)[: len(mode)] if mode != "L" else (x * 7) % 256 for x in range(w * h)])
    out = io.BytesIO()
    kw = {}
    if exif_orientation:
        ex = Image.Exif()
        ex[0x0112] = exif_orientation
        kw["exif"] = ex
    img.save(out, format=fmt, quality=95, **kw) if fmt != "PNG" else img.save(out, format=fmt)
    return out.getvalue()


def _size(b: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(b)) as im:
        return im.size


def test_a_camera_photo_becomes_a_small_profile_picture():
    big = _photo()
    small = st.shrink_image(big, "image/jpeg", st.max_side_for("student-photos"))
    assert max(_size(small)) == 600 and len(small) < len(big) / 5
    assert Image.open(io.BytesIO(small)).format == "JPEG"


def test_documents_keep_more_detail_than_profile_pictures():
    big = _photo()
    assert st.max_side_for("pending-photos") == 600 and st.max_side_for("person-documents") == 1600
    assert max(_size(st.shrink_image(big, "image/jpeg", st.max_side_for("person-documents")))) == 1600


def test_phone_rotation_is_applied_and_hidden_details_dropped():
    big = _photo(800, 400, exif_orientation=6)  # "rotate 90": the phone held upright
    small = st.shrink_image(big, "image/jpeg", 600)
    with Image.open(io.BytesIO(small)) as im:
        assert im.size[1] > im.size[0]
        assert 0x0112 not in im.getexif()


def test_png_and_webp_stay_png_and_webp():
    for fmt, ctype in (("PNG", "image/png"), ("WEBP", "image/webp")):
        small = st.shrink_image(_photo(1800, 1800, fmt=fmt), ctype, 600)
        with Image.open(io.BytesIO(small)) as im:
            assert im.format == fmt and max(im.size) == 600


def test_small_or_damaged_pictures_are_kept_as_they_came():
    tiny = _photo(200, 150)
    assert st.shrink_image(tiny, "image/jpeg", 600) == tiny or len(st.shrink_image(tiny, "image/jpeg", 600)) < len(tiny)
    broken = b"\xff\xd8\xff" + b"not really a picture"
    assert st.shrink_image(broken, "image/jpeg", 600) == broken
    assert st.shrink_image(b"%PDF-1.4 ...", "application/pdf", 600) == b"%PDF-1.4 ..."


def test_uploads_are_saved_small(tmp_path, monkeypatch):
    monkeypatch.setattr(st.settings, "storage_local_path", str(tmp_path))
    big = _photo(2400, 1800)
    assert len(big) < st.MAX_PHOTO_BYTES
    key, _ = st.save_uploaded_file(category="student-photos", entity_id=uuid.uuid4(), content=big, original_filename="me.jpg",
                                   content_type="image/jpeg", allowed_content_types=st.ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=st.MAX_PHOTO_BYTES)
    assert key.endswith(".jpg") and max(_size((tmp_path / key).read_bytes())) == 600
    key2, _, ctype = st.save_attachment(entity_id=uuid.uuid4(), content=big, original_filename="slip.jpg", declared_content_type="image/jpeg", folder="expenses")
    assert ctype == "image/jpeg" and max(_size((tmp_path / key2).read_bytes())) == 1600


def test_the_one_off_script_keeps_originals_and_skips_branding(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("shrink", Path(__file__).resolve().parents[2] / "scripts" / "shrink_stored_images.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    storage, keep = tmp_path / "storage", tmp_path / "originals"
    big = _photo()
    for rel in ("student-photos/a/1.jpg", "person-documents/b/2.jpg", "branding/logo.jpg"):
        (storage / rel).parent.mkdir(parents=True, exist_ok=True)
        (storage / rel).write_bytes(big)
    (storage / "student-certificates").mkdir()
    (storage / "student-certificates" / "c.pdf").write_bytes(b"%PDF-1.4 x")

    n, before, after = mod.run(storage, keep, apply=False)
    assert n == 2 and after < before and (storage / "student-photos/a/1.jpg").read_bytes() == big   # look-only changes nothing

    n, _, _ = mod.run(storage, keep, apply=True)
    assert n == 2
    assert max(_size((storage / "student-photos/a/1.jpg").read_bytes())) == 600
    assert max(_size((storage / "person-documents/b/2.jpg").read_bytes())) == 1600
    assert (keep / "student-photos/a/1.jpg").read_bytes() == big and (keep / "person-documents/b/2.jpg").read_bytes() == big
    assert (storage / "branding/logo.jpg").read_bytes() == big
    assert not list(storage.rglob("*.shrinking"))
    assert mod.run(storage, keep, apply=True)[0] == 0   # running again does nothing


@pytest.mark.parametrize("category", ["student-photos", "employee-photos", "pending-photos"])
def test_every_photo_category_uses_the_profile_size(category):
    assert st.max_side_for(category) == st.PROFILE_PHOTO_MAX_SIDE
