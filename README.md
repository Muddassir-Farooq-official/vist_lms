# VIST LMS

Campus administration system for Vertex Institute of Science & Technology — a modular
monolith built with FastAPI, PostgreSQL, and Redis. Despite the name, this is not a
course/quiz/content-delivery LMS (no lessons, quizzes, assignments, or grading rubrics) —
it's an institute management system: students, employees/payroll, fees, fines, library
circulation, complaints, and CSV-based enrollment.

## Status

An institute management system, implemented, routed and covered by automated tests. Modules:

- **Students & admissions** — CSV import, merit lists and rounds, roll numbers, guardians, ID cards,
  identity corrections (reason required), semester advance with a double-run guard.
- **Academics** — programs, courses, offerings and sections, enrollment rules, timetable, terms,
  grading scale, marks entry, result sheets (maker/checker), transcripts, supplementary exams,
  datesheets and admit cards, calendar and holidays.
- **Attendance** — marking window, corrections decided by the HOD, locking, leave excuses, fines and drop rules.
- **Fees** — structures, automatic installments and challans, manual payments (closed-books and reused-reference
  checks), reversal (receipt prints VOID), refunds with cancel, scholarships, late-fee cap, undo of a mistaken activation.
- **Accounts & finance reports** — day book, expenses (maker/checker), period close, defaulters aging, daily
  collection, vendor payables, bank-statement matching, exports for the accountant.
- **Payroll, timekeeping, employees** — pay items, advances, security deposits (hold and release), held payslips,
  withholdings report, staff attendance, leave, bank transfer sheet.
- **Records** — numbered, QR-checkable certificates and ID cards (public check page `/verify/<code>`), documents on
  file, clearance, degree audit.
- **Purchasing & inventory** (price approval, unique invoices, returns with refunds), **library**, **hostel**,
  **gate & visitors**, **fines**, **complaints**, **announcements**, **applications**, **tasks**, **SMS** (queue,
  bulk send, fee reminders), **parent portal**, **principal / HOD / exam controller / warden portals**.

**Known gaps:**
- No course-content layer (lessons, quizzes); assignments are links.
- No teacher/course evaluation, academic probation rules, or exam seating plans.
- The payment gateway is simulated until the biller credentials arrive; fees run on challans and manual entry.
- The SMS sender needs a real provider configured before messages leave the building.
- `ruff` and `mypy --strict` run report-only in CI.
- The Docker image build has not been verified here; dependencies are pinned with hashes in `requirements.lock`.

## What's been verified

Run against real PostgreSQL 16 and Redis:

- **85 Alembic migrations** apply cleanly to an empty database, with a single head, and `alembic check` reports
  that the models match the schema.
- **751 automated tests** (`pytest tests/`) — unit, integration and security suites — all pass.

## Quick start (without Docker)

Requires PostgreSQL 16+ (with `pg_trgm` available) and Redis 7+ running locally.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env: set DATABASE_URL, DATABASE_URL_SYNC, REDIS_URL, JWT_SECRET

# create the database and enable the trigram extension
createdb vist_lms
psql -d vist_lms -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'

alembic upgrade head

python scripts/seed_permissions.py --with-admin \
  --admin-username admin --admin-password 'ChangeThisImmediately!'

uvicorn app.main:app --reload
```

Then visit `http://localhost:8000/docs` for interactive API docs, or:

```bash
curl -c cookies.txt -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"ChangeThisImmediately!"}'
```

## Quick start (Docker — build itself untested, see note above)

```bash
cp .env.example .env
docker compose up -d db redis
docker compose run --rm migrate
docker compose up -d app
docker compose exec app python scripts/seed_permissions.py --with-admin \
  --admin-username admin --admin-password 'ChangeThisImmediately!'
```

## Running tests

```bash
createdb vist_lms_test
psql -d vist_lms_test -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'
pytest tests/ -v
```

Tests flush the Redis DB pointed to by `REDIS_URL` before/after each test (rate-limit
counters live there) — **do not point `REDIS_URL` at a database with data you care about
when running the suite.**

