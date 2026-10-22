"""Refuse an identical POST repeated within a few seconds (double-clicked buttons, flaky mobile retries).

Same person + same address + same body inside `duplicate_post_window_seconds` is almost always an accident, and for
money, marks and attendance an accidental twin is expensive. Works across worker processes through Redis; if Redis
is unreachable the guard steps aside rather than blocking real work.
"""
from __future__ import annotations

import hashlib
import json

from app.core.config import settings

_MAX_BODY = 2 * 1024 * 1024
_SKIP_PREFIXES = ("/api/v1/auth/",)


class DuplicateSubmissionGuard:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        window = int(getattr(settings, "duplicate_post_window_seconds", 0) or 0)
        if scope["type"] != "http" or scope["method"] != "POST" or window <= 0 or scope["path"].startswith(_SKIP_PREFIXES):
            return await self.app(scope, receive, send)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        try:
            if int(headers.get("content-length") or 0) > _MAX_BODY:
                return await self.app(scope, receive, send)
        except ValueError:
            pass

        chunks: list[bytes] = []
        more = True
        size = 0
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks)

        async def replay():
            nonlocal body
            data, body = body, b""
            return {"type": "http.request", "body": data, "more_body": False}

        if size <= _MAX_BODY:
            who = headers.get("authorization") or next((c.strip() for c in headers.get("cookie", "").split(";") if c.strip().startswith("access_token=")), "")
            key = "dup:" + hashlib.sha256(b"\0".join([who.encode(), scope["path"].encode(), scope.get("query_string", b""), body])).hexdigest()
            if await _already_seen(key, window):
                payload = json.dumps({"error": {"code": "DUPLICATE_SUBMISSION", "details": None,
                                                "message": "This was already sent a moment ago. Wait a few seconds before trying again."}}).encode()
                await send({"type": "http.response.start", "status": 409, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
                await send({"type": "http.response.body", "body": payload})
                return
        return await self.app(scope, replay, send)


async def _already_seen(key: str, window: int) -> bool:
    try:
        from app.core.redis import get_redis

        return not await get_redis().set(key, "1", nx=True, ex=window)
    except Exception:  # noqa: BLE001 - never block real work because Redis hiccuped
        return False
