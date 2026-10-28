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

# Finance fixes (branch `finance-fixes`, migration `f1e2d3c4b5a6`)

From the October 2026 audit (issue IDs FIN-xx refer to the audit report). Tests: `tests/integration/test_finance_fixes.py`.

## Payments
- A payment can be reversed only once, from either screen (challan "mark unpaid" or receipt "reverse"); reversals now point at their payment, and older unlinked reversals are still recognised (FIN-01).
- Every payment, reversal, refund, scholarship and status change locks the fee first, so two cashiers cannot both collect the same amount (FIN-02).
- Challans follow their installment: a counter payment, waiver or cancellation cancels the challan of a settled installment, a part-payment lowers it, a reversal brings it back (FIN-03).
- A challan payment settles the installment printed on that challan first (counter payments still go to the oldest installment) (FIN-03).
- Waived or cancelled fees collect nothing, through any path; changing a fee's status needs a reason (FIN-12). The simulated / bank path has the same checks (FIN-15).
- Closed books are enforced on refunds, payroll pay and reversal, purchase pay and reversal, and salary advances (FIN-07).

## One set of numbers
- Fee dashboard, student / employee profile and the collection report use the same figures as the student's fee account: scholarships taken off, waived fees left out, reversals and refunds counted (FIN-04, FIN-05, FIN-06).
- New, moved or promoted students are not charged fees of a closed or ended academic term (FIN-10).
- Bank reconciliation reads every day-book row and matches the slip / cheque number first (FIN-14).
- A hostel room move no longer bills the same days twice (FIN-11).

## New screens (Fees page)
- **Student accounts**: one student's whole fee picture, take a payment, confirm a challan the bank collected, receipts.
- **Scholarships & discounts**: give (percentage or fixed, one fee or all, covering installments due between two dates), list, end. Without a start date a scholarship covers the current term (FIN-09).
- **Previous dues**: bring in arrears from the manual system, one student or a CSV (`roll_number,amount,due_date,note`), checked before import; the same row cannot be imported twice; a wrong entry can be cancelled.

## Payroll
- Salary advances: the accounts office asks, an administrator approves (not their own request, never to themselves), then the accounts office pays it. Limit: `payroll_advance_max_salary_multiple` x basic salary (Settings -> Payroll, default 1) (FIN-08).
- Payroll runs: DRAFT -> sent for approval (accounts office) -> APPROVED or sent back (administration) -> PAID (accounts office). Releasing a held payslip is for the administration.
- Nobody can change their own pay items or pay back their own security deposit.
- The accounts office no longer has staff attendance (permissions revoked by the migration; menu entry removed). It still prepares payroll from the attendance totals.

## Deploying
1. Back up the database **and** `var/storage`; check the backup restores.
2. Confirm production is at revision `e3f4a5b6c7d8` (`alembic current`). If not, stop: see the audit report (DB-01).
3. Deploy the code and run `alembic upgrade head` (additive only: new nullable columns, new enum values, two permission rows removed from the Accounts Officer role).
4. Run `python scripts/sync_challans.py` (report only), review, then `python scripts/sync_challans.py --apply` to bring existing challans in step.
5. Tell the accounts office: payroll now needs "Send for approval", and advances need administrator approval before they are paid.

Behaviour to know: for a payment that was reversed through its receipt before this release, the reversal now cancels that payment itself rather than the newest one; totals are unchanged, but which installment shows as paid (and its late fee) can shift on such fees.

# Reports centre (no migration)

One **Reports** page in the admin, accounts, principal, HOD and exam portals. 23 reports in seven groups (fees, accounts, payroll,
academics, staff, store, fixed assets), each with filters, totals, print and CSV download; everyone sees only the reports their
permissions allow. Figures come from the same calculations as the operational screens (tested: fee position = fee dashboard,
statement = student account, income and expenditure = accounts summary, cash book opening = previous closing).
Tests: `tests/integration/test_reports_center.py`.

# Fixed-asset register (migration `a7b8c9d0e1f2`)

Menu: **Fixed assets** (admin and accounts office; read-only for the principal). Tests: `tests/integration/test_assets.py`.

- Every item has its own tag (`VIST-IT-0001`: prefix, category code, number) or keeps a number already painted on it; labels
  with a QR code print 24 to an A4 sheet. Scanning a label opens the asset.
