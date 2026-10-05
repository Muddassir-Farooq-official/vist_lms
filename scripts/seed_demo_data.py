"""Populate the SANDBOX with realistic demo data so every module can be
clicked through by hand. Idempotent-ish: refuses to run twice.

    python scripts/seed_demo_data.py
Logins created (password for all: Demo@12345):
    admin / teacher1 / teacher2 / accounts1 / librarian1 / student-* (roll numbers printed)
NEVER run against production.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.employees.models import JobRole
from app.main import app
from app.programs.models import Program

PW = "Demo@12345"


async def main() -> None:
    if settings.app_env == "production":
        raise SystemExit("Refusing to seed demo data in production.")
    async with AsyncSessionLocal() as s:
        if (await s.execute(select(Program).where(Program.code == "BSCS"))).scalar_one_or_none():
            raise SystemExit("Demo data already present -- run `make sandbox-reset` first.")
        admin = (await s.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
        if admin is None:
            role = (await s.execute(select(Role).where(Role.code == Roles.ADMIN))).scalar_one()
            admin = User(username="admin", password_hash=hash_password(PW), must_change_password=False)
            s.add(admin)
            await s.flush()
            s.add(UserRole(user_id=admin.id, role_id=role.id))
        else:
            admin.password_hash = hash_password(PW)
            admin.must_change_password = False
        for code, title, role_code in [
            ("LECT", "Lecturer", Roles.TEACHER),
            ("ACCT", "Accounts Officer", Roles.ACCOUNTS_OFFICER),
            ("LIBR", "Librarian", Roles.LIBRARIAN),
        ]:
            if (await s.execute(select(JobRole).where(JobRole.code == code))).scalar_one_or_none() is None:
                s.add(JobRole(code=code, title=title, default_role_code=role_code))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://sandbox") as c:
        r = await c.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
        assert r.status_code == 200, r.text
        for code, name, sems in [("BSCS", "BS Computer Science", 8), ("BBA", "Bachelor of Business Admin", 8)]:
            pr = await c.post("/api/v1/programs", json={"code": code, "name": name, "duration_years": 4,
                                                          "total_semesters": sems, "max_credit_hours_per_semester": 21})
            print("program", code, pr.status_code)
        programs = (await c.get("/api/v1/programs")).json()
        items = programs["items"] if isinstance(programs, dict) else programs
        pid = {p["code"]: p["id"] for p in items}
        for i in range(1, 11):
            body = {
                "first_name": f"Student{i}", "last_name": "Demo", "date_of_birth": "2005-01-15",
                "gender": "Male" if i % 2 else "Female", "cnic": f"35202-00000{i:02d}-{i % 10}",
                "contact_no": f"0300111{i:04d}", "marital_status": "Single", "religion": "Islam",
                "nationality": "Pakistani", "guardian_name": "Guardian Demo",
                "guardian_cnic": f"35202-99999{i:02d}-{i % 10}", "relation_with_guardian": "Father",
                "emergency_contact_no": f"0300222{i:04d}", "present_address": "Demo Street 1",
                "province": "Punjab", "district": "Lahore", "city": "Lahore",
                "program_id": pid["BSCS"] if i <= 6 else pid["BBA"], "current_semester": 1,
                "admission_session": "Fall 2026", "email": f"student{i}@demo.test",
            }
            sr = await c.post("/api/v1/students", json=body)
            print("student", sr.status_code, sr.json().get("username") if sr.status_code == 201 else sr.text[:120])
    print("\nDemo data ready. Admin login: admin /", PW)


if __name__ == "__main__":
    asyncio.run(main())