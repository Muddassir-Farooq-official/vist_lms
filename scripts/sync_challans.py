"""One-off after deploying the finance fixes: bring every existing unpaid challan in step with its installment.

Before the fix, a payment at the counter, a waiver or a cancellation left the fee's challans payable (and at their
old amount). From now on every ledger change keeps them in step; this script does the same once for the challans
that already exist. It never touches payments, charges or paid challans -- only the status (PENDING / CANCELLED)
and the amount of challans that are not paid.

    python scripts/sync_challans.py            # report only: what would change (nothing is saved)
    python scripts/sync_challans.py --apply    # make the changes, in one transaction

Run the report first, on a restored copy if you can, and keep its output.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.fees.assignment_service import sync_challans  # noqa: E402
from app.fees.models import FeeAssignment  # noqa: E402
from app.fees.payment_models import ChallanStatus, PaymentChallan  # noqa: E402


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        before = {c.id: (c.status, float(c.amount), c.consumer_number) for c in (await session.execute(
            select(PaymentChallan).where(PaymentChallan.status != ChallanStatus.PAID))).scalars()}
        assignments = (await session.execute(select(FeeAssignment))).scalars().all()
        for a in assignments:
            await sync_challans(session, a)
        await session.flush()
        changed = []
        for c in (await session.execute(select(PaymentChallan).where(PaymentChallan.id.in_(list(before))))).scalars():
            old = before[c.id]
            if (c.status, float(c.amount)) != (old[0], old[1]):
                changed.append((old[2], old[0].value, old[1], c.status.value, float(c.amount)))
        for number, s0, a0, s1, a1 in sorted(changed):
            print(f"challan {number}: {s0} Rs {a0:,.2f} -> {s1} Rs {a1:,.2f}")
        print(f"\n{len(assignments)} fees checked, {len(changed)} challans {'updated' if apply else 'would change'}.")
        if apply:
            await session.commit()
            print("Saved.")
        else:
            await session.rollback()
            print("Nothing was saved (run with --apply to save).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="save the changes (default: report only)")
    asyncio.run(main(parser.parse_args().apply))