- Categories set the method: straight line (life in years), reducing balance (% a year) or none (land), with an optional
  residual value; an asset can override them. "Add the usual categories" uses the rates commonly used in Pakistan
  (buildings 10%, furniture / equipment / vehicles 15%, computers 30%): **confirm them with your auditor before the first run.**
- Owned before the system: with the real cost and purchase date the system works out what has already been used up to the
  go-live date; without them, enter today's estimated value. One at a time or from a CSV (template on the page), checked first.
- Received purchase orders can be added to the register (one asset per unit). A purchase line goes either to the store or to
  the register, never both.
- Depreciation is posted once a month (preview first). Only the latest run can be reversed, by the administration, never
  inside closed books. Cost and terms can be corrected only before any depreciation is posted for the asset.
- Moving, handing over, condition, status and repairs are kept as the asset's history.
- Disposal (sold, scrapped, donated, lost, written off): the office asks, the administration approves (not their own
  request). Approval posts depreciation to the disposal date, keeps the book value, and records sale money as income
  ("Sale of fixed assets").
- Physical checks: start one for a place or everywhere, scan or type tags; on closing, what was not seen becomes Missing and
  (optionally) things found elsewhere are moved to where they were found.
- Reports: register (on any date), fixed-asset schedule for the financial year, physical check result, disposals, repairs.
  Income and expenditure now also shows the surplus after depreciation (equipment bought through purchases is added back and
  replaced by its depreciation; the book value of disposals is taken off).
- Permissions: `asset.view` (admin, accounts office, principal), `asset.manage` (admin, accounts office),
  `asset.approve` (admin).

## Deploying
1. Apply the finance-fixes changes first (migration `f1e2d3c4b5a6`); this builds on them.
2. Back up the database. Deploy the code, run `alembic upgrade head` (additive: nine new tables, three new permissions).
3. Make sure `PUBLIC_URL` is set (e.g. `https://lms.example.edu.pk`) so a phone can open a scanned label. Optional: set
   `ASSET_TAG_PREFIX` if tags should not start with `VIST` (before the first asset is added).

# Security, owner decisions and hardening (migrations `b8c9d0e1f2a3`, `c9d0e1f2a3b4`)

Tests: `tests/integration/test_security_fixes.py`, `test_fee_rules.py`, `test_approvals_and_rules.py`.

## Security (audit SEC-01, SEC-02, SEC-03, SEC-04, OPS-01)
- **Real visitor address.** Uvicorn no longer trusts every `X-Forwarded-For` (`FORWARDED_ALLOW_IPS` was `"*"`); the app reads
  the header from the right, so a visitor cannot fake their address to dodge the login limit. Example nginx site:
  `docker/nginx-vist.conf.example` (`proxy_set_header X-Forwarded-For $remote_addr;`).
- **Failed sign-ins are kept.** Wrong passwords, blocked accounts and wrong reset codes are written in their own committed
  session, so the request's rollback no longer erases them; five wrong reset codes really finish the code.
- **One-time passwords** are kept only until the admission slip is printed once; printing again needs a password reset.
- **Deploys wait for approval.** The deploy job runs in the GitHub environment `production`. In GitHub: Settings ->
  Environments -> New environment `production` -> Required reviewers (add yourself). Also: Settings -> Branches -> protect
  `main` (require a pull request and the CI checks).
- **Migrations are checked before they run.** `scripts/check_migration_path.py` (run by `deploy.sh`) lists the migrations
  about to run and stops if one deletes data or drops tables, unless its id is in `ALLOW_DESTRUCTIVE_MIGRATIONS`.
- **No secrets in git.** CI fails if a `.env`, private key or real password is committed (`scripts/check_no_secrets.py`).

