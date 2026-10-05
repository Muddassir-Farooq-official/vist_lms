"""Check that this installation is complete, and say exactly what to do if it is not.

Run it from the project folder (it needs nothing but the project's own settings):

    python scripts/check_installation.py

It answers three questions:
  1. Are ALL the database update files (migrations) present in migrations/versions?
  2. Is the database up to date with them?
  3. Does the database have every table the software needs?
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Every database update this version of the software expects, oldest first.
EXPECTED_MIGRATIONS = [
    ("95aa84ae9878", "initial schema: auth, students, programs, imports, audit"),
    ("221008dd59b1", "add trigram index, case-insensitive program uniqueness, cnic check con"),
    ("3c0a16bd1234", "fix timezone-naive datetime columns on import_jobs and student_status_"),
    ("d16161b28764", "employee module: job_roles, employees, salary history, bonuses, payrol"),
    ("50a6caaf23ad", "library module: books, copies, issues"),
    ("9e50ff6d33c2", "book editions: physical/digital/both, cover image, digital file; emplo"),
    ("7667af85492b", "library subscriptions: plans, enrollments, dues ledger"),
    ("65bb0065e185", "remove library subscription module, replaced by general Fee module"),
    ("ebb6a9fe36a2", "fee module: types, structures, program scoping, assignments, ledger"),
    ("0858d901dd42", "revoke stray fee.manage grant from accounts_officer role"),
    ("5a31a34360a8", "student semester tracking; fee structure semester scoping"),
    ("132e1c4cbce1", "payment challans for 1LINK 1Bill automation; revoke manual payment ent"),
    ("a1c4f9e02b7d", "journals (is_journal + periodical metadata on books) and book borrow r"),
    ("c7f3a1d95e6b", "system-generated roll numbers / employee IDs as login usernames"),
    ("d4e8b6f13a2c", "book short_code for human-readable per-book accession codes"),
    ("e5a9c1f04b3d", "library books are physical-only — drop digital edition/upload columns"),
    ("f8b3d7e29a1c", "fines module — scalable, cross-role fine tracking for students/employe"),
    ("a3c6e9f21b4d", "renewal_count on book_issues + WAITLISTED borrow-request status"),
    ("b7d2f4a83e9c", "complaints module — scalable feedback & complaint tracking"),
    ("c9e1a5d47f2b", "revoke fine.issue from librarian, accounts_officer, teacher — admin on"),
    ("d1f5b8e6c3a4", "student/employee profile fields (photo, certificate, qualifications, a"),
    ("e2b7c4f91a6d", "retroactively rename existing student roll numbers to the new format"),
    ("f3a8d6c25b7e", "notifications module — in-app notifications (the real, working version"),
    ("a1c9e7f43d2b", "academics module — courses, offerings, enrollment, lectures + program "),
    ("b4f2a8d17c9e", "revoke lecture.manage from teacher — syllabus/lecture uploads are admi"),
    ("c7e3f9a52d6b", "subject/course-level fees — charged per enrollment (including retakes)"),
    ("d9a1c6e48b3f", "backfill User.is_active for already-suspended/dropped/graduated studen"),
    ("e5b7d3a91c6f", "collectible late fees — track how much of each installment's late fee "),
    ("a7d4f9c62e8b", "fine request/approval workflow — librarian and accounts officer reques"),
    ("b8e5c3a71f4d", "add PAYROLL notification category"),
    ("c9f6a2e83b5d", "attendance and grading module"),
    ("d1a7e4c95f2b", "lecture room and scheduled start/end time"),
    ("e6b3a9f27d5c", "timetable and class sessions replace lectures"),
    ("f7c4d1a836e9", "periods and timetable slot auto-assign support"),
    ("a2e9c5f18b6d", "class session resources support links; wipe timetable/attendance/gradi"),
    ("c3f8b6d29a1e", "topic completion and late attendance marking note"),
    ("d4a7f2c93b8e", "result module: institute-wide assessment type catalog"),
    ("e8c3b5a1f492", "remove payroll module"),
    ("f9d2a6c41e7b", "student email, blood group, admission session/category"),
    ("a4e7c2b95d18", "employee email, blood group, marital status, employment type, emergenc"),
    ("b5f1d3a7c962", "course sections (section_label + capacity) and student section"),
    ("c6a2e8d41f75", "attendance: LATE/LEAVE statuses, class_sessions.attendance_taken_at, c"),
    ("d7b3f9a52c18", "attendance is Present or Absent only: drop the Late/Leave leftovers"),
    ("e9d5b2c8a461", "admin content locks on course offerings and class sessions"),
    ("a3c7e1f9d052", "complaints can be filed anonymously"),
    ("c5f1a9d7e204", "announcements (+ attachments, authorities, read tracking); complaints "),
    ("d6a2f8c1e305", "assignments: teacher sets work, students submit a Google Drive link, t"),
    ("e7b3a9d2f416", "applications: students and employees apply to the administration (leav"),
    ("f8c4b0e3a527", "tasks: staff assign work to each other with a deadline; accept / rejec"),
    ("a9d5c1f4b638", "tasks: the assigner confirms completion; every rejection goes to the a"),
    ("b1e6d2a9c473", "gate: a Security Guard role that marks students and staff in and out; "),
    ("c2f7e5a1d948", "results: marks are submitted, approved and published before students s"),
    ("d3a8f6b2c159", "results: the grading scale (letter grades and grade points) and each s"),
    ("e4b9a7c3d261", "fees: scholarships and concessions (a new ledger entry type that reduc"),
    ("b7d3f9a1c845", "accounts: expense and income categories, vendors, expenses (with recei"),
    ("c8e4a2d6f917", "purchasing: purchase requests that are approved, ordered, received and"),
    ("d9f5b3e7a128", "payroll: pay items, salary advances, monthly payroll runs and payslips"),
    ("e1a6c4b8d739", "exam department: an Exam Controller role that approves, publishes and "),
]


def main() -> int:
    problems: list[str] = []
    versions = ROOT / "migrations" / "versions"
    import re

    present: set[str] = set()
    for f in (versions.glob("*.py") if versions.exists() else []):
        # the migration's own id, however it happens to be written:  revision = "abc"   revision: str = 'abc'
        found = re.search(r"""^revision\s*(?::[^=\n]+)?=\s*['"]([0-9a-f]+)['"]""", f.read_text(errors="ignore"), re.M)
        if found:
            present.add(found.group(1))
    missing_files = [(rev, name) for rev, name in EXPECTED_MIGRATIONS if rev not in present]
    print("1. Database update files in migrations/versions")
    if missing_files:
        print(f"   MISSING {len(missing_files)} of {len(EXPECTED_MIGRATIONS)}:")
        for rev, name in missing_files:
            print(f"     - {rev}  {name}")
        problems.append("Copy the missing migration files (listed above) into migrations/versions/. In the downloads they have a name starting with 'migrations__': remove that part, e.g. migrations__d9f5b3e7a128_payroll.py -> d9f5b3e7a128_payroll.py")
    else:
        print(f"   OK: all {len(EXPECTED_MIGRATIONS)} are there")

    manifest = ROOT / "scripts" / "expected_files.txt"
    print("\n1b. The software's own files")
    if manifest.exists():
        wanted = [ln.strip() for ln in manifest.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
        absent_files = [w for w in wanted if not (ROOT / w).exists()]
        if absent_files:
            print(f"   MISSING {len(absent_files)} of {len(wanted)}:")
            for w in absent_files[:60]:
                print(f"     - {w}")
            if len(absent_files) > 60:
                print(f"     ... and {len(absent_files) - 60} more")
            problems.append("Copy the missing files (listed under 1b) from the latest download, to the same paths. The catch-up zip keeps every file at its real path: unzip it over the project folder.")
        else:
            print(f"   OK: all {len(wanted)} are there")
    else:
        print("   (scripts/expected_files.txt is not present, so this check was skipped)")

    try:
        from sqlalchemy import create_engine, text

        from app.core.config import settings
        from app.core.database import Base
        import app.main  # noqa: F401  (registers every table the software uses)
    except Exception as exc:  # noqa: BLE001
        print(f"\n2. The application itself could not be loaded ({type(exc).__name__}: {str(exc)[:140]})")
        print("   Some of the software's own files are probably missing or out of date: copy over every file from the latest downloads.")
        return 1
    try:
        engine = create_engine(settings.database_url_sync)
        with engine.connect() as conn:
            try:
                current = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
            except Exception:
                current = []
            existing = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))}
    except Exception as exc:  # noqa: BLE001
        print(f"\n2. Database: COULD NOT CONNECT ({type(exc).__name__}: {str(exc)[:120]})")
        print("   Check DATABASE_URL_SYNC in your .env file and that PostgreSQL is running.")
        return 1

    newest = EXPECTED_MIGRATIONS[-1][0]
    print("\n2. Database version")
    print(f"   The database is at: {', '.join(current) or '(nothing: it was never set up)'}   |   newest expected: {newest}")
    if newest not in current:
        problems.append("Run:  alembic upgrade head      (this brings the database up to date)")

    needed = {t.name for t in Base.metadata.sorted_tables}
    absent = sorted(needed - existing)
    print("\n3. Tables the software needs")
    if absent:
        print(f"   MISSING {len(absent)}: {', '.join(absent[:20])}{' ...' if len(absent) > 20 else ''}")
        problems.append("After the migration files are all in place, run:  alembic upgrade head      (this creates the missing tables)")
        if newest in current:
            problems.append("The database says it is up to date, yet tables are missing: some migration files were not in the folder when you last ran the upgrade. Put them in place, then run:  alembic downgrade -1  and  alembic upgrade head   (or ask for help: do not edit alembic_version by hand).")
    else:
        print(f"   OK: all {len(needed)} tables exist")

    print()
    if problems:
        print("WHAT TO DO:")
        for n, p in enumerate(dict.fromkeys(problems), 1):
            print(f"  {n}. {p}")
        print("  Then restart the application.")
        return 1
    print("Everything is in place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())