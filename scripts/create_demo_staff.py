"""Create a ready-to-use sample login for each kind of staff member, so every portal can be seen and tried.

There is ONE login page for everybody. The account's role decides which portal opens after signing in. Staff accounts are created by the
administrator (Employees > Add employee > choose the job title). This script does exactly that, for one sample person per role.

    python scripts/create_demo_staff.py --admin-user admin --admin-password "YourAdminPassword"

It talks to the running system (default http://localhost:8000), skips anyone who already exists, and prints a table of the logins.
Each person must change the password the first time they sign in. Do NOT use this on a live system you do not want sample accounts in.
"""
from __future__ import annotations

import argparse
import sys

import httpx

# (job title code, display title, system role, sample first name, CNIC, what they see first)
PEOPLE = [
    ("DEMO_TEACHER", "Lecturer", "TEACHER", "Demo Teacher", "35202-9910001-1", "Faculty portal: classes, attendance, grading"),
    ("DEMO_LIBRARIAN", "Librarian", "LIBRARIAN", "Demo Librarian", "35202-9910002-2", "Library portal: books, borrowing, fines"),
    ("DEMO_ACCOUNTS", "Accounts Officer", "ACCOUNTS_OFFICER", "Demo Accounts", "35202-9910003-3", "Accounts portal: fees, accounts, payroll"),
    ("SECURITY_GUARD", "Security Guard", "SECURITY_GUARD", "Demo Guard", "35202-9910004-4", "Gate: search people, mark in and out"),
    ("EXAM_CONTROLLER", "Exam Controller", "EXAM_CONTROLLER", "Demo Exam", "35202-9910005-5", "Exam department: approve and publish results"),
    ("DEMO_HOSTEL", "Hostel Warden", "HOSTEL_WARDEN", "Demo Warden", "35202-9910006-6", "(no portal of its own yet: tasks and applications only)"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--admin-user", required=True)
    ap.add_argument("--admin-password", required=True)
    ap.add_argument("--password", default="Welcome#Demo2026", help="the first password given to every sample account (min 10 characters)")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base_url.rstrip("/") + "/api/v1", timeout=30)
    r = c.post("/auth/login", json={"username": a.admin_user, "password": a.admin_password})
    if r.status_code != 200:
        print(f"Could not sign in as the administrator ({r.status_code}): {r.text[:200]}")
        return 1
    rows = []
    for code, title, system_role, name, cnic, sees in PEOPLE:
        job = next((j for j in c.get("/job-roles").json() if j["code"] == code), None)         # (looked up fresh each time)
        if job is None:
            made = c.post("/job-roles", json={"code": code, "title": title, "default_role_code": system_role})
            if made.status_code != 201:
                rows.append((name, "-", "-", f"could not create the job title: {made.text[:80]}"))
                continue
            job = made.json()
        first, last = name.split(" ", 1)[0], name.split(" ", 1)[1]
        e = c.post("/employees", json={"first_name": first, "last_name": last, "date_of_birth": "1988-01-01", "gender": "Male", "cnic": cnic, "contact_no": "03000000000",
                                       "hire_date": "2026-01-01", "department": "Demo", "job_role_id": job["id"], "base_salary": 50000, "password": a.password})
        if e.status_code == 201:
            rows.append((name, e.json()["username"], a.password, sees))
        else:
            rows.append((name, "(already exists or refused)", "-", e.json().get("error", {}).get("message", e.text)[:80]))
    print(f"\nSign in at {a.base_url}/  (the one login page for everybody)\n")
    print(f"{'Person':<16}{'Username':<28}{'First password':<20}What they see")
    print("-" * 100)
    for name, user, pw, sees in rows:
        print(f"{name:<16}{user:<28}{pw:<20}{sees}")
    print("\nEach person is asked to choose a new password the first time they sign in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())