## Your rules (owner decisions 1-10)
| Decision | What the system does now |
|---|---|
| Late fee | No late fee for `fee_late_fee_grace_days` after the due date; at most `fee_late_fee_cap` per installment (Settings -> Fees; both 0 until you set them). It does not run while a student is suspended or after they leave. |
| Withdrawal | Leaving (Dropped) cancels installments not yet due; anything already due stays owed. The office can tick "keep the fees not yet due" for one student. Withdrawing from a course cancels that course fee's future installments (not when struck off for attendance). |
| Clearance | Completion / character certificates and marking a student Graduated need every fee paid, including installments not yet due and unposted late fees (or a written reason to override). |
| Overpayment | The counter still refuses more than is owed. Money that arrives from the bank / gateway is always recorded; the excess is advance credit, shown on the student's account, usable on another fee ("Use credit on another fee") or refundable through Refunds. A duplicate bank confirmation is ignored. |
| Scholarships | Discount only unpaid installments (unchanged). The accounts office can now propose; an administrator approves before it applies. |
| Hostel | A stay is charged by days. Leaving mid-month replaces an unpaid charge with one for the days stayed; if it was already paid, the amount to give back is shown and logged. A room move never bills the same days twice. |
| Attendance fine | One per student per term; a second short course is added to the same fine. Locking attendance takes a row lock. |
| Second person | Approvals page (admin): waiving or cancelling a fee, waiving a fine, writing off previous dues, reopening closed books and scholarships wait for an administrator other than the requester. With only one administrator they go ahead and the audit log marks them "sole approver". Purchases above `purchase_second_approval_above` (Rs 50,000): the approver must not be the requester, and the payer must not be the approver. |
| Write-off cancelled | Shows as the installment owed again, not as a new installment (FIN-18). |

## Hardening
- **Financial rows protected (DB-03):** nine foreign keys from CASCADE to RESTRICT (fees, payments, challans, refunds, fine
  reversals, salary advances, payslips). Undoing a fee activation removes its unused charges explicitly.
- **Timetable (ACAD-01):** removing a slot no longer deletes past classes that were never marked.
- **SMS sender (OPS-03):** new `sms` service in `docker-compose.prod.yml`, started by the normal deploy.
- **Least-privilege database users (SEC-05 / DB-07):** `scripts/db_roles.sql` creates `vist_owner` (migrations only) and
  `vist_app` (rows only; cannot change tables or delete audit history). Optional, in a maintenance window: run it, then
  set in `.env`: `DATABASE_URL` / `DATABASE_URL_SYNC` with `vist_app`, and `MIGRATION_DATABASE_URL` with `vist_owner`.

## Deploying this release
1. Apply finance-fixes and reports-and-assets first (this builds on them). Back up the database and `var/storage`.
2. Set up the GitHub `production` environment with a required reviewer (above) before merging to `main`.
3. Change the host nginx site to `proxy_set_header X-Forwarded-For $remote_addr;` and reload nginx.
4. Deploy; `alembic upgrade head` runs `b8c9d0e1f2a3` (additive) and `c9d0e1f2a3b4` (constraint swap, run outside
   fee-collection hours).
5. In Settings -> Fees set the late-fee grace days and cap; check Settings -> Accounts -> second-approval limit (Rs 50,000).
6. Tell the office: waivers, write-offs, reopening books and scholarships now appear on the administrator's Approvals page.

# Institute branding and the new Settings page (branch `branding`, migration `d0e1f2a3b4c5`)

Any institute can now set its own identity in **Settings** (administrator only). One profile per installation.

- **Institute tab:** full name, short name (menu, page titles, SMS), motto, address, city, phone, email, website,
  registration / affiliation no.; logo upload (PNG/JPG, at most 2 MB; resized; a browser-tab icon is made from it) and an
  optional white logo for the dark menu.
- **Look & documents tab:** main colour (picker, suggested colours, a warning when white text on it is hard to read, live
  preview, sample challan PDF); footer line for documents; bank details printed on challans; signatory name, title and
  signature image (the image is never public; only administrators can view it).
- **Rules tab:** the policy values, grouped, searchable, "changed" badge and "Use default".
- **My account tab:** password change.
- One save bar for everything, with a warning when leaving with unsaved changes. Every change is in the audit log.

Where it shows: every portal menu, page title, favicon, sign-in / password / landing / certificate-check pages, report print
headers, admission slip; PDFs: challan, receipt, certificates, ID cards, transcript / result card, payslip, admit card, merit
list, timetable; SMS sign-off. Other servers pick up a change within a minute.

Deploy:
1. Back up the database, then `alembic upgrade head` (adds the empty `institute_profile` table and the
   `institute.profile.manage` permission for Administrator; no existing rows change; takes under a second).
2. Until the profile is saved, the `INSTITUTE_NAME` / `APP_NAME` values from `.env` are used. One visible change: menus and
   page titles that said a fixed "VIST" now show `APP_NAME` (e.g. "VIST LMS Exams") until a short name is saved.
