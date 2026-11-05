"""SMS and email sender: run it next to the app (like the import worker). docker-compose service "sms".

    python scripts/sms_sender.py          # loop: send queued SMS and emails every 30 s; overdue-fee reminders once a day at 10:00
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
from app.core.model_registry import load_all_models  # noqa: E402

load_all_models()     # this process saves notifications / emails too: every table must be known (NoReferencedTableError)

REMINDER_HOUR = 10


async def one_pass(with_reminders: bool) -> dict:
    async with AsyncSessionLocal() as session:
        from app.policy.service import apply_from_db

        await apply_from_db(session)                 # use the administrator's current SMS switches
        svc = SmsService(session)
        queued = await svc.queue_fee_overdue_reminders() if with_reminders else 0
        from app.todos.service import remind_missing_lists

        try:
            async with session.begin_nested():
                await remind_missing_lists(session)   # staff with no to-do list today (Settings -> Rules -> Tasks); once a day
        except Exception as exc:  # noqa: BLE001
            print("to-do reminder failed:", exc, flush=True)
        from app.core.background import missed_class_round

        await missed_class_round(session)            # a teacher on campus who did not take a class -> the administration (once per class)
        result = await svc.send_pending(limit=200)
        from app.core.background import beat
        from app.notifications.email_service import send_pending as send_emails

        emails = await send_emails(session, limit=200)
        from app.core.background import push_round

        emails.update(await push_round(session))         # new notifications to phones / browsers (when push is set up)
        await beat(session, "sender", f"{result.get('processed', 0)} SMS, {emails.get('emails', 0)} emails")
        await session.commit()
        return {"reminders_queued": queued, **result, **emails}


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
            if r["processed"] or r["reminders_queued"] or r.get("emails"):
                print(now.isoformat(timespec="seconds"), r, flush=True)
        except Exception as exc:  # keep running; the next pass retries
            print(now.isoformat(timespec="seconds"), "error:", exc, flush=True)
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())