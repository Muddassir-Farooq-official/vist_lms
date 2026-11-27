"""Fill a DEMO site (or a local sandbox) with sample data so every part can be clicked through: a current term, two
programs with first-semester courses, rooms and periods, teachers with a weekly timetable, an accounts officer, a
librarian and twenty students enrolled in their courses. Everything is made through the same API the screens use.

    python scripts/seed_demo_data.py

Runs only where APP_ENV is not production, or on a demo site (DEMO_SITE=true, set by `sites.sh add-demo`).
It never changes the administrator's password: it works through a temporary account that is switched off at the end.
Sample logins (password Demo@12345 for all: three teachers, an accounts officer, a librarian and the students) are printed
at the end.
NEVER run against a real institute.
"""
from __future__ import annotations

import asyncio
import secrets
import sys
from datetime import date, timedelta
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
BOT = "demo_seed"
A = "/api/v1/academics"

PROGRAMS = [("BSCS", "BS Computer Science"), ("BBA", "Bachelor of Business Administration")]
COURSES = {   # code: (title, credit hours, programs)
    "ENG101": ("Functional English", 3, ("BSCS", "BBA")),
    "MTH101": ("Calculus I", 3, ("BSCS",)),
    "CS101": ("Introduction to Computing", 3, ("BSCS",)),
    "CS102": ("Programming Fundamentals", 4, ("BSCS",)),
    "BUS101": ("Principles of Management", 3, ("BBA",)),
    "ACC101": ("Financial Accounting", 3, ("BBA",)),
    "ECO101": ("Microeconomics", 3, ("BBA",)),
}
TEACHERS = [   # username suffix, first, last, courses
    ("teacher1", "Ayesha", "Khan", ("CS101", "CS102")),
    ("teacher2", "Bilal", "Ahmed", ("MTH101", "ECO101", "ACC101")),
    ("teacher3", "Sana", "Malik", ("ENG101", "BUS101")),
]
OFFICE = [("accounts1", "Usman", "Tariq", "ACCT", "Accounts Officer", Roles.ACCOUNTS_OFFICER),
          ("librarian1", "Hina", "Raza", "LIBR", "Librarian", Roles.LIBRARIAN)]
FIRST = ["Ali", "Fatima", "Hamza", "Zainab", "Omar", "Maryam", "Hassan", "Aiman", "Saad", "Noor",
         "Taha", "Iqra", "Usama", "Hira", "Danish", "Amna", "Fahad", "Laiba", "Zain", "Mahnoor"]
LAST = ["Shah", "Butt", "Chaudhry", "Qureshi", "Mirza", "Sheikh", "Javed", "Aslam", "Nawaz", "Iqbal"]
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]


def _term(today: date) -> tuple[str, date, date]:
    if today.month >= 8:
        return f"Fall {today.year}", date(today.year, 9, 1), date(today.year + 1, 1, 20)
    if today.month == 1:
        return f"Fall {today.year - 1}", date(today.year - 1, 9, 1), date(today.year, 1, 20)
    return f"Spring {today.year}", date(today.year, 2, 1), date(today.year, 6, 30)


def _ok(r, what: str) -> bool:
    if r.status_code in (200, 201):
        return True
    print(f"  !! {what}: {r.status_code} {r.text[:160]}")
    return False


async def _bot_account() -> str:
    """A temporary administrator for this script only, so the real administrator's password is never touched."""
    pw = secrets.token_urlsafe(18) + "Aa1!"
    async with AsyncSessionLocal() as s:
        role = (await s.execute(select(Role).where(Role.code == Roles.ADMIN))).scalar_one()
        bot = (await s.execute(select(User).where(User.username == BOT))).scalar_one_or_none()
        if bot is None:
            bot = User(username=BOT, password_hash=hash_password(pw), must_change_password=False, is_active=True)
            s.add(bot)
            await s.flush()
            s.add(UserRole(user_id=bot.id, role_id=role.id))
        else:
            bot.password_hash, bot.is_active, bot.must_change_password = hash_password(pw), True, False
        for code, title, role_code in [("LECT", "Lecturer", Roles.TEACHER)] + [(o[3], o[4], o[5]) for o in OFFICE]:
            if (await s.execute(select(JobRole).where(JobRole.code == code))).scalar_one_or_none() is None:
                s.add(JobRole(code=code, title=title, default_role_code=role_code))
        await s.commit()
    return pw