3. Sign in as administrator, open Settings → Institute, fill it in and upload the logo.
4. Uploaded images go to the normal file storage (`branding/` folder): include it in backups.

Tests: `tests/integration/test_branding.py`.

# Admin access to every panel, "view as", and the activity log (branch `portals-audit-selfservice`, migration `e1f2a3b4c5d6`)

- **Panels:** the admin menu has a "Panels" group: Accounts office, Exam department, Principal, Gate, Hostel warden
  (plus Library as before). They open with the administrator's own login; "Back to admin" returns.
- **View as a person** (`/dashboard/admin/view-as`): search any teacher, student, parent or staff member and open their
  own screens for up to 30 minutes. Read-only: every request that could change data is refused while viewing
  (`READ_ONLY_VIEW`), and admin pages are closed. A dark bar on every page names the person and has "Exit view".
  Administrators cannot be viewed. Starting and ending a view are in the activity log.
- **Activity log** (`/dashboard/admin/activity`; principal read-only at `/dashboard/principal/activity`): every recorded
  action with the person's name and kind (administrator / staff / student / parent / system), area, record, time and IP
  address. Filters, quick filters, "only this person", before/after detail, CSV download (administrators only; each
  download is itself logged). The admin dashboard's Recent activity has "View all".

Deploy: back up, then `alembic upgrade head`. The migration only adds 2 permissions and 3 grants (no table changes;
safe at any time). Restart the app.
Tests: `tests/integration/test_view_as_and_activity.py`.

# Self-service profile changes with approval and a face check (migration `f2a3b4c5d6e7`)

- **Students and staff** ("My Profile" → "Update my details") can ask to change their mobile number, email, home address,
  city, emergency contact, and (students) the guardian's phone. Name, CNIC, date of birth, roll number and program stay
  office-only. Nothing changes on the record until an administrator approves. One waiting request per person; new
  changes are added to it; the person can take a photo back or withdraw the whole request.
- **Photos**: a person's own upload no longer publishes at once. It is first checked automatically, offline, for exactly
  one clear, uncovered face (refused with a plain reason: no face, two people, mask or niqab over the mouth and chin,
  sunglasses, too dark, blurred, too small). A photo that passes waits for the administrator; the old photo stays on ID
  cards until then. The administration's own uploads still publish at once.
- **Administrators**: People → "Profile changes" shows each request with the current and new values side by side and the
  current and new photo; Approve applies everything at once; Reject needs a reason (quick reasons offered) which the
  person sees. Waiting requests appear in the admin dashboard's "Needs your attention". The person is notified.
- Every request, refused photo, approval and rejection is in the activity log, approvals with before / after values.

New dependency: `numpy==2.1.3` and `opencv-python-headless==4.10.0.84` (pinned with hashes for Python 3.12 / x86_64 in
`requirements.lock`; rebuild the Docker image). If the library is missing the check is skipped and the administrator
decides alone. The check is a first filter, not perfect; the administrator's approval is the final check.

Deploy: back up, rebuild the image, `alembic upgrade head` (creates one empty table and one permission; safe at any
time), restart. Tests: `tests/integration/test_profile_changes.py`; two photo tests in
`test_file_download_authorization.py` now expect a person's own photo to wait for approval.

# General staff role (migration `a3b4c5d6e7f8`)

- **New job title "General Staff"** (system role `STAFF`) for an office boy, IT person, driver, cleaner and similar. Add
  them like any employee (Employees → choose the job title "General Staff", or make your own titles such as "Driver"
  that map to the Staff role).
- **Their own portal** (`/dashboard/staff`, where they land after signing in): Home (today's arrival, this month's
  attendance, open tasks, requests waiting, last salary, calendar and the latest notice), Tasks, My pay & attendance,
  Announcements, Applications (leave and others), Purchase requests, Feedback & complaints, Calendar and My Profile.
- **What they cannot see**: students, fees, marks, payroll of others, the library, the hostel, the staff board or the
  activity log (all refused by the server, not just hidden). Their only permissions are submit applications, use tasks,
  request purchases and file complaints; pay, attendance, notices, calendar and profile are their own records.