**Important:** set `COOKIE_SECURE=false` in your local `.env` before running tests (and for
local `http://` development in general). The test client talks over plain `http://`, and
browsers/httpx correctly refuse to echo back a `Secure`-flagged cookie over a non-TLS
connection — with `COOKIE_SECURE=true` locally, every cookie-based auth test will fail with
401s that look like a bug but are actually the security feature working as intended. Leave
`COOKIE_SECURE=true` (the `.env.example` default) in any real staging/production deployment
that terminates TLS.

## CI

`.github/workflows/ci.yml` runs on every push and PR: `pytest` (blocking — this is the
check that actually catches regressions) against a fresh Postgres 16 + Redis 7, plus `ruff`
and `mypy --strict` (report-only for now — see "Known gaps" above for why, and the
workflow's own comments for how to make them blocking once the pre-existing backlog is
addressed).

## CSV import format

`POST /api/v1/imports/students` expects a UTF-8 CSV with this exact header row:

```
first_name,last_name,date_of_birth,gender,cnic,contact_no,marital_status,religion,
nationality,guardian_name,guardian_cnic,guardian_contact_no,relation_with_guardian,
emergency_contact_no,present_address,province,district,city,program,roll_number,
admission_fee_paid,enrollment_date
```

`guardian_contact_no`, `program`, `roll_number`, `admission_fee_paid`, and
`enrollment_date` are optional; every other column is required. **Note:** `roll_number` is
accepted as a column for backward compatibility but its value is always discarded — the
actual roll number is system-generated (`<2-digit-year><sequence>`, e.g. `26001`) and also
becomes the student's login username. See `app/imports/validators.py` for exact format
rules (CNIC: `12345-1234567-1`; phone: `03XXXXXXXXX` or `+923XXXXXXXXX`; dates: `YYYY-MM-DD`,
`DD/MM/YYYY`, or `DD-MM-YYYY`).

**Approved policy**: if any row in the file fails validation, the entire file is rejected
and nothing is imported. Fix the reported rows (`GET
/api/v1/imports/students/{id}/errors`) and re-upload.

## Key architectural decisions already approved

- Roll numbers and employee IDs are **system-generated and sequential**
  (`<2-digit-year><seq>` for students, `EMP-NNNN` for employees) and double as login
  usernames — not derived from CNIC or program code. An earlier format was retired and,
  per a later approved decision, existing records were retroactively renamed to match
  (migration `e2b7c4f91a6d`) — see `app/core/sequences.py` for the full history.
- Roll numbers are **globally unique** across the institute (not per-program).
- CNIC is **mandatory** for every student record.
- Student and employee accounts are created with an **admin-issued temporary password**,
  forced change on first login (`must_change_password=true`).
- CSV import is **all-or-nothing** per file.
- Fee-structure activation auto-generates a `FeeAssignment` + per-installment ledger debits
  + a payable challan for every currently-active student/employee matching that
  structure's scope — nothing needs to be manually triggered per person.
- Recording a payment and reversing one are different authority levels
  (`PAYMENT_RECORD` vs. `PAYMENT_REVERSE`) — an Accounts Officer can do the former, only
  an Admin can do the latter.

## Project layout

```
app/
├── main.py            # FastAPI app, middleware, exception handlers, health checks
├── core/               # config, db, redis, security, storage, logging, exceptions, RBAC deps, pagination, sequences
├── auth/                # users, roles, permissions, JWT, refresh rotation
├── audit/                # write-only audit log
├── academics/             # programs' course catalog: courses, offerings, enrollment, attendance, marks
├── programs/               # program CRUD (exact-match lookup, no fuzzy matching)
├── students/                # student model, role-scoped schemas, resource-scope authz, CSV import target
├── employees/                # staff records, job roles, payroll
├── fees/                      # fee types/structures/assignments/challans, simulated payment gateway
├── fines/                      # library/disciplinary fines
├── library/                     # catalogue, copies, issue/return, requests, e-resources
├── complaints/                   # student complaint submission/management
├── profiles/                      # self-service profile editing
├── notifications/                  # in-app notification records (no delivery channel wired)
├── reports/                         # dashboards/reporting
└── imports/                          # generic CSV import pipeline (students today)

migrations/versions/           # 29 migrations
scripts/seed_permissions.py    # idempotent role/permission/bootstrap-admin seeding
tests/{unit,integration,security}/  # 123 passing tests
.github/workflows/ci.yml       # pytest (blocking) + ruff/mypy (report-only)
```