async def _demo_passwords(usernames: list[str]) -> None:
    """Sample logins share one known password and skip the first-login change, so visitors can try each portal."""
    async with AsyncSessionLocal() as s:
        for u in (await s.execute(select(User).where(User.username.in_(usernames)))).scalars():
            u.password_hash, u.must_change_password, u.temp_password = hash_password(PW), False, None
        await s.commit()


async def _switch_off_bot() -> None:
    async with AsyncSessionLocal() as s:
        bot = (await s.execute(select(User).where(User.username == BOT))).scalar_one_or_none()
        if bot is not None:
            bot.is_active = False
            bot.password_hash = hash_password(secrets.token_urlsafe(32))
            await s.commit()


async def main() -> None:
    if settings.app_env == "production" and not settings.demo_site:
        raise SystemExit("Refusing to add sample data to a real institute (APP_ENV=production and DEMO_SITE is not true).")
    async with AsyncSessionLocal() as s:
        if (await s.execute(select(Program).where(Program.code == "BSCS"))).scalar_one_or_none():
            raise SystemExit("Sample data is already here.")
    # Sign-in codes (two-factor) are for people; this script's own temporary account signs in inside this process only.
    settings.two_factor_required_for_admins = 0
    bot_pw = await _bot_account()
    today = date.today()
    label, start, end = _term(today)
    logins: list[tuple[str, str]] = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://demo") as c:
            r = await c.post("/api/v1/auth/login", json={"username": BOT, "password": bot_pw})
            assert r.status_code == 200 and (await c.get("/api/v1/auth/me")).status_code == 200, r.text
            async with AsyncSessionLocal() as s:
                jobs = {j.code: str(j.id) for j in (await s.execute(select(JobRole))).scalars()}

            print("Term", label)
            _ok(await c.post(f"{A}/academic-terms", json={"label": label, "start_date": str(start), "end_date": str(end)}), "term")

            print("Programs and courses")
            pid = {}
            for code, name in PROGRAMS:
                r = await c.post("/api/v1/programs", json={"code": code, "name": name, "duration_years": 4, "total_semesters": 8,
                                                          "max_credit_hours_per_semester": 21})
                if _ok(r, f"program {code}"):
                    pid[code] = r.json()["id"]
            cid = {}
            for code, (title, ch, progs) in COURSES.items():
                r = await c.post(f"{A}/courses", json={"code": code, "title": title, "credit_hours": ch})
                if not _ok(r, f"course {code}"):
                    continue
                cid[code] = r.json()["id"]
                for p in progs:
                    if p in pid:
                        _ok(await c.post(f"{A}/courses/{cid[code]}/programs", json={"program_id": pid[p], "semester_number": 1}), f"link {code}")

            print("Rooms and periods")
            rooms = ["Room 101", "Room 102", "Lab 1"]
            for name in rooms:
                _ok(await c.post(f"{A}/rooms", json={"name": name, "kind": "LAB" if name.startswith("Lab") else "CLASS", "seats": 40}), f"room {name}")
            periods = []
            existing = (await c.get(f"{A}/periods")).json()
            for i, (a, b) in enumerate([("08:30:00", "09:30:00"), ("09:30:00", "10:30:00"), ("11:00:00", "12:00:00"), ("12:00:00", "13:00:00")], 1):
                found = next((p for p in existing if p["start_time"] == a and p["end_time"] == b), None)
                if found is None:
                    r = await c.post(f"{A}/periods", json={"period_number": len(existing) + i, "start_time": a, "end_time": b})
                    found = r.json() if _ok(r, f"period {a}") else None
                if found:
                    periods.append(found["id"])

            print("Staff")
            emp = {}
            for n, (user, first, last, _courses) in enumerate(TEACHERS):
                r = await c.post("/api/v1/employees", json={"first_name": first, "last_name": last, "date_of_birth": "1986-03-1" + str(n),
                                 "gender": "Female" if first in ("Ayesha", "Sana") else "Male", "cnic": f"35202-10000{n:02d}-1",
                                 "contact_no": f"030011100{n:02d}", "hire_date": str(start - timedelta(days=90)), "department": "Faculty",
                                 "job_role_id": jobs["LECT"], "base_salary": 90000, "password": PW + "x9"})
                if _ok(r, f"teacher {first}"):
                    emp[user] = r.json()
            for n, (user, first, last, job, _t, _r) in enumerate(OFFICE):
                r = await c.post("/api/v1/employees", json={"first_name": first, "last_name": last, "date_of_birth": "1990-05-0" + str(n + 1),
                                 "gender": "Male" if first == "Usman" else "Female", "cnic": f"35202-20000{n:02d}-2",
                                 "contact_no": f"030022200{n:02d}", "hire_date": str(start - timedelta(days=90)), "department": "Administration",
                                 "job_role_id": jobs[job], "base_salary": 60000, "password": PW + "x9"})
                if _ok(r, f"staff {first}"):
                    emp[user] = r.json()

            print("Classes and timetable")
            slot = 0
            for user, _f, _l, courses in TEACHERS:
                if user not in emp:
                    continue
                for code in courses:
                    if code not in cid:
                        continue
                    r = await c.post(f"{A}/offerings", json={"course_id": cid[code], "term_label": label, "section_label": "A",
                                                             "term_start_date": str(start), "term_end_date": str(end)})
                    if not _ok(r, f"class {code}"):
                        continue
                    off = r.json()["id"]
                    _ok(await c.patch(f"{A}/offerings/{off}", json={"assigned_teacher_id": emp[user]["id"], "is_open_for_enrollment": True}), f"teacher for {code}")
                    for k in range(2):        # two classes a week; all one batch, so no two classes share a time
                        n = slot * 2 + k
                        day, period = DAYS[n % 5], (periods[n // 5] if n // 5 < len(periods) else None)
                        if period:
                            _ok(await c.post(f"{A}/offerings/{off}/timetable-slots", json={"day_of_week": day, "period_id": period,
                                             "room": rooms[slot % len(rooms)]}), f"timetable {code}")
                    slot += 1

            print("Students")
            students = []
            for i in range(20):
                prog = "BSCS" if i < 12 else "BBA"
                if prog not in pid:
                    continue
                r = await c.post("/api/v1/students", json={
                    "first_name": FIRST[i], "last_name": LAST[i % len(LAST)], "date_of_birth": f"2006-0{1 + i % 9}-1{i % 9}",
                    "gender": "Female" if i % 2 else "Male", "cnic": f"35202-30000{i:02d}-{i % 10}",
                    "contact_no": f"0301333{i:04d}", "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
                    "guardian_name": f"{FIRST[(i + 5) % 20]} {LAST[i % len(LAST)]}", "guardian_cnic": f"35202-40000{i:02d}-{i % 10}",
                    "relation_with_guardian": "Father", "emergency_contact_no": f"0301444{i:04d}", "present_address": "House 1, Sample Street",
                    "province": "Punjab", "district": "Lahore", "city": "Lahore", "program_id": pid[prog], "current_semester": 1,
                    "admission_session": label, "email": f"student{i + 1}@demo.example"})
                if _ok(r, f"student {FIRST[i]}"):
                    students.append((r.json()["username"], prog))
        await _demo_passwords([e["username"] for e in emp.values()] + [u for u, _ in students])

        print("Enrolling the students in their courses")
        offerings = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://demo") as c:
            await c.post("/api/v1/auth/login", json={"username": BOT, "password": bot_pw})
            r = await c.get(f"{A}/offerings", params={"term_label": label, "page_size": 100})
            if r.status_code == 200:
                body = r.json()
                offerings = body["items"] if isinstance(body, dict) else body
        by_course = {o.get("course_code") or (o.get("course") or {}).get("code"): o["id"] for o in offerings}
        for username, prog in students:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://demo") as c:
                if (await c.post("/api/v1/auth/login", json={"username": username, "password": PW})).status_code != 200:
                    print("  !! could not sign in as", username)
                    continue
                for code, (_t, _ch, progs) in COURSES.items():
                    if prog in progs and by_course.get(code):
                        _ok(await c.post(f"{A}/enrollments/me", json={"course_offering_id": by_course[code]}), f"{username} -> {code}")
        who = {t[0]: f"teacher ({t[1]} {t[2]})" for t in TEACHERS} | {o[0]: f"{o[4].lower()} ({o[1]} {o[2]})" for o in OFFICE}
        logins = [(e["username"], who[u]) for u, e in emp.items()] + [(u, "student, " + p) for u, p in students]
    finally:
        await _switch_off_bot()

    print("\nSample data ready. Sample logins (password " + PW + " for all; the administrator keeps their own password):")
    for username, who in logins:
        print(f"    {username:<22} {who}")


if __name__ == "__main__":
    asyncio.run(main())