- **My Profile for every office**: accounts, library, exam, gate and warden portals now have "My Profile" (same page as
  faculty), so those staff can also ask for contact-detail and photo changes. A profile-change decision notification now
  opens the person's own My Profile.
- Administrators: Panels → "General staff"; tasks can be given to staff; staff appear in "View as a person".
- Fixes found on the way: staff-only announcements were readable by parents (parents counted as "staff"); calendar
  notices skipped the hostel warden (wrong role name).

Deploy: back up, `alembic upgrade head` (data only: adds one role, one job title and four grants; safe at any time),
restart. Tests: `tests/integration/test_staff_role.py`.

# Student screens (no migration)

- **Admin → Students**: a fuller table (photo or initials, name, roll number, gender, program, semester and section,
  mobile, session, status), search, status and program filters, "Add student" and a tidy "More" menu (advance semester,
  export, print ID cards). On phones each row is a compact card.
- **One record dialog** instead of two: a header with the photo, name, roll number, program and status, and small icons
  for the transcript, admission slip and documents; tabs Overview (fees, fines, library, complaints, admission, personal,
  contact), Guardians, Courses (by term), Edit details and Status & access (status, password reset, parent portal,
  corrections). Full screen on phones.
- **Photos**: click the photo (or the small camera) and choose an image; it uploads at once. A small download icon and a
  small remove icon replace the old buttons. The same on My Profile for students and all staff (their own photo still
  goes through the face check and the office's approval).
- **Principal, HOD and exam office → Student lookup**: the same table (browse, not only search), filters, and the same
  dialog read-only. The exam office can now open a record (before, every "Open" was refused).
- API: the student list returns program code and name, gender, mobile, session and whether there is a photo (still no
  CNIC, guardian or address); `GET /students/programs` feeds the program filter.

Tests: `tests/integration/test_student_screens.py`.

# Employees screen and the busy screens (no migration)

- **Admin → Employees**: the same treatment as Students. A fuller table (photo or initials, name, code, job title,
  department and type, mobile, joining date, status), search, status and job-title filters, "Add employee" and a "More"
  menu (import, export). One record dialog with tabs: Overview (salary, bonuses this year, pass rate or library, complaints,
  employment, personal, contact, teaching), Pay & bonuses (salary history and increases, bonuses), Qualifications, Edit
  details, and Job, status & access (job title change, status with clearance, extra access, password). Click the photo to
  upload; small download and remove icons.
- **Job titles tab**: the "portal and access" list now offers every portal (exam, gate, general staff were missing) and no
  longer offers Administrator, which the server always refused.
- **Every screen**: tables fill their panel (many were squeezed to the left) and scroll sideways inside their own box on
  small screens; space between tables and the forms next to them; amounts in the number tiles are no longer cut off
  ("Rs. 270,..."); the finance chart is no longer oversized and its bars show rupees; finance lists show the title and the
  detail on two lines; the admission round form is a tidy labelled grid; pages shared with the exam / accounts offices
  show their own portal name under the logo.
- API: the employee list returns the job title, department, mobile, joining date, type and whether there is a photo
  (no CNIC, address or bank details).

# Pay and performance (no migration)

- **My security deposit** on every employee's "My pay & attendance" page: held now, the target (one month's basic
  salary), still to collect, the % kept each month, any amount paid before this system, month-by-month cuts, and the
  pay-back when they leave. Shown only to people who have a security deposit. API: `GET /payroll/me/security`.
- **Absences after a paid payroll**: absences and late marks in a month that came after that month's payroll was worked
  out were never deducted. The next month's payroll now compares last month's paid payslip with last month's full
  attendance and takes only the extra days, once, at last month's day rate, as its own line ("September 2026, after that
  payroll was paid: 2 days absent"). Nothing is carried when the administration overrode last month's attendance, and it
  never refunds. It shows under "Absences carried from the month before" in the withholdings report.
- **Staff scorecard**: one score out of 100 per employee per month from records already kept: attendance (40),
  punctuality (25), tasks done on time (25) and complaints against them (10). A part with nothing to measure is left
  out; people with no attendance recorded are not scored. Admin dashboard: "Top performers" of last month with a
  one-click "Give bonus". Employees → Performance: everyone, any month, with each part and the reason behind it.
  Scores never change pay; a bonus is always the administration's decision and goes on the next payroll.
  API: `GET /performance?month=YYYY-MM-01` (administration only).

