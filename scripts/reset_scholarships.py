"""One-off clean start for scholarships and discounts, so they can be given again (for example "Tuition Fee only").

What it does, for EVERY scholarship / discount (running or already ended):
  * takes its discount off every installment that is NOT yet paid in full (the full amount is owed again; challans update);
  * installments already PAID IN FULL keep the discount they were paid at (nobody owes extra on money already accepted);
  * marks running ones as ended (they stay on record; nothing is deleted);
  * writes each removal as a new ledger row linked to the discount it undoes, plus an activity-log entry.
It does not touch payments, charges, fines, write-offs or cancelled previous dues, and sends no notifications.

    python scripts/reset_scholarships.py                                  # report only: nothing is saved
    python scripts/reset_scholarships.py --csv reset_report.csv           # same, and save the report
    python scripts/reset_scholarships.py --apply --by admin --csv reset_applied.csv   # do it, in one transaction

Take a database backup first. Run the report, check it, then --apply. Afterwards give the new discounts
(Fees -> Scholarships & discounts -> Applies to: "Tuition Fee only (every semester)").
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.approvals.models import ApprovalRequest  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.fees.scholarship_service import ScholarshipService  # noqa: E402


async def main(apply: bool, by: str | None, out: str | None) -> int:
    async with AsyncSessionLocal() as session:
        actor = (await session.execute(select(User).where(User.username == (by or "")))).scalar_one_or_none() if by else None
        if apply and actor is None:
            print("Give --by <your username> (the administrator recorded in the activity log)."); return 2
        if actor is None:          # report only: any administrator id will do, the transaction is rolled back
            actor = (await session.execute(select(User).order_by(User.created_at).limit(1))).scalar_one()
        rows = await ScholarshipService(session).reset_all(actor_id=actor.id, note="Discounts reset for a clean start (scripts/reset_scholarships.py)")
        for r in rows:
            print(f"{r['roll_number']:<10} {r['student'][:28]:<28} {r['scholarship'][:26]:<26} {r['amount']:>12}  {r['status_before']:<6}"
                  f"  removed Rs {r['removed_from_unpaid']:>12,.2f}   kept on paid Rs {r['kept_on_paid_installments']:>12,.2f}")
        removed = sum(r["removed_from_unpaid"] for r in rows)
        kept = sum(r["kept_on_paid_installments"] for r in rows)
        running = sum(1 for r in rows if r["status_before"] == "ACTIVE")
        print(f"\n{len(rows)} scholarships ({running} running). Discount removed from unpaid installments: Rs {removed:,.2f}. "
              f"Kept on installments already paid: Rs {kept:,.2f}.")
        waiting = (await session.execute(select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.kind.in_(["SCHOLARSHIP", "SCHOLARSHIP_EDIT"]), ApprovalRequest.status == "REQUESTED"))).scalar_one()
        if waiting:
            print(f"Note: {waiting} scholarship request(s) still wait on the Approvals page. Reject them, or they would be given when approved.")
        if out:
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["roll_number"])
                w.writeheader(); w.writerows(rows)
            print(f"Report saved to {out}.")
        if apply:
            await session.commit()
            print("Saved.")
        else:
            await session.rollback()
            print("Nothing was saved (run with --apply --by <username> to save).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="save the changes (default: report only)")
    parser.add_argument("--by", help="your administrator username (required with --apply)")
    parser.add_argument("--csv", dest="out", help="also write the report to this CSV file")
    a = parser.parse_args()
    sys.exit(asyncio.run(main(a.apply, a.by, a.out)))