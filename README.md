# VIST LMS

Campus administration system for Vertex Institute of Science & Technology — a modular
monolith built with FastAPI, PostgreSQL, and Redis. Despite the name, this is not a
course/quiz/content-delivery LMS (no lessons, quizzes, assignments, or grading rubrics) —
it's an institute management system: students, employees/payroll, fees, fines, library
circulation, complaints, and CSV-based enrollment.

## Status

All of the following are implemented, routed, and covered by automated tests — not just
scaffolded:

- **Auth & RBAC** — users/roles/permissions, Argon2id password hashing (with rehash-on-login
  for parameter upgrades), JWT access tokens + rotating refresh tokens with reuse detection,
  HttpOnly cookie auth for browser clients, permission-code-based authorization,
  resource-scope (IDOR) checks, per-(username, IP) *and* per-username-only login rate
  limiting.
- **Students** — records, manual creation, CSV bulk import (checksum-based idempotency,
  whole-file-reject-on-any-invalid-row policy, automatic account creation with a
  system-generated roll number as username and a forced first-login password change).
- **Academics** — programs, courses, course offerings, enrollment (with real eligibility rules:
  program/semester curriculum matching, credit-hour-per-semester caps, retake handling),
  lecture resources. **Attendance and marks/grading are NOT implemented** — `ATTENDANCE_VIEW`
  and `MARKS_PUBLISH` exist as permission codes in the RBAC seed table but have no endpoint,
  model, or migration behind them at all (dead permissions referencing a future module). The
  only grading-adjacent thing that exists is `EnrollmentOutcome` — an admin-only placeholder
  that marks a completed enrollment Pass/Fail, explicitly documented in the code as standing
  in "ahead of a real Exams/Grading module."
- **Employees & payroll** — staff records, job roles, salary, bonuses, payroll runs.
- **Fees** — fee types/structures (program/semester-scoped or course-scoped), automatic
  per-installment challan generation on activation, manual payment recording, payment
  reversal (a deliberately higher authority level than recording), a simulated payment
  gateway standing in for 1LINK 1Bill until VIST's biller credentials arrive.
- **Fines** — library/disciplinary fines, issue and waive.
- **Library** — catalogue, copy tracking, issue/return, borrower requests, e-resources.
- **Complaints**, **profiles** (self-service), **reports/dashboards**, **audit log**.
- **Notifications** — the data model exists; no delivery channel (email/SMS) is wired to a
  provider yet, so nothing is actually sent anywhere today.

**Known gaps, not hidden:**
- The hostel module is genuinely not built (the `HOSTEL_WARDEN` role exists with zero
  permissions assigned — a deliberate placeholder, not an oversight).
- `ATTENDANCE_VIEW` and `MARKS_PUBLISH` are dead permission codes — granted to roles in the
  seed table but with no endpoint, model, or migration implementing either feature.
- Notifications have no delivery provider wired up (see above).
- CI runs `pytest` as a blocking check; `ruff` and `mypy --strict` currently run
  report-only (see `.github/workflows/ci.yml`) because they surface real pre-existing
  findings across the codebase that predate any CI existing at all — tightening that is
  tracked, not swept under the rug.
- The Docker build itself has not been verified in this environment (no Docker daemon
  available) — see the Docker section below.

## What's been verified

Everything above has been run against a real PostgreSQL 16 + Redis 7 instance, not just
written and assumed correct — including a full live run with `uvicorn`, real HTTP requests,
and a real attempted attack (a spoofed-content-type file upload) that was confirmed blocked:

- **29 Alembic migrations** apply cleanly against a live schema.
- The seed script creates real roles/permissions/admin rows.
- **123 automated tests pass** (`pytest tests/`) — unit, integration, and security suites,
  covering CSV validation, auth/RBAC/IDOR, the fees module's payment authorization
  boundaries, upload content-type verification, and the login rate limiter.
- CI (`.github/workflows/ci.yml`) runs this same suite against a fresh Postgres + Redis on
  every push/PR.

A recent security-and-correctness pass through this codebase (auth, file uploads, the fees
module, CI) found and fixed several real, previously-shipped bugs — not just style issues:
a spoofed-content-type file upload vulnerability, a missing `course_id` field that made
`POST /fees/structures` crash on every real call, a fee-structure clone that silently
dropped its course scope, a response schema that silently dropped fields the router had
already computed, `needs_rehash()` being defined but never called, a login rate limiter
that could be bypassed by rotating source IPs, and 9 tests that had gone stale against a
roll-number/employee-ID format change and were failing for the wrong reason. All of these
have tests locking in the fix, not just a manual note that it was checked once.

**Not verified**: the Docker build itself. No Docker daemon was available in the
environment this was built and audited in. The Dockerfile and docker-compose.yml are
written to the same standard as the rest of the codebase, but you should run
`docker compose build` yourself before trusting it in CI/deployment.

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