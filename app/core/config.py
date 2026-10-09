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
    api_v1_prefix: str = Field(default="/api/v1")

    # Database
    database_url: str
    database_url_sync: str
    db_pool_size: int = 10
    db_max_overflow: int = 20

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
    # --- Launch guards (Step 2). All default to the SAFE behaviour. ---
    # Results may only be published once the course's attendance is locked
    # (i.e. final). An ADMIN can override one sheet with a written reason.
    require_attendance_lock_before_publish: bool = True
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
    sms_on_datesheet: int = 1
    sms_on_results: int = 1
    sms_on_fee_overdue: int = 1
    sms_to_guardian: int = 1
    sms_on_admission: int = 1
    # The weighted-average cutoff on a course's graded components (see
    # app/academics's AssessmentComponent/StudentMark) that separates
    # an auto-computed PASSED from FAILED.
    marks_pass_threshold_percent: float = 50.0
    # A teacher can only mark a session's attendance within this many
    # minutes of its scheduled start_time (approved rule) — admin is
    # exempt and can mark anytime, but must supply a note when marking
    # outside this window (see AttendanceService.mark_session).
    attendance_marking_window_minutes: int = 15

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()