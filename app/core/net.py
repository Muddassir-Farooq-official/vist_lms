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
    """Walk X-Forwarded-For from the RIGHT (the end our own proxies wrote) and return the first address that is not a
    trusted proxy. The left part of the header is whatever the visitor sent and is never believed: nginx's
    `$proxy_add_x_forwarded_for` keeps a forged value at the front and appends the real address."""
    peer = getattr(getattr(request, "client", None), "host", None) or "unknown"
    if not _is_trusted(peer):
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer                      # a broken header: do not guess
        if not _is_trusted(hop):
            return hop
    return peer
