"""Emergency switch: stop requiring a verified email (for example the mail server broke and nobody can get a code).

    python scripts/email_verification_off.py            # turn the requirement off now (recorded in the activity log)

Normally an administrator does this on Settings -> Email. Turning email off there also pauses the requirement
by itself. Nothing else changes: verified addresses stay verified.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit.service import AuditService  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.notifications.email_models import EmailSettings  # noqa: E402


async def main() -> int:
    async with AsyncSessionLocal() as session:
        row = await session.get(EmailSettings, 1)
        if row is None or not row.require_verified_email:
            print("Email verification is not required right now. Nothing to do.")
            return 0
        row.require_verified_email = False
        await AuditService(session).log(actor_user_id=None, action="settings.email_verification_off_script", entity_type="email_settings",
                                        entity_id=None, before={"require_verified_email": True}, after={"require_verified_email": False})
        await session.commit()
    print("Done: people no longer need to verify their email before using the portal (within about 10 seconds).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))