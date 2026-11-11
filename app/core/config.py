"""Central application configuration.

All environment-specific values come from environment variables (Section 38
of the architecture spec). Nothing here is hard-coded per-deployment; the
same image runs in dev/staging/prod purely by changing the environment.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_env: str = Field(default="development")
    debug: bool = Field(default=False)
    app_name: str = Field(default="VIST LMS")
    # Optional: an incoming-webhook URL (Slack, Discord, Google Chat, Teams...) told about unexpected server errors.
    error_alert_webhook: str = ""
    api_v1_prefix: str = Field(default="/api/v1")

    # The institute's identity. These are only the starting values: an administrator changes them in
    # Settings -> Institute (stored in the institute_profile table), and app/branding applies them to every page,
    # PDF, report and SMS within seconds.
    institute_name: str = Field(default="Vertex Institute of Science & Technology")
    institute_logo_path: str = Field(default="static/images/vist_logo.png")
    institute_tagline: str = ""
    institute_address: str = ""
    institute_city: str = ""
    institute_phone: str = ""
    institute_email: str = ""
    institute_website: str = ""
    institute_registration_no: str = ""
    institute_logo_dark_path: str = ""
    institute_favicon_path: str = ""
    institute_signature_path: str = ""
    institute_signatory_name: str = ""
    institute_signatory_title: str = "Principal"
    brand_color: str = ""                    # e.g. #0b6b5d; empty = the standard blue theme
    document_footer: str = ""                # printed at the foot of receipts, certificates and challans
    bank_name: str = ""
    bank_account_title: str = ""
    bank_account_no: str = ""
    bank_branch: str = ""
    brand_version: int = 0                   # changes whenever the profile or a logo changes (cache-busting)

    # Database
    database_url: str
    database_url_sync: str
    # Per app process. Gunicorn runs 4 processes, plus the import worker and the SMS sender:
    # 4 x (5 + 5) + a few = about 45 connections, well under PostgreSQL's default limit of 100.
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT / Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 14
    cookie_secure: bool = True
    cookie_domain: str | None = None

    # CORS
    cors_origins: str = "http://localhost:8000"
    # The address people type to reach the portal (printed on certificates for verification). Empty = the first CORS origin.
    public_url: str = ""
    # Phone / browser push notifications (app/push). Make the keys with: python scripts/push_keys.py
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""                         # mailto:admin@your-institute.edu.pk (push services contact this address if needed)
    asset_tag_prefix: str = "VIST"          # fixed-asset tags: VIST-IT-0001 (set before the first asset is added)

    # File storage
    storage_backend: str = "local"
    storage_local_path: str = "./var/storage"
    storage_bucket: str | None = None
    storage_endpoint_url: str | None = None
    storage_access_key: str | None = None
    storage_secret_key: str | None = None

    # CSV import
    import_max_file_size_mb: int = 20
    import_batch_size: int = 500

    # Notifications
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    sms_provider: str | None = None
    web_app_sends_when_sender_down: bool = True   # the web app sends queued SMS / emails itself when the 'sms' service is not running
    sms_api_key: str | None = None

    # Institute wall-clock timezone: drives 'today', attendance windows, due dates.
    institute_timezone: str = "Asia/Karachi"

    # Library rules (editable here now; a Settings screen will edit them later)
    library_max_loans_student: int = 5           # books one student may hold at once
    library_max_loans_employee: int = 10         # books one staff member may hold at once
    library_block_if_unpaid_fines: bool = True   # cannot borrow while any fine is unpaid
    library_block_if_overdue: bool = True        # cannot borrow while any book is overdue
    library_late_fee_per_day: float = 10.0       # Rs. per day a book is late (0 = no automatic late fee)
    library_late_fee_max: float = 0.0            # cap on one late fee (0 = no cap)

    # Show a clear "run alembic upgrade head" message when the database is behind the code.
    check_migrations: bool = True

    # When a student submits a Google Drive link, quickly check that it is shared ('Anyone with the link'). Never blocks on a
    # network problem; if the link looks private the student is warned and may still submit.
    assignment_drive_access_check: bool = True

    # A task's deadline must be at least this many hours away (when it is set, and whenever it is changed).
    task_min_notice_hours: int = 3
    # Accounts: an expense above this amount (Rs.) recorded by the accounts office waits for the administration to approve it.
    expense_approval_limit: float = 50000.0
    # The first month of the financial year (7 = July, as is usual in Pakistan). Used by the accounts report's "this financial year".
    financial_year_start_month: int = 7

    # Leave-type applications may start this many days in the past (e.g. a sick day reported afterwards), and no more than a year ahead.
    application_max_backdate_days: int = 30

    # Any request slower than this is written to the server log (path, time, number of database queries).
    slow_request_ms: int = 1500

    # Rate limiting
    rate_limit_login_per_minute: int = 5
    # Reverse proxies whose X-Forwarded-For is believed (comma-separated addresses or networks): loopback and Docker's private ranges.
    trusted_proxies: str = "127.0.0.1/32,::1/128,172.16.0.0/12,10.0.0.0/8"
    # The above is per (username, ip) pair — deliberately generous enough
    # that a shared office/campus NAT IP with several people logging in
    # doesn't trip it, but that also means it does nothing to slow a
    # credential-stuffing attack that rotates source IPs against one
    # username. This second limiter is per-username ONLY, across a
    # longer window, specifically to cap that case regardless of how
    # many IPs are involved. Deliberately more permissive per-minute
    # than the per-IP limit (a real user retrying a forgotten password
    # a few times shouldn't get locked out) but caps the AGGREGATE.
    rate_limit_login_per_username_per_hour: int = 20
    rate_limit_import_per_hour: int = 10

    # Fee payments — VIST has applied for 1LINK 1Bill biller API access
    # but does not have credentials yet (see app/fees/payment_gateway.py).
    # Both settings below default to the SAFE/OFF state deliberately —
    # a real security review found the webhook wide open and the
    # student self-pay simulator reachable by any real student account.
    # Nothing here should be turned on outside a demo/test environment
    # until real 1LINK credentials and signature verification exist.
    payment_webhook_shared_secret: str | None = None  # None/empty = webhook endpoint refuses every request
    enable_student_payment_simulation: bool = False  # False = only ADMIN can use the simulated "pay" button

    # Attendance & Marks — approved decision: one institute-wide set of
    # thresholds, not configurable per course/offering. Applied by
    # AttendanceService.lock_attendance() when an admin finalizes an
    # offering's attendance for the term.
    #
    # Below this %, a student can never receive a PASSED outcome for
    # that course (they can still be marked FAILED) — checked at grade
    # computation time, not at lock time, since it depends on the
    # student's attendance at whatever point their grade is computed.
    attendance_eligibility_threshold_percent: float = 75.0
    # At/below this % (but above the drop threshold), lock_attendance
    # auto-issues a Fine (source=ATTENDANCE) for the configured amount —
    # no approval step, same as fees auto-charging on enrollment.
    attendance_fine_threshold_percent: float = 65.0
    attendance_fine_amount: float = 1000.0
    # At/below this %, lock_attendance auto-withdraws the student from
    # the course entirely instead of just fining them — this check
    # takes priority over the fine threshold above (worse outcome wins).
    attendance_drop_threshold_percent: float = 40.0
    # Graded attendance fines (attendance_fine_mode = 1): per subject, by band. From the minimum to pass down to band 1's
    # lower edge = band 1, and so on; below band 3's lower edge the student is listed for an administrator's decision.
    # Mode 0 = the older rule above (one fine per term at lock, automatic drop). Paying a graded fine (or a waiver)
    # lifts the "minimum attendance to pass" condition for that subject: marks decide.
    # Two-step sign-in (app/auth/two_factor.py): administrators must use an authenticator app code; a browser can be
    # remembered for this many days.
    two_factor_required_for_admins: int = 1
    two_factor_remember_days: int = 30
    attendance_fine_mode: int = 1
    attendance_band1_from: float = 65.0
    attendance_band1_amount: float = 3000.0
    attendance_band2_from: float = 55.0
    attendance_band2_amount: float = 6000.0
    attendance_band3_from: float = 45.0
    attendance_band3_amount: float = 10000.0
    attendance_weekly_warnings: int = 1             # tell students (and guardians) each week when a subject is in a fine band
    attendance_warning_min_classes: int = 6         # ... once at least this many classes of the subject have been taken
    # --- Launch guards (Step 2). All default to the SAFE behaviour. ---
    # Results may only be published once the course's attendance is locked
    # (i.e. final). An ADMIN can override one sheet with a written reason.
    purchase_over_estimate_percent: float = 10.0    # goods costing more than this % over the approved estimate need the price approved before payment
    fee_late_fee_cap: float = 0.0                   # most late fee one instalment can build up (Rs.); 0 = no cap
    fee_late_fee_grace_days: int = 0                # no late fee for this many days after an instalment's due date
    fee_late_fee_auto_post: int = 1                 # 1 = when a late instalment is paid, its late fee becomes a Fine to collect
    fee_late_fee_double_every_days: int = 0         # the daily late fee doubles after every this many late days (0 = never, flat rate)
    fee_late_fee_max_per_day: float = 0.0           # the daily late fee never goes above this (Rs.); 0 = no limit
    # 1Bill (1LINK) bill payment. Off until the bank's details arrive: ONE_BILL_ENABLED=1 and a username/password in .env.
    one_bill_enabled: int = 0
    one_bill_username: str | None = None
    one_bill_password: str | None = None
    one_bill_allowed_ips: str = ""                   # comma-separated; empty = any address (the username/password still apply)
    one_bill_convenience_fixed: float = 0.0          # Rs. added to every 1Bill payment (Settings -> Fees)
    one_bill_convenience_percent: float = 0.0        # % of the challan added to every 1Bill payment
    purchase_second_approval_above: float = 50000.0  # purchase orders / payments above this need a second administrator (0 = never)
    require_attendance_lock_before_publish: bool = True
    # End Semester only after every course with students has locked attendance and a published result
    # (otherwise students are closed out with no result at all). An administrator can still override with a reason.
    require_results_before_end_semester: bool = True
    # Withdrawing from a course in the first N days of term leaves no mark; after that it shows as W
    # on the transcript, and students can no longer withdraw themselves.
    withdrawal_deadline_days: int = 28
    supplementary_publish_by_other: bool = True     # whoever enters a supplementary mark does not publish it
    # Attendance may only be locked once no classes remain and the term end
    # date (if set) has passed. An ADMIN can lock early with a written reason.
    block_early_attendance_lock: bool = True
    # Old behaviour: an attendance fine disabled the student's whole login.
    # New default: the login stays open; self-enrollment and the result card
    # are on hold until the fine is paid or waived.
    attendance_fine_blocks_login: bool = False
    # Fees (Step 3). An instalment created AFTER its due date (a fee activated
    # late, or previous dues imported) only starts accruing a late fee this
    # many days after it was created, never from the old due date.
    fee_backdated_grace_days: int = 7
    # Payments may be dated (paid_on) at most this many days in the past.
    fee_payment_max_backdate_days: int = 90
    # Promotion (Step 4): Advance Semester holds a student back when
    # - more than this many failed courses are still not cleared (passed on retake)
    promotion_max_courses_to_clear: int = 2
    # - CGPA (from published results) is below this
    promotion_min_cgpa: float = 1.5
    # Terms (Step 7): new offerings and fee structures must use a term from the
    # Academic Terms list, so the same term can never be typed two ways.
    require_registered_terms: bool = True
    # Exams: withhold the admit card while the student has overdue fees (1 = yes, 0 = no).
    admit_card_requires_fees_cleared: int = 1
    # Exam duty: one teacher on duty for every this many students sitting a paper (app/exams/duty.py).
    exam_students_per_invigilator: int = 30
    # SMS (see app/sms/service.py). No SMS_PROVIDER_URL = messages are recorded (LOGGED) but not sent.
    # Supplementary exams: the most grade points a supplementary pass can earn (0 = no cap; e.g. 2.0 = at most C).
    supplementary_max_grade_points: float = 0.0
    sms_provider_url: str = ""
    sms_provider_method: str = "GET"
    sms_provider_body: str = ""
    sms_api_key: str = ""
    sms_sender_id: str = ""
    sms_success_marker: str = ""
    sms_number_format: str = "92"
    # Which events send an SMS (1 = yes, 0 = no); editable in Settings -> Institute Policy.
    sms_on_attendance_fine: int = 1
    sms_on_attendance_warning: int = 1
    sms_on_datesheet: int = 1
    sms_on_results: int = 1
    sms_on_fee_overdue: int = 1
    sms_on_fee_reminder: int = 1             # 7 and 3 days before a challan is due, and on the day
    # Certificates on request (app/records/requests.py): fee per kind, Rs. 0 = free. Editable in Settings.
    certificate_fee_bonafide: float = 300.0
    certificate_fee_character: float = 300.0
    certificate_fee_completion: float = 500.0
    certificate_fee_provisional: float = 1000.0
    certificate_fee_migration: float = 2000.0
    certificate_fee_fee_clearance: float = 0.0
    # Notification sound and phone alerts (app/notifications/router.py /sound, static/js/notify-sound.js). Editable in Settings.
    notify_sound_tone: int = 1               # soft chime
    notify_sound_urgent_tone: int = 7        # alarm
    notify_sound_volume: int = 70
    notify_sound_repeat: int = 1
    phone_alert_vibrate: int = 1
    phone_alert_keep_urgent: int = 1
    monthly_report_notice: int = 1          # the 1st of each month: tell the principal last month's report is ready
    sms_on_exam_duty: int = 1                # tell a teacher by SMS / WhatsApp when they are put on (or taken off) exam duty
    sms_on_warning: int = 0                  # tell the guardian when a student is warned (app/discipline); off unless switched on
    sms_on_fee_paid: int = 1                 # a message (receipt on WhatsApp) for each fee payment
    sms_on_certificate_ready: int = 1        # a message (certificate on WhatsApp) when a requested certificate is ready
    sms_on_parent_weekly: int = 1            # Monday note to parents when a child needs attention
    sms_to_guardian: int = 1
    sms_on_admission: int = 1
    sms_on_security_alert: int = 1                  # text administrators when a security alert fires (at most once per half hour per kind)
    # The weighted-average cutoff on a course's graded components (see
    # app/academics's AssessmentComponent/StudentMark) that separates
    # an auto-computed PASSED from FAILED.
    marks_pass_threshold_percent: float = 50.0
    # A teacher can only mark a session's attendance within this many
    # minutes of its scheduled start_time (approved rule) — admin is
    # exempt and can mark anytime, but must supply a note when marking
    # outside this window (see AttendanceService.mark_session).
    attendance_marking_window_minutes: int = 15
    missed_class_alerts: int = 1                    # 1 = tell the administration when a teacher who checked in did not take a class

    # Payroll from staff attendance (editable in Settings). A day's pay = basic salary / working days of the run.
    payroll_default_working_days: int = 0           # 0 = each person's own working days in the month (from their timing); or one number for everyone, e.g. 26
    payroll_attendance_deductions: int = 1          # 1 = deduct absences and late / early marks from pay
    payroll_casual_leaves_staff: int = 2            # absences forgiven per month for non-teaching staff
    payroll_sick_leaves_per_year: int = 8           # paid sick leave a year (approved leave applications, calendar year)
    payroll_casual_leaves_teacher: int = 0          # teachers: no casual leave
    payroll_late_marks_per_day: int = 4             # this many late arrivals / early leavings = one day's pay
    payroll_missing_checkout_counts: int = 1        # 1 = a day with no leaving mark counts as leaving early
    payroll_advance_max_salary_multiple: float = 1.0   # an employee may owe at most this many months' basic salary in advances
    payroll_security_percent: float = 0.0           # % of basic held back each month as a security deposit, until it equals one basic salary
    graduation_min_cgpa: float = 2.0                # degree audit: the lowest CGPA that can graduate
    todo_reminder_hour: int = 15                    # staff with no to-do list today get a reminder from this hour (0 = off)
    amount_display: int = 0                         # amounts on dashboard cards: 0 full, 1 K / M, 2 lakh / crore (Settings -> Rules)
    certificate_signatory: str = "Principal"       # printed under the signature line on certificates

    # How long (seconds) one worker trusts "this user is still active and signed in" before checking again.
    session_check_seconds: int = 10
    # The same POST from the same person within this many seconds is refused as a double submission (0 = off).
    duplicate_post_window_seconds: int = 8

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def production_problems(self) -> list[str]:
        """Settings that are unsafe on a live server. The app refuses to start
        in production while any of these is true (see app/main.py)."""
        from urllib.parse import urlsplit

        weak = {"", "changeme", "change_me", "change-me", "secret", "password", "postgres", "pg", "admin", "123456", "test"}
        out = []
        secret = (self.jwt_secret or "").strip()
        if len(secret) < 32 or secret.lower() in weak or "change_me" in secret.lower():
            out.append("JWT_SECRET is missing, too short or still the example value. "
                       "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
        for name in ("database_url", "database_url_sync"):
            pw = urlsplit(getattr(self, name) or "").password or ""
            if pw.lower() in weak or "change_me" in pw.lower() or len(pw) < 12:
                out.append(f"{name.upper()} uses a weak or example database password (use 12+ random characters).")
        if self.debug:
            out.append("DEBUG must be false.")
        if self.enable_student_payment_simulation:
            out.append("ENABLE_STUDENT_PAYMENT_SIMULATION must be false.")
        if not self.cookie_secure:
            out.append("COOKIE_SECURE must be true (the site must run on https).")
        if "*" in self.cors_origins_list:
            out.append("CORS_ORIGINS must list your real site address, not *.")
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()