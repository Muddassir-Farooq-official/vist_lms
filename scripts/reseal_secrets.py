"""Re-encrypt the secrets stored in the database (the SMTP password) for a new JWT_SECRET.

Run by scripts/server/harden.sh rotate-jwt, BEFORE the new secret is put in .env, with the OLD secret still in .env and
the new one in the environment variable NEW_JWT_SECRET (never on the command line):

    NEW_JWT_SECRET=... docker compose -f docker-compose.prod.yml run --rm --no-deps -e NEW_JWT_SECRET app python scripts/reseal_secrets.py
    (add --back to undo: the secrets are re-encrypted from NEW_JWT_SECRET back to the secret in .env)

Prints only counts, never a secret. Safe to run twice: a value that cannot be read with the "from" key is left alone.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.core import secretbox  # noqa: E402
from app.core.config import settings  # noqa: E402


async def reseal(old: str, new: str, session=None) -> dict:
    """`session`: an open database session to use (tests); by default the script opens its own."""
    if session is None:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as own:
            return await reseal(old, new, own)
    from app.notifications.email_models import EmailSettings

    done = skipped = 0
    running = settings.jwt_secret                                 # put back afterwards, whichever way this runs
    try:
        rows = (await session.execute(select(EmailSettings).where(EmailSettings.password_enc.is_not(None)))).scalars().all()
        for row in rows:
            settings.jwt_secret = old
            plain = secretbox.open_(row.password_enc)
            if plain is None:
                skipped += 1                                  # already under the new key, or unreadable: leave it
                continue
            settings.jwt_secret = new
            row.password_enc = secretbox.seal(plain)
            done += 1
        settings.jwt_secret = running
        await session.commit()
    finally:
        settings.jwt_secret = running
    return {"re_encrypted": done, "left_alone": skipped}


def main() -> int:
    new = (os.environ.get("NEW_JWT_SECRET") or "").strip()
    current = settings.jwt_secret
    if len(new) < 32:
        print("NEW_JWT_SECRET is missing or shorter than 32 characters; nothing was changed.")
        return 2
    old, target = (new, current) if "--back" in sys.argv else (current, new)
    result = asyncio.run(reseal(old, target))
    print(f"Stored secrets re-encrypted: {result['re_encrypted']}; left alone: {result['left_alone']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
