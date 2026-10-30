"""Encrypt a small secret (the SMTP password) before it is stored in the database, using only the standard library.

Construction: encrypt-then-MAC with HMAC-SHA256 as the pseudo-random function.
  keys     enc_key, mac_key = HMAC-SHA256(JWT_SECRET, "vist-secretbox/enc" | "/mac")
  stream   HMAC-SHA256(enc_key, nonce || counter) blocks, XORed with the plaintext (PRF in counter mode)
  tag      HMAC-SHA256(mac_key, version || nonce || ciphertext), checked in constant time before decrypting
  stored   "v1:" + urlsafe-base64(nonce(16) || ciphertext || tag(32))
Changing JWT_SECRET makes stored secrets unreadable: open() then returns None and the administrator re-enters them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from app.core.config import settings

_VERSION = b"v1"


def _keys() -> tuple[bytes, bytes]:
    root = (settings.jwt_secret or "").encode()
    return (hmac.new(root, b"vist-secretbox/enc", hashlib.sha256).digest(), hmac.new(root, b"vist-secretbox/mac", hashlib.sha256).digest())


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def seal(plain: str) -> str:
    enc, mac = _keys()
    data = plain.encode()
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data))))
    tag = hmac.new(mac, _VERSION + nonce + ct, hashlib.sha256).digest()
    return "v1:" + base64.urlsafe_b64encode(nonce + ct + tag).decode()


def open_(sealed: str | None) -> str | None:
    """The secret, or None when there is none or it cannot be read (tampered, or JWT_SECRET changed)."""
    if not sealed or not sealed.startswith("v1:"):
        return None
    try:
        raw = base64.urlsafe_b64decode(sealed[3:].encode())
    except Exception:
        return None
    if len(raw) < 48:
        return None
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    enc, mac = _keys()
    if not hmac.compare_digest(tag, hmac.new(mac, _VERSION + nonce + ct, hashlib.sha256).digest()):
        return None
    return bytes(a ^ b for a, b in zip(ct, _stream(enc, nonce, len(ct)))).decode(errors="replace")