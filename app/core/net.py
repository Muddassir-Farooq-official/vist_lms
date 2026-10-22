"""The visitor's real network address.

Behind nginx / Docker every request appears to come from the proxy, which would make the per-address login
limiter and the audit trail useless. `X-Forwarded-For` is believed ONLY when the connection really comes from a
trusted proxy (settings.trusted_proxies); from anyone else the header is ignored, so it cannot be faked.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from app.core.config import settings


@lru_cache(maxsize=8)
def _networks(raw: str) -> tuple:
    nets = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _is_trusted(peer: str) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _networks(settings.trusted_proxies))


def client_ip(request) -> str:
    peer = getattr(getattr(request, "client", None), "host", None) or "unknown"
    if not _is_trusted(peer):
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    first = forwarded.split(",")[0].strip()
    try:
        ipaddress.ip_address(first)
    except ValueError:
        return peer
    return first
