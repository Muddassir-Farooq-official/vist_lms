# What was repaired in this copy

The uploaded project could not start: about a dozen modules were missing or empty, the migration chain had three heads and two
missing parents, several pages and routes the frontend calls did not exist, and some features were half-removed. Everything below
now exists and is covered by the test suite (751 tests, all passing; 85 migrations, one head, models match the schema).

## Rebuilt modules (were missing or 0 bytes)
`app/core/{csvsafe,net,alerts,scope,controls,duplicate_guard}.py`, `local_date` in `app/core/clock.py`, `app/fees/{money,references}.py`,
`app/records/{models,pdf,service,router}.py`, `app/reports/{finance_service,finance_router}.py`.

## Migrations
Renamed the files with a trailing comma / space; removed three duplicates from `app/fees/`; recreated `d39fa0b1c234`
(fine payment reversals); added `c1d2e3f4a5b6` (users.temp_password), `d2e3f4a5b6c7` (enrollment withdrawal reason),
`e3f4a5b6c7d8` (payslips.released_on). Added the model columns the existing migrations already created.

## Pages added
calendar, hostel, finance reports, student lookup, student records, certificate check (`/verify/<code>`), forgot password;
plus lost fragments on students, employees, payroll, staff payslips, result sheets, offering detail and SMS pages.

## Features restored or completed
Identity correction; parent sibling linking; leave excuses attendance; staff "my attendance"; archived replaced files;
principal/HOD read-only student lookup; store-return refunds; bulk SMS; fee payment reversal (VOID receipt); held payslip release;
security deposit release; refund cancel; undo fee activation; late-fee cap; closed-books and reused-reference checks on fee payments;
purchase price approval and unique invoices; withholdings report; random one-time student passwords; server-side account re-check.

## Behaviour changes to know about
- New students get a random one-time password (shown on the admission slip) instead of a shared default.
- Accounts are re-read from the database every `SESSION_CHECK_SECONDS`, so deactivation and role changes take effect at once.
- Payroll default working days = calendar days of the month (policy value 0); set 26 or 22 to fix it.
- A late fee on an instalment that has since been paid no longer counts as "due now".
- Three older tests were updated because the hostel and calendar menu entries now exist.

## Not verified
Docker image build; a real SMS provider; real bank/1Link integration. `requirements.lock` pins current versions with hashes.
`.env` and `.env.production` were left out of this archive; `.env.example` is provided. The originals contained database
credentials: rotate them.
