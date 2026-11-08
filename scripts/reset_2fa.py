"""Turn two-step sign-in off for one account, when nobody else can (e.g. the only administrator lost their phone).

Run ON THE SERVER:
    docker compose -f docker-compose.prod.yml exec app python scripts/reset_2fa.py <username>

The person sets it up again at their next sign-in. Remembered devices and open sessions of that account end.
The reset is kept in the activity log ("reset on the server"). Prints no secret.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(username: str) -> int:
    from sqlalchemy import func, select

    from app.auth import two_factor as tf
    from app.auth.models import User
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(func.lower(User.username) == username.lower()))).scalar_one_or_none()
        if user is None:
            print(f"No account called '{username}'. Nothing was changed.")
            return 1
        if user.totp_secret_enc is None and user.totp_pending_enc is None:
            print(f"'{user.username}' has no two-step sign-in set up. Nothing to reset.")
            return 0
        await tf.reset(session, user, actor_id=None, how="reset on the server (scripts/reset_2fa.py)")
        await session.commit()
    print(f"Two-step sign-in for '{user.username}' is reset. They set it up again at their next sign-in.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Use: python scripts/reset_2fa.py <username>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1].strip())))