Tests: `tests/integration/test_pay_and_performance.py`.

# Courses page and editing academic terms (no migration)

- **Academic terms are now editable**: a new "Academic terms" tab on the Courses page. Each term shows its dates and
  length, a "Current" badge, what uses it (course offerings, fee structures, datesheets, admission rounds) and its status.
  **Edit** changes the start and end dates, the status (open / closed) and the **name** at any time (see below).
  Every change is in the activity log. API: `PATCH /academics/academic-terms/{id}` accepts `label`;
  `GET /academics/academic-terms` returns `used_by`.
- **Catalog**: search and active / inactive filter, course code badges, descriptions, one Edit button per row.
- **Curriculum**: grouped by semester as cards with course and credit-hour totals; a one-line "add a course" bar.
- **Offerings**: one tidy toolbar (term, "only without a teacher", Create offering, Open a semester, More: readiness,
  auto-schedule, copy timetable, end semester); a summary (offerings, without a teacher, enrolments, open for enrolment);
  each row shows course, section, credit hours, program, teacher, seats with a fill bar, enrolment status, a Manage button
  and a small menu (open / close enrolment, enrol students, seats, delete).
- **Teacher performance**: pass rate as a coloured bar; complaints highlighted.

# Renaming a term everywhere, Programs page, Timetable and Attendance screens (no migration)

- **Renaming a term now works even when it is in use.** The new name is written, in one step, on every course offering,
  fee structure, datesheet and admission round that carries the old name, and the activity log records how many of each
  were changed (`academic_term.renamed`). Refused if the new name is already a term, or if some records already use the
  new name (so two terms never merge by accident). Challans, receipts and certificates already printed keep the old name
  on paper; the Edit dialog says so and asks before renaming a term in use. Dropdowns on the Courses page refresh at once.
- **Programs page**: one card per program with its code, status, years and semesters, active students, courses and credit
  hours in the curriculum, and how many semesters are planned. Search, status filter and a summary. Each card opens the
  program's curriculum (Courses page) or its students (Students page, already filtered). The Add and Edit forms now also
  set the credit-hour cap per semester. New read-only endpoint: `GET /programs/overview`.
- **Timetable**: All slots has a search (room, course or teacher), a day filter and a count; Daily periods shows each
  period's length, suggests the next period number, adds in one row and asks before deleting in the app's own dialog.
