"""Offline check that a profile photo shows one clear, uncovered face (OpenCV's bundled detectors; nothing is sent anywhere).

A photo is refused, with a plain reason the person can act on, when:
- no face is found, or more than one person is in it;
- the face is too small (too far from the camera) or the photo is very dark / blurred;
- the eyes cannot be found (sunglasses, a veil over the eyes, eyes closed or turned away);
- the lower face is covered (a mask or niqab): the mouth / chin area does not show skin the way the forehead and cheeks do.

It is a first filter, not the final word: every photo that passes still waits for an administrator's approval.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_SIDE = 200          # pixels
MAX_SIDE = 900          # the photo is scaled down to this before checking (fast, and enough detail)


@dataclass
class FaceResult:
    ok: bool
    reason: str = ""
    detail: dict | None = None


_cascades: dict = {}


def _cascade(name: str):
    import cv2

    if name not in _cascades:
        c = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        if c.empty():
            raise RuntimeError(f"OpenCV detector {name} is missing")
        _cascades[name] = c
    return _cascades[name]


def _skin_ratio(ycrcb, x0: int, y0: int, x1: int, y1: int) -> float:
    import cv2
    import numpy as np

    region = ycrcb[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)]
    if region.size == 0:
        return 0.0
    mask = cv2.inRange(region, np.array((0, 133, 77), dtype=np.uint8), np.array((255, 173, 127), dtype=np.uint8))
    return float((mask > 0).mean())


def check_face(content: bytes) -> FaceResult:
    try:
        import cv2
        import numpy as np
    except ImportError:                       # the library is not installed: let the administrator decide
        return FaceResult(True, "", {"checked": False})

    img = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return FaceResult(False, "This file could not be read as a photo. Upload a JPG or PNG.")
    h, w = img.shape[:2]
    if min(h, w) < MIN_SIDE:
        return FaceResult(False, f"The photo is too small ({w}×{h}). Use a photo at least {MIN_SIDE} pixels on each side.")
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))

    raw = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if raw.mean() < 45:
        return FaceResult(False, "The photo is too dark. Take it again in good light, facing a window or a lamp.")
    if cv2.Laplacian(raw, cv2.CV_64F).var() < 25:
        return FaceResult(False, "The photo is blurred. Hold the camera still and take it again.")

    min_face = int(min(h, w) * 0.18)
    faces = list(_cascade("haarcascade_frontalface_default.xml").detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_face, min_face)))
    if not faces:
        faces = list(_cascade("haarcascade_frontalface_alt2.xml").detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(min_face, min_face)))
    if not faces:
        return FaceResult(False, "No clear face was found. Face the camera, keep your whole face uncovered and visible, and use good light.")
    faces.sort(key=lambda f: f[2] * f[3], reverse=True)
    if len(faces) > 1 and faces[1][2] * faces[1][3] > 0.35 * faces[0][2] * faces[0][3]:
        return FaceResult(False, "More than one person is in the photo. Only you should be in it.")
    x, y, fw, fh = (int(v) for v in faces[0])
    if fw < 0.22 * w:
        return FaceResult(False, "Your face is too small in the photo. Come closer so your head and shoulders fill it.")

    # Eyes, in the upper half of the face (plain, then the detector that copes with ordinary glasses).
    upper = gray[y:y + int(fh * 0.55), x:x + fw]
    eye_min = max(int(fw * 0.1), 8)
    eyes = _cascade("haarcascade_eye.xml").detectMultiScale(upper, scaleFactor=1.1, minNeighbors=5, minSize=(eye_min, eye_min))
    if len(eyes) == 0:
        eyes = _cascade("haarcascade_eye_tree_eyeglasses.xml").detectMultiScale(upper, scaleFactor=1.1, minNeighbors=4, minSize=(eye_min, eye_min))
    if len(eyes) == 0:
        return FaceResult(False, "Your eyes are not visible. Remove sunglasses or anything covering your eyes, and look at the camera.")

    # Lower face: compare skin in the mouth / chin area with skin on the cheeks and forehead.
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    cx0, cx1 = x + int(fw * 0.2), x + int(fw * 0.8)
    top = _skin_ratio(ycrcb, cx0, y + int(fh * 0.12), cx1, y + int(fh * 0.30)) * 0.5 + \
        _skin_ratio(ycrcb, x + int(fw * 0.12), y + int(fh * 0.45), x + int(fw * 0.88), y + int(fh * 0.62)) * 0.5
    bottom = _skin_ratio(ycrcb, cx0, y + int(fh * 0.68), cx1, y + int(fh * 0.98))
    detail = {"checked": True, "faces": len(faces), "eyes": int(len(eyes)), "skin_upper": round(top, 3), "skin_lower": round(bottom, 3)}
    if top >= 0.25 and bottom < 0.45 * top:
        return FaceResult(False, "Your mouth and chin seem to be covered (a mask or veil). The photo must show your whole face, uncovered.", detail)
    return FaceResult(True, "", detail)
