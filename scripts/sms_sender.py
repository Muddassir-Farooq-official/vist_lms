"""SMS sender: run it next to the app (like the import worker).

    python scripts/sms_sender.py          # loop: send queued SMS every 30 s; overdue-fee reminders once a day at 10:00
    python scripts/sms_sender.py --once   # one pass (for cron)

Without SMS_PROVIDER_URL in .env messages are only recorded (LOGGED), never sent.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.clock import institute_now  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.sms.service import SmsService  # noqa: E402

REMINDER_HOUR = 10


async def one_pass(with_reminders: bool) -> dict:
    async with AsyncSessionLocal() as session:
        from app.policy.service import apply_from_db

        await apply_from_db(session)                 # use the administrator's current SMS switches
        svc = SmsService(session)
        queued = await svc.queue_fee_overdue_reminders() if with_reminders else 0
        result = await svc.send_pending(limit=200)
        await session.commit()
        return {"reminders_queued": queued, **result}


async def main() -> None:
    if "--once" in sys.argv:
        print(await one_pass(with_reminders=institute_now().hour == REMINDER_HOUR))
        return
    last_reminder_day = None
    while True:
        now = institute_now()
        due = now.hour >= REMINDER_HOUR and last_reminder_day != now.date()
        try:
            r = await one_pass(with_reminders=due)
            if due:
                last_reminder_day = now.date()
            if r["processed"] or r["reminders_queued"]:
                print(now.isoformat(timespec="seconds"), r, flush=True)
        except Exception as exc:  # keep running; the next pass retries
            print(now.isoformat(timespec="seconds"), "error:", exc, flush=True)
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())