- **Attendance**: page description; counts on the Needs attention and Correction requests tabs; Mark attendance has
  labelled Term / Course / Class pickers that load as you choose (no Refresh / Load buttons); **Mark now** opens the Mark
  tab with the right term, course and class (before, it scrolled to a hidden tab and looked like it did nothing); the
  Present / Absent toggle sits side by side again (a later style rule had stacked it, on the teacher's page too); Lock the
  term shows the chosen course and its check as tiles (problems in red / amber) and explains what to do when no course is
  chosen; Holidays and Approved leave have labelled fields.

# Exam screens, teacher attendance, Teacher Progress, Hostel (no migration)

- **Result Types**: a summary by category, categories as chips, max marks right-aligned, one-row "Add a type" form that
  suggests the categories already in use, and the app's own confirm dialog before deleting.
- **Datesheets & admit cards**: a labelled "create" form, a list with Published / Draft badges and the date published,
  the open datesheet highlighted, a paper and exam-day count, a labelled "Add a paper" form, papers with full dates and
  course code badges, clashes as a clear list, Publish disabled until there are papers and no clashes, and the admit card
  search next to it. Papers can still be added or removed after publishing (the server re-checks clashes, as before).
  Shared with the Exam Controller portal.
- **Supplementary exams**: the list loads as soon as the term is chosen (no Show button); a summary (can sit, registered,
  marked, published); fail results, marked and published states as coloured badges; a count of ticked students; the
  Publish button only appears when something is marked.
- **Teacher attendance**: a **Today's classes** card at the top shows each class today with its time, room and whether
  the marking window is open (green, with the time it closes), not started yet, or closed (send to the office). One tap
  opens that class's register. Term / Course / Class pickers are labelled; the Correction requests tab shows how many are
  waiting.
- **Teacher Progress**: courses most behind first; a coloured progress bar with "10 of 18 due so far"; search by teacher
  or course, term and status filters, and clickable status counts (Behind, A little behind, On track, Not started).
- **Hostel**: the room table's column headings now match the columns (they were shifted by one); beds used as a bar with
  "n free" / "Full"; labelled, tidy Add-a-room and Monthly-charge forms; residents stack properly on phones.
- **Institute Overview**: the "course(s) have no result sheet yet" line no longer overlaps the boxes above it.
- **General staff and Hostel warden homes**: when the administrator opens them from the Panels menu without "View as",
  the page no longer makes personal requests that can only fail (own attendance, pay, requests); it says so and links to
  "View as a person".

# Teacher grading, office screens, principal and HOD fixes (no migration)

- **Teacher grading**: the course's result types as cards with a "Total 100 marks" badge (× to remove while editable);
  a **Total** column that adds up each student's marks as you type, with a mark above that type's maximum outlined in
  red; once the marks are submitted, approved or published they are shown as plain read-only numbers (before, the locked
  boxes looked editable) and the hint says why; everyone's marks load at the same time instead of one student after
  another (faster for big classes); the marks sheet download / check / upload sits in one tidy bar, and the upload part is
  hidden while the marks are locked; the app's own dialogs for remove and submit.
- **Gate visitor log** (admin and guard): 7 columns instead of 10: visitor (with reference and where from), when
  (in → out or "inside", with the date written out), CNIC / phone, purpose / meeting, vehicle / people, entered by.
  Lines stay together on phones.
- **SMS log**: time and written-out date, recipient type under the number, status as a coloured badge
  (Waiting / Sent / Failed / Logged).
- **Inventory → Issue items**: the form uses two columns instead of one narrow column.
- **Head of department → Attendance**: no "Mark Now" buttons any more (that page is read-only for them, so the buttons
  could not work).
- **Principal → Staff attendance**: no In / Out / Undo buttons (the principal can view but not mark; the server refused
  those presses). The administrator and the gate keep them.

# Accounts and Exam Controller portals: Fines and Refunds (no migration)

The Exam Controller portal and most Accounts pages (home, Fees, Accounts, Payroll, Approvals) were already in the new
style and are unchanged. Two Accounts screens were reworked; both are shared with the admin (and Fines with the library).

- **Fines**: amounts as money ("Rs. 6,129" instead of "6129.00"); payer name with roll number / code and "Student" or
  "Staff" underneath; source as a plain label (Hostel, Library, Store…); status shown as Unpaid / Paid / Waived;
  a search box (name, roll no., reason or receipt number); a summary of how many fines, their total and how many
  people; **Hostel charge** added to the source filter (hostel charges could not be filtered before); Waive as a quiet
  red button next to Mark paid.
- **Refunds**: labelled fields (roll number, fee, kind, refund, write-off, reason) instead of bare boxes; the list shows
  the payer with roll number and request date, the refund in bold, write-off only when there is one, status as a
  coloured badge, and Approve / Reject / Pay out as proper buttons. The status filter sits in the list header.

# Payroll: a day's pay from each person's own working days (no migration)

- **A day's pay = basic salary ÷ that person's own working days in the month**, taken from their timing in
  Staff attendance (their working weekdays). Someone due 8 days a month earns basic ÷ 8 a day, so missing 7 of them
  costs 7/8 of the basic (less any casual leave); someone on Monday–Saturday is paid basic ÷ 27 a day in October 2026.
  Before, everyone was divided by the calendar days (30 / 31), which made a missed day too cheap.
- This is what "Working days in a month" = **0** now means (the default). A number there (e.g. 26) is still used for
  everyone, and a number typed when preparing a payroll is used for that payroll. A payslip's working days can still be
  changed by hand with Adjust, and Recalculate keeps what was set; only people added later get their own count.
- Holidays excused for everyone still count as working (paid) days. Paid payrolls are not changed; a draft prepared
  before this update keeps its numbers. **Cancel and prepare it again** to use the new rule.
- **Before preparing**: give anyone who works only some days their own timing (Staff attendance → Timings → their days),
  otherwise they are expected every Monday–Saturday and counted absent on the other days. If their days are not fixed
  weekdays, set Working days on their payslip with Adjust.
- Payroll run window: the Absent column shows "7 / 2 forgiven (casual leave) / 5 charged" on separate lines (it looked
  like "72 casual"), and the Days column is now **Day rate** ("÷ 27" and the rupees per day).