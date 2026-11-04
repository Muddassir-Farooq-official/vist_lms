"""Web Push, sent by this server itself (no OneSignal or Firebase account): RFC 8291 message encryption (aes128gcm)
and RFC 8292 VAPID signing. Works with Chrome / Edge / Firefox / Samsung on Android and desktop, and Safari on
iPhone / iPad (iOS 16.4+) once the app is added to the Home Screen.

Keys: VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT in .env (scripts/push_keys.py makes them). Without them
push is simply off and nothing else changes.
"""
from __future__ import annotations

import base64
import os
import struct
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _public_bytes(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def generate_vapid_keys() -> tuple[str, str]:
    """(public, private) as base64url: the public key goes to browsers, the private key stays in .env."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64u(_public_bytes(key)), b64u(private)


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """The message body for one device, encrypted so only that device can read it (RFC 8291, aes128gcm)."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_key = ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(as_key)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("Push message too long")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public + ciphertext


def vapid_headers(endpoint: str, *, public_key: str, private_key: str, subject: str) -> dict:
    """Authorization header proving this server sent the message (RFC 8292)."""
    import jwt

    parts = urlsplit(endpoint)
    key = ec.derive_private_key(int.from_bytes(unb64u(private_key), "big"), ec.SECP256R1())
    token = jwt.encode({"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject}, key, algorithm="ES256")
    return {"Authorization": f"vapid t={token}, k={public_key}"}