from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailService:
    """STUB — there is no SMTP/SES/SendGrid credential configured
    anywhere in this system (see app/core/config.py: no email-related
    settings exist at all today). Calling send() logs what WOULD have
    been sent and returns without actually sending anything — this is
    intentional, not a bug: better to have a clearly-visible no-op than
    to silently fail against a real provider with fake/missing
    credentials.

    To make this real once you have provider credentials:
    1. Add the provider's settings to app/core/config.py (e.g.
       smtp_host, smtp_port, smtp_username, smtp_password, from_email
       for plain SMTP; or an API key for SendGrid/SES).
    2. Replace the body of send() below with the actual provider call.
    3. Nothing else changes — every caller already goes through this
       one method and one import path."""

    async def send(self, *, to_email: str, subject: str, body: str) -> None:
        logger.info("email_stub_send", to=to_email, subject=subject)