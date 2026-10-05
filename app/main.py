from __future__ import annotations

import time
import uuid

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.academics.router import router as academics_router
from app.attendance.router import router as attendance_router
from app.audit.router import router as audit_router
from app.auth.permissions import Roles
from app.auth.redirects import home_for_roles
from app.auth.router import router as auth_router
from app.announcements.router import router as announcements_router
from app.applications.router import router as applications_router
from app.tasks.router import router as tasks_router
from app.gate.router import router as gate_router
from app.accounts.router import router as accounts_router
from app.purchasing.router import router as purchasing_router
from app.payroll.router import router as payroll_router
from app.academics.result_router import router as result_router
from app.assignments.router import router as assignments_router
from app.complaints.router import router as complaints_router
from app.core.config import settings
from app.core.database import query_counter,  check_db_connection
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.core import migration_guard
from app.core.redis import check_redis_connection
from app.core.security import decode_access_token
from app.employees.router import job_roles_router, router as employees_router
from app.fees.assignment_router import router as fee_assignments_router
from app.fees.account_router import router as fee_accounts_router
from app.fees.challan_router import router as fee_challans_router
from app.fees.dashboard_router import router as fee_dashboard_router
from app.fees.router import structures_router as fee_structures_router, types_router as fee_types_router
from app.fines.router import router as fines_router
from app.imports.router import router as imports_router
from app.library.dashboard_router import router as library_dashboard_router
from app.library.eresources_router import router as library_eresources_router
from app.library.issue_router import router as library_issue_router
from app.library.portal_router import router as library_portal_router
from app.library.request_router import router as library_requests_router
from app.library.router import router as library_books_router
from app.core.reference_data_router import router as reference_data_router
from app.notifications.router import router as notifications_router
from app.profiles.router import router as profiles_router
from app.programs.router import router as programs_router
from app.reports.router import router as reports_router
from app.students.router import router as students_router

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="VIST Learning Management System API — Phase A (foundation, auth, student CSV import).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)

# Compress HTML/JSON/CSS/JS responses (dashboard.css alone is ~29 KB): a large
# saving on mobile connections, and free for the server.
app.add_middleware(GZipMiddleware, minimum_size=600)


class CachedStaticFiles(StaticFiles):
    """Static assets are cached by the browser for 10 minutes, then served
    stale-while-revalidating for a day. Pages stop re-checking every CSS/JS
    file on every click (each check is a network round trip on a phone),
    while a new release still reaches users within minutes."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=86400"
        return response


app.mount("/static", CachedStaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Role cards shown on the landing page (Section 18 roles). Purely
# presentational — actual authorization is still enforced entirely by the
# API's RBAC dependencies, never by which page the person clicked into.
ROLE_CARDS = [
    {
        "slug": "admin",
        "code": Roles.ADMIN,
        "label": "Administrator",
        "description": "Full system access — manage students, programs, imports, and roles.",
    },
    {
        "slug": "teacher",
        "code": Roles.TEACHER,
        "label": "Teacher",
        "description": "Manage attendance, marks, and course materials for your assigned sections.",
    },
    {
        "slug": "student",
        "code": Roles.STUDENT,
        "label": "Student",
        "description": "View your profile, attendance, results, fees, and course materials.",
    },
    {
        "slug": "accounts_officer",
        "code": Roles.ACCOUNTS_OFFICER,
        "label": "Accounts Officer",
        "description": "Manage fee structures, student ledgers, and payments.",
    },
    {
        "slug": "library",
        "code": Roles.LIBRARIAN,
        "label": "Library",
        "description": "Library catalog and borrowing — module coming soon.",
    },
    {
        "slug": "hostel",
        "code": Roles.HOSTEL_WARDEN,
        "label": "Hostel",
        "description": "Hostel rooms and residents — module coming soon.",
    },
]
ROLE_CARDS_BY_SLUG = {r["slug"]: r for r in ROLE_CARDS}

# Drawer navigation for the admin area (Section 31 dashboard + whatever
# modules actually exist). `enabled=False` entries are modules with no
# backend built yet — shown so the admin knows they're planned, but
# rendered as inert (no href, no click) rather than a link to a page that
# doesn't exist. Update this list is the single place to add a new admin
# section as modules get built.
def _nav(slug, label, icon, href, *, group=None, mobile=False, short=None):
    """One menu entry. `group` puts it under a heading in the side menu;
    `mobile=True` also shows it in the bottom bar on phones (keep it to 4
    per portal -- a fifth "More" button opens the full menu)."""
    return {"slug": slug, "label": label, "icon": icon, "href": href, "enabled": True,
            "group": group, "mobile": mobile, "short": short}


# ---- Admin: grouped by what the person is doing, not by build order --------
ADMIN_NAV_ITEMS = [
    _nav("dashboard", "Dashboard", "dashboard", "/dashboard/admin", mobile=True, short="Home"),
    _nav("students", "Students", "students", "/dashboard/admin/students", group="People", mobile=True),
    _nav("employees", "Employees", "employees", "/dashboard/admin/employees", group="People"),
    _nav("programs", "Programs", "programs", "/dashboard/admin/programs", group="Academics"),
    _nav("courses", "Courses", "graduation-cap", "/dashboard/admin/courses", group="Academics", mobile=True),
    _nav("timetable", "Timetable", "clock", "/dashboard/admin/timetable", group="Academics"),
    _nav("attendance", "Attendance", "attendance", "/dashboard/admin/attendance", group="Academics", mobile=True),
    _nav("progress", "Teacher Progress", "exams", "/dashboard/admin/progress", group="Academics"),
    _nav("results", "Result Types", "exams", "/dashboard/admin/results", group="Academics"),
    _nav("result-sheets", "Result Approval", "tasks", "/dashboard/admin/result-sheets", group="Academics"),
    _nav("fees", "Fees", "fees", "/dashboard/admin/fees", group="Finance"),
    _nav("finance", "Accounts", "tasks", "/dashboard/admin/finance", group="Finance"),
    _nav("payroll", "Payroll", "fees", "/dashboard/admin/payroll", group="Finance"),
    _nav("fines", "Fines", "ban", "/dashboard/admin/fines", group="Finance"),
    _nav("library", "Library", "library", "/dashboard/library", group="Library"),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/admin/announcements", group="Support"),
    _nav("gate", "Gate", "shield", "/dashboard/admin/gate", group="Support"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/admin/tasks", group="Support"),
    _nav("applications", "Applications", "file", "/dashboard/admin/applications", group="Support"),
    _nav("complaints", "Complaints", "ban", "/dashboard/admin/complaints", group="Support"),
    _nav("imports", "CSV Imports", "imports", "/dashboard/admin/imports", group="System"),
    _nav("settings", "Settings", "settings", "/dashboard/admin/settings", group="System"),
]

# Library staff (ADMIN + LIBRARIAN). Student/teacher borrowing lives in their
# own portals (see STUDENT_NAV_ITEMS / FACULTY_NAV_ITEMS).
LIBRARY_NAV_ITEMS = [
    _nav("dashboard", "Dashboard", "dashboard", "/dashboard/library", mobile=True, short="Home"),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/library/announcements"),
    _nav("issue-return", "Issue & Return", "issue-return", "/dashboard/library/issue-return", group="Circulation", mobile=True, short="Issue"),
    _nav("requests", "Borrow Requests", "issue-return", "/dashboard/library/requests", group="Circulation"),
    _nav("overdue", "Overdue", "clock", "/dashboard/library/overdue", group="Circulation", mobile=True),
    _nav("catalogue", "Catalogue", "library", "/dashboard/library/catalogue", group="Collection", mobile=True),
    _nav("journals-search", "Journals & E-Resources", "library", "/dashboard/library/journals", group="Collection"),
    _nav("fines", "Fines", "fees", "/dashboard/library/fines", group="Finance"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/library/tasks", group="Services"),
    _nav("applications", "Applications", "file", "/dashboard/library/applications", group="Services"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/library/purchases", group="Services"),
    _nav("payslips", "My payslips", "file", "/dashboard/library/payslips", group="Services"),
]

# Accounts Officer's own portal.
GATE_NAV_ITEMS = [
    _nav("gate", "Gate", "shield", "/dashboard/gate", mobile=True),
    _nav("log", "Today's log", "clock", "/dashboard/gate/log", mobile=True),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/gate/announcements", mobile=True),
    _nav("tasks", "Tasks", "tasks", "/dashboard/gate/tasks", mobile=True),
    _nav("applications", "Applications", "file", "/dashboard/gate/applications"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/gate/purchases"),
    _nav("payslips", "My payslips", "file", "/dashboard/gate/payslips"),
]

EXAM_NAV_ITEMS = [
    _nav("results", "Result approval", "tasks", "/dashboard/exam", mobile=True),
    _nav("result-types", "Result types", "exams", "/dashboard/exam/result-types", mobile=True),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/exam/announcements", mobile=True),
    _nav("tasks", "Tasks", "tasks", "/dashboard/exam/tasks", mobile=True),
    _nav("applications", "Applications", "file", "/dashboard/exam/applications"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/exam/purchases"),
    _nav("payslips", "My payslips", "file", "/dashboard/exam/payslips"),
]

WARDEN_NAV_ITEMS = [
    _nav("announcements", "Announcements", "megaphone", "/dashboard/warden", mobile=True),
    _nav("tasks", "Tasks", "tasks", "/dashboard/warden/tasks", mobile=True),
    _nav("applications", "Applications", "file", "/dashboard/warden/applications", mobile=True),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/warden/purchases"),
    _nav("payslips", "My payslips", "file", "/dashboard/warden/payslips"),
]

ACCOUNTS_NAV_ITEMS = [
    _nav("announcements", "Announcements", "megaphone", "/dashboard/accounts/announcements", mobile=True),
    _nav("fees", "Fees", "fees", "/dashboard/accounts/fees", mobile=True),
    _nav("finance", "Accounts", "tasks", "/dashboard/accounts/finance", mobile=True),
    _nav("payroll", "Payroll", "fees", "/dashboard/accounts/payroll"),
    _nav("payslips", "My payslips", "file", "/dashboard/accounts/payslips"),
    _nav("fines", "Fines", "ban", "/dashboard/accounts/fines", mobile=True),
    _nav("tasks", "Tasks", "tasks", "/dashboard/accounts/tasks"),
    _nav("applications", "Applications", "file", "/dashboard/accounts/applications"),
]

# Student self-service. Home is a real overview; Library is one service among
# several (it used to BE the home page).
STUDENT_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/student", mobile=True),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/student/announcements"),
    _nav("assignments", "Assignments", "exams", "/dashboard/student/assignments", group="Study"),
    _nav("courses", "My Courses", "graduation-cap", "/dashboard/student/courses", group="Study", mobile=True, short="Courses"),
    _nav("timetable", "My Timetable", "clock", "/dashboard/student/timetable", group="Study", mobile=True, short="Timetable"),
    _nav("attendance", "My Attendance", "attendance", "/dashboard/student/attendance", group="Study", mobile=True, short="Attendance"),
    _nav("results", "My Results", "exams", "/dashboard/student/results", group="Study"),
    _nav("library", "Library", "library", "/dashboard/student/library", group="Services"),
    _nav("applications", "Applications", "file", "/dashboard/student/applications", group="Services"),
    _nav("fees", "My Fees", "fees", "/dashboard/student/fees", group="Services"),
    _nav("fines", "My Fines", "fees", "/dashboard/student/fines", group="Services"),
    _nav("complaints", "Feedback & Complaints", "ban", "/dashboard/student/complaints", group="Services"),
    _nav("profile", "My Profile", "user", "/dashboard/student/profile", group="Account"),
]

# Teacher self-service.
FACULTY_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/faculty", mobile=True),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/faculty/announcements"),
    _nav("assignments", "Assignments", "exams", "/dashboard/faculty/assignments", group="Teaching"),
    _nav("courses", "My Courses", "graduation-cap", "/dashboard/faculty/courses", group="Teaching", mobile=True, short="Courses"),
    _nav("attendance", "Attendance", "attendance", "/dashboard/faculty/attendance", group="Teaching", mobile=True),
    _nav("grading", "Grading", "exams", "/dashboard/faculty/grading", group="Teaching", mobile=True),
    _nav("timetable", "My Timetable", "clock", "/dashboard/faculty/timetable", group="Teaching"),
    _nav("library", "Library", "library", "/dashboard/faculty/library", group="Services"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/faculty/tasks", group="Services"),
    _nav("applications", "Applications", "file", "/dashboard/faculty/applications", group="Services"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/faculty/purchases", group="Services"),
    _nav("payslips", "My payslips", "file", "/dashboard/faculty/payslips", group="Services"),
    _nav("fines", "My Fines", "fees", "/dashboard/faculty/fines", group="Services"),
    _nav("complaints", "Feedback & Complaints", "ban", "/dashboard/faculty/complaints", group="Services"),
    _nav("profile", "My Profile", "user", "/dashboard/faculty/profile", group="Account"),
]


@app.middleware("http")
async def database_up_to_date_guard(request: Request, call_next):
    """If the database is behind the code (or is missing tables the code needs), answer with one clear instruction instead of a crash."""
    path = request.url.path
    if not (path.startswith("/health") or path.startswith("/static")):
        behind, missing = await migration_guard.is_behind()
        if behind:
            fix = migration_guard.FIX_COMMAND
            tables = migration_guard.kind() == "tables"
            if path.startswith("/api/"):
                message = migration_guard.TABLES_MESSAGE.format(what=", ".join(missing[:8]) + (" ..." if len(missing) > 8 else "")) if tables else \
                    f"The database needs to be updated before the system can be used. Ask whoever manages the server to run: {fix}"
                return JSONResponse(status_code=503, content={"error": {"code": "DATABASE_OUT_OF_DATE", "message": message, "details": {"pending": missing}}})
            return HTMLResponse(migration_guard.tables_page(missing) if tables else migration_guard.PAGE.format(fix=fix, missing=", ".join(missing)), status_code=503)
    return await call_next(request)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Attaches a request ID to every log line for this request and adds
    baseline security headers (Section 22/37)."""
    request_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    counter = {"n": 0}
    query_counter.set(counter)
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time"] = f"{elapsed_ms:.0f}ms"
    if elapsed_ms >= settings.slow_request_ms and not request.url.path.startswith("/static"):
        logger.warning("slow_request", method=request.method, path=request.url.path, ms=round(elapsed_ms), db_queries=counter["n"])
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    structlog.contextvars.clear_contextvars()
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Centralized error shape (Section 28). Raw exceptions/SQL/stack traces
    never reach the client — only the structured code + message."""
    logger.warning("app_error", code=exc.code, path=str(request.url.path), message=exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception", path=str(request.url.path), exc_info=exc)
    # A page that needs a table or column the database does not have is an installation problem, not a mystery: say so.
    import re as _re

    detail = str(getattr(exc, "orig", "") or exc)
    found = _re.search(r'(relation|column|type) "([^"]+)" (?:of relation "[^"]+" )?does not exist', detail)
    if found:
        return JSONResponse(status_code=503, content={"error": {"code": "DATABASE_OUT_OF_DATE", "details": {"missing": f"{found.group(1)} {found.group(2)}"},
                            "message": f"This part of the system needs something the database does not have yet ({found.group(1)} \u201c{found.group(2)}\u201d). The update files for the newest features were probably not installed. "
                                       f"Ask whoever manages the server to copy every file from the migrations folder into migrations/versions, then run: {migration_guard.FIX_COMMAND}"}})
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred."}},
    )


app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(audit_router, prefix=settings.api_v1_prefix)
app.include_router(academics_router, prefix=settings.api_v1_prefix)
app.include_router(attendance_router, prefix=settings.api_v1_prefix)
app.include_router(complaints_router, prefix=settings.api_v1_prefix)
app.include_router(announcements_router, prefix=settings.api_v1_prefix)
app.include_router(applications_router, prefix=settings.api_v1_prefix)
app.include_router(tasks_router, prefix=settings.api_v1_prefix)
app.include_router(gate_router, prefix=settings.api_v1_prefix)
app.include_router(accounts_router, prefix=settings.api_v1_prefix)
app.include_router(purchasing_router, prefix=settings.api_v1_prefix)
app.include_router(payroll_router, prefix=settings.api_v1_prefix)
app.include_router(result_router, prefix=settings.api_v1_prefix)
app.include_router(assignments_router, prefix=settings.api_v1_prefix)
app.include_router(students_router, prefix=settings.api_v1_prefix)
app.include_router(programs_router, prefix=settings.api_v1_prefix)
app.include_router(profiles_router, prefix=settings.api_v1_prefix)
app.include_router(reference_data_router, prefix=settings.api_v1_prefix)
app.include_router(notifications_router, prefix=settings.api_v1_prefix)
app.include_router(imports_router, prefix=settings.api_v1_prefix)
app.include_router(reports_router, prefix=settings.api_v1_prefix)
app.include_router(employees_router, prefix=settings.api_v1_prefix)
app.include_router(job_roles_router, prefix=settings.api_v1_prefix)
app.include_router(library_books_router, prefix=settings.api_v1_prefix)
app.include_router(library_issue_router, prefix=settings.api_v1_prefix)
app.include_router(library_dashboard_router, prefix=settings.api_v1_prefix)
app.include_router(library_requests_router, prefix=settings.api_v1_prefix)
app.include_router(library_portal_router, prefix=settings.api_v1_prefix)
app.include_router(library_eresources_router, prefix=settings.api_v1_prefix)
app.include_router(fee_types_router, prefix=settings.api_v1_prefix)
app.include_router(fee_structures_router, prefix=settings.api_v1_prefix)
app.include_router(fee_assignments_router, prefix=settings.api_v1_prefix)
app.include_router(fee_challans_router, prefix=settings.api_v1_prefix)
app.include_router(fee_accounts_router, prefix=settings.api_v1_prefix)
app.include_router(fee_dashboard_router, prefix=settings.api_v1_prefix)
app.include_router(fines_router, prefix=settings.api_v1_prefix)
# NOTE — deliberately NOT wired in: app.fees.webhook_router. It has no
# signature/HMAC verification (there's no real 1LINK shared secret to
# verify against yet), which makes it an open "mark any challan paid"
# endpoint if exposed. The current design is staff-driven instead —
# ADMIN/ACCOUNTS_OFFICER mark challans paid/unpaid directly via
# POST /fees/challans/{id}/mark-paid|mark-unpaid (see
# app/fees/challan_router.py). Re-wire this router only once real 1LINK
# credentials exist AND payment_webhook() has a signature check added.


@app.get("/health", tags=["health"], summary="Liveness — is the process up")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/health/live", tags=["health"], summary="Liveness probe")
async def health_live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"], summary="Readiness — dependencies reachable")
async def health_ready() -> JSONResponse:
    t0 = time.perf_counter()
    db_ok = await check_db_connection()
    db_ms = round((time.perf_counter() - t0) * 1000)
    t1 = time.perf_counter()
    redis_ok = await check_redis_connection()
    redis_ms = round((time.perf_counter() - t1) * 1000)
    ready = db_ok and redis_ok
    hints = []
    if db_ms > 200 or redis_ms > 200:
        hints.append(
            f"The database ({db_ms} ms) or Redis ({redis_ms} ms) answers slowly. On Windows, if DATABASE_URL / REDIS_URL say "
            "'localhost', try 127.0.0.1 instead: 'localhost' can try IPv6 first and wait before falling back."
        )
    behind, missing = await migration_guard.is_behind()
    ready = ready and not behind
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ok" if ready else "degraded", "database": db_ok, "redis": redis_ok,
                 "database_ms": db_ms, "redis_ms": redis_ms, "hints": hints,
                 "migrations": "behind" if behind else "current", "pending_migrations": missing,
                 "pdf_support": "installed" if __import__("importlib.util").util.find_spec("reportlab") else "MISSING - run: pip install reportlab"},
    )


@app.get("/", include_in_schema=False)
async def landing_page(request: Request, reauth: int = 0):
    """The one sign-in page for every role. Someone who already has a valid
    session is sent straight to their portal instead of being shown the form
    again. `?reauth=1` is used when a portal has just rejected the session, so
    the two pages can never bounce a person back and forth."""
    if not reauth:
        token = request.cookies.get("access_token")
        if token:
            try:
                claims = decode_access_token(token)
                destination = home_for_roles(claims.get("roles", []), bool(claims.get("must_change_password")))
                if destination:
                    return RedirectResponse(destination, status_code=302)
            except Exception:  # expired / tampered token -> just show the form
                pass
    response = templates.TemplateResponse(request, "login.html", {})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/change-password", include_in_schema=False)
async def forced_change_password_page(request: Request):
    """Role-agnostic — reachable regardless of which dashboard a user
    would otherwise land on, since must_change_password can be true for
    any role (Student, Employee via CSV import/manual creation/admin
    reset). Deliberately does not extend any dashboard base template —
    those all assume a fully-authenticated, password-already-changed
    session for their nav/data loading, which is exactly the state a
    user on this page is NOT yet in."""
    return templates.TemplateResponse(request, "change_password.html", {})


@app.get("/login/{role_slug}", include_in_schema=False)
async def legacy_role_login_page(role_slug: str):
    """Old per-role sign-in URLs (bookmarks, portal redirects) now lead to the
    single sign-in page. reauth=1 stops it re-forwarding a rejected session."""
    return RedirectResponse("/?reauth=1", status_code=302)


@app.get("/dashboard/admin", include_in_schema=False)
async def admin_dashboard_page(request: Request):
    """Auth/role checking happens client-side in admin-common.js against
    the real API (/auth/me + /reports/admin-dashboard) — this route just
    serves the page shell. Anyone without a valid ADMIN session gets
    redirected to /login/admin by the page's own JS before any data shows."""
    return templates.TemplateResponse(
        request, "admin/dashboard.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "dashboard"}
    )


@app.get("/dashboard/admin/students", include_in_schema=False)
async def admin_students_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/students.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "students"}
    )


@app.get("/dashboard/admin/programs", include_in_schema=False)
async def admin_programs_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/programs.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "programs"}
    )


@app.get("/dashboard/admin/courses", include_in_schema=False)
async def admin_courses_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/courses.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "courses"}
    )


@app.get("/dashboard/admin/timetable", include_in_schema=False)
async def admin_timetable_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/timetable.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "timetable"}
    )


@app.get("/dashboard/admin/attendance", include_in_schema=False)
async def admin_attendance_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/attendance.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "attendance"}
    )


@app.get("/dashboard/admin/progress", include_in_schema=False)
async def admin_progress_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/progress.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "progress"}
    )


@app.get("/dashboard/admin/results", include_in_schema=False)
async def admin_results_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/results.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "results"}
    )


@app.get("/dashboard/admin/courses/offering/{offering_id}", include_in_schema=False)
async def admin_offering_detail_page(request: Request, offering_id: str):
    """Replaces the old 'Materials' popup entirely — syllabus, term
    dates, timetable, and class sessions are sections on a real page
    now, not everything crammed into one dialog. offering_id is read
    client-side from the URL; the page itself has no server-side
    per-offering logic."""
    return templates.TemplateResponse(
        request, "admin/offering_detail.html",
        {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "courses", "offering_id": offering_id},
    )


@app.get("/dashboard/admin/imports", include_in_schema=False)
async def admin_imports_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/imports.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "imports"}
    )


@app.get("/dashboard/admin/employees", include_in_schema=False)
async def admin_employees_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/employees.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "employees"}
    )


@app.get("/dashboard/admin/settings", include_in_schema=False)
async def admin_settings_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/settings.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "settings"}
    )


@app.get("/dashboard/admin/fees", include_in_schema=False)
async def admin_fees_page(request: Request):
    """Also reachable by ACCOUNTS_OFFICER (see the page's own
    allowed_roles block override) — they see the dashboard, assignment
    list, and payment recording; fee type/structure creation and
    activation are hidden client-side (and blocked server-side
    regardless) since those require FEE_MANAGE, which is ADMIN-only."""
    return templates.TemplateResponse(
        request, "admin/fees.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "fees"}
    )


@app.get("/dashboard/admin/fines", include_in_schema=False)
async def admin_fines_page(request: Request):
    """Also reachable by ACCOUNTS_OFFICER (see the page's own
    allowed_roles block override) — mark-paid/waive controls are hidden
    client-side (and blocked server-side regardless) for anyone without
    fine.manage. LIBRARIAN uses the separate /dashboard/library/fines
    route below instead, which renders the exact same page/JS but with
    the Library nav drawer rather than the Admin one — a librarian
    seeing the full admin drawer (Students, Employees, Settings...) they
    have no access to was a real, confusing bug, not a style choice."""
    return templates.TemplateResponse(
        request, "admin/fines.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "fines"}
    )


@app.get("/dashboard/library/fines", include_in_schema=False)
async def library_fines_page(request: Request):
    """Same template and JS as /dashboard/admin/fines — the page itself
    already adapts its buttons to whatever permissions the logged-in
    user actually has (mark-paid/waive only for fine.manage holders).
    This route exists purely to give librarians the correctly-scoped
    Library nav drawer instead of the Admin one."""
    return templates.TemplateResponse(
        request, "admin/fines.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "fines"}
    )


@app.get("/dashboard/accounts/fees", include_in_schema=False)
async def accounts_fees_page(request: Request):
    """Accounts Officer's own dedicated portal — same content as
    /dashboard/admin/fees, correctly-scoped Accounts nav drawer instead
    of the Admin one. This is what Admin's "Accounts" nav link jumps
    into, the same doorway pattern as "Library"."""
    return templates.TemplateResponse(
        request, "admin/fees.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "fees", "admin_back": True}
    )


@app.get("/dashboard/accounts/fines", include_in_schema=False)
async def accounts_fines_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/fines.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "fines", "admin_back": True}
    )


@app.get("/dashboard/admin/complaints", include_in_schema=False)
async def admin_complaints_page(request: Request):
    """All complaints across every role, detail view, status
    management, and the per-person analytics/highlight view — admin
    (complaint.manage) only, no shared-page-with-different-nav pattern
    needed here since only ADMIN holds complaint.manage today."""
    return templates.TemplateResponse(
        request, "admin/complaints.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "complaints"}
    )


@app.get("/dashboard/library", include_in_schema=False)
async def library_dashboard_page(request: Request):
    return templates.TemplateResponse(
        request, "library/dashboard.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "dashboard"}
    )


@app.get("/dashboard/library/catalogue", include_in_schema=False)
async def library_catalogue_page(request: Request):
    return templates.TemplateResponse(
        request, "library/catalogue.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "catalogue"}
    )


@app.get("/dashboard/library/issue-return", include_in_schema=False)
async def library_issue_return_page(request: Request):
    return templates.TemplateResponse(
        request, "library/issue-return.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "issue-return"}
    )


# NOTE: there is deliberately no /dashboard/library/journals admin page.
# Per approved decision, journals have no local admin-managed catalogue
# at all — the "Journals" experience is entirely live search against
# PubMed/PMC/DOAJ (see app/library/eresources_router.py), surfaced only
# in the student/faculty self-service portal, not something a librarian
# curates or adds to.


@app.get("/dashboard/library/requests", include_in_schema=False)
async def library_requests_page(request: Request):
    return templates.TemplateResponse(
        request, "library/requests.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "requests"}
    )


@app.get("/dashboard/library/overdue", include_in_schema=False)
async def library_overdue_page(request: Request):
    return templates.TemplateResponse(
        request, "library/overdue.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "overdue"}
    )


@app.get("/dashboard/library/journals", include_in_schema=False)
async def library_journals_staff_page(request: Request):
    """Staff-side access to the same live PubMed/PMC/DOAJ search and
    curated E-Resources list the student/faculty portal has — a
    librarian helping someone at the desk shouldn't have to log in as
    a student to use it."""
    return templates.TemplateResponse(
        request, "library/journals-search.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "journals-search"}
    )


@app.get("/dashboard/student/announcements", include_in_schema=False)
async def student_announcements_page(request: Request):
    return templates.TemplateResponse(request, "student/announcements.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "announcements"})


@app.get("/dashboard/faculty/announcements", include_in_schema=False)
async def faculty_announcements_page(request: Request):
    return templates.TemplateResponse(request, "faculty/announcements.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "announcements"})


@app.get("/dashboard/library/announcements", include_in_schema=False)
async def library_announcements_page(request: Request):
    return templates.TemplateResponse(request, "library/announcements.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "announcements"})


@app.get("/dashboard/accounts/announcements", include_in_schema=False)
async def accounts_announcements_page(request: Request):
    return templates.TemplateResponse(request, "accounts/announcements.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "announcements"})


@app.get("/dashboard/admin/announcements", include_in_schema=False)
async def admin_announcements_page(request: Request):
    return templates.TemplateResponse(request, "admin/announcements.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "announcements"})


@app.get("/dashboard/student/assignments", include_in_schema=False)
async def student_assignments_page(request: Request):
    return templates.TemplateResponse(request, "student/assignments.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "assignments"})


@app.get("/dashboard/faculty/assignments", include_in_schema=False)
async def faculty_assignments_page(request: Request):
    return templates.TemplateResponse(request, "faculty/assignments.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "assignments"})


@app.get("/dashboard/student/applications", include_in_schema=False)
async def student_applications_page(request: Request):
    return templates.TemplateResponse(request, "student/applications.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "applications"})


@app.get("/dashboard/faculty/applications", include_in_schema=False)
async def faculty_applications_page(request: Request):
    return templates.TemplateResponse(request, "faculty/applications.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "applications"})


@app.get("/dashboard/library/applications", include_in_schema=False)
async def library_applications_page(request: Request):
    return templates.TemplateResponse(request, "library/applications.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "applications"})


@app.get("/dashboard/accounts/applications", include_in_schema=False)
async def accounts_applications_page(request: Request):
    return templates.TemplateResponse(request, "accounts/applications.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "applications"})


@app.get("/dashboard/admin/applications", include_in_schema=False)
async def admin_applications_page(request: Request):
    return templates.TemplateResponse(request, "admin/applications.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "applications"})


@app.get("/dashboard/faculty/tasks", include_in_schema=False)
async def faculty_tasks_page(request: Request):
    return templates.TemplateResponse(request, "faculty/tasks.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "tasks"})


@app.get("/dashboard/library/tasks", include_in_schema=False)
async def library_tasks_page(request: Request):
    return templates.TemplateResponse(request, "library/tasks.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "tasks"})


@app.get("/dashboard/accounts/tasks", include_in_schema=False)
async def accounts_tasks_page(request: Request):
    return templates.TemplateResponse(request, "accounts/tasks.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "tasks"})


@app.get("/dashboard/admin/tasks", include_in_schema=False)
async def admin_tasks_page(request: Request):
    return templates.TemplateResponse(request, "admin/tasks.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "tasks"})


@app.get("/dashboard/gate", include_in_schema=False)
async def gate_home_page(request: Request):
    return templates.TemplateResponse(request, "gate/home.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "gate"})


@app.get("/dashboard/gate/log", include_in_schema=False)
async def gate_log_page(request: Request):
    return templates.TemplateResponse(request, "gate/log.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "log"})


@app.get("/dashboard/gate/announcements", include_in_schema=False)
async def gate_announcements_page(request: Request):
    return templates.TemplateResponse(request, "gate/announcements.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "announcements"})


@app.get("/dashboard/gate/tasks", include_in_schema=False)
async def gate_tasks_page(request: Request):
    return templates.TemplateResponse(request, "gate/tasks.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "tasks"})


@app.get("/dashboard/gate/applications", include_in_schema=False)
async def gate_applications_page(request: Request):
    return templates.TemplateResponse(request, "gate/applications.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "applications"})


@app.get("/dashboard/admin/gate", include_in_schema=False)
async def admin_gate_page(request: Request):
    return templates.TemplateResponse(request, "admin/gate.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "gate"})


@app.get("/dashboard/admin/result-sheets", include_in_schema=False)
async def admin_result_sheets_page(request: Request):
    return templates.TemplateResponse(request, "admin/result_sheets.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "result-sheets"})


@app.get("/dashboard/student/fees", include_in_schema=False)
async def student_fees_page(request: Request):
    return templates.TemplateResponse(request, "student/fees.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "fees"})


@app.get("/dashboard/admin/finance", include_in_schema=False)
async def admin_finance_page(request: Request):
    return templates.TemplateResponse(request, "admin/finance.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "finance"})


@app.get("/dashboard/accounts/finance", include_in_schema=False)
async def accounts_finance_page(request: Request):
    return templates.TemplateResponse(request, "admin/finance.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "finance"})


@app.get("/dashboard/faculty/purchases", include_in_schema=False)
async def faculty_purchases_page(request: Request):
    return templates.TemplateResponse(request, "faculty/purchases.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "purchases"})


@app.get("/dashboard/library/purchases", include_in_schema=False)
async def library_purchases_page(request: Request):
    return templates.TemplateResponse(request, "library/purchases.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "purchases"})


@app.get("/dashboard/gate/purchases", include_in_schema=False)
async def gate_purchases_page(request: Request):
    return templates.TemplateResponse(request, "gate/purchases.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "purchases"})


@app.get("/dashboard/admin/payroll", include_in_schema=False)
async def admin_payroll_page(request: Request):
    return templates.TemplateResponse(request, "admin/payroll.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "payroll"})


@app.get("/dashboard/accounts/payroll", include_in_schema=False)
async def accounts_payroll_page(request: Request):
    return templates.TemplateResponse(request, "admin/payroll.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "payroll"})


@app.get("/dashboard/accounts/payslips", include_in_schema=False)
async def accounts_payslips_page(request: Request):
    return templates.TemplateResponse(request, "admin/payslips.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "payslips"})


@app.get("/dashboard/faculty/payslips", include_in_schema=False)
async def faculty_payslips_page(request: Request):
    return templates.TemplateResponse(request, "faculty/payslips.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "payslips"})


@app.get("/dashboard/library/payslips", include_in_schema=False)
async def library_payslips_page(request: Request):
    return templates.TemplateResponse(request, "library/payslips.html", {"nav_items": LIBRARY_NAV_ITEMS, "active_nav": "payslips"})


@app.get("/dashboard/gate/payslips", include_in_schema=False)
async def gate_payslips_page(request: Request):
    return templates.TemplateResponse(request, "gate/payslips.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "payslips"})


@app.get("/dashboard/exam", include_in_schema=False)
async def exam_home_page(request: Request):
    return templates.TemplateResponse(request, "admin/result_sheets.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "results"})


@app.get("/dashboard/exam/result-types", include_in_schema=False)
async def exam_result_types_page(request: Request):
    return templates.TemplateResponse(request, "admin/results.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "result-types"})


@app.get("/dashboard/exam/announcements", include_in_schema=False)
async def exam_announcements_page(request: Request):
    return templates.TemplateResponse(request, "exam/announcements.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "announcements"})


@app.get("/dashboard/exam/applications", include_in_schema=False)
async def exam_applications_page(request: Request):
    return templates.TemplateResponse(request, "exam/applications.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "applications"})


@app.get("/dashboard/exam/tasks", include_in_schema=False)
async def exam_tasks_page(request: Request):
    return templates.TemplateResponse(request, "exam/tasks.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "tasks"})


@app.get("/dashboard/exam/purchases", include_in_schema=False)
async def exam_purchases_page(request: Request):
    return templates.TemplateResponse(request, "exam/purchases.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "purchases"})


@app.get("/dashboard/exam/payslips", include_in_schema=False)
async def exam_payslips_page(request: Request):
    return templates.TemplateResponse(request, "exam/payslips.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "payslips"})


@app.get("/dashboard/warden", include_in_schema=False)
async def warden_home_page(request: Request):
    return templates.TemplateResponse(request, "warden/announcements.html", {"nav_items": WARDEN_NAV_ITEMS, "active_nav": "announcements"})


@app.get("/dashboard/warden/tasks", include_in_schema=False)
async def warden_tasks_page(request: Request):
    return templates.TemplateResponse(request, "warden/tasks.html", {"nav_items": WARDEN_NAV_ITEMS, "active_nav": "tasks"})


@app.get("/dashboard/warden/applications", include_in_schema=False)
async def warden_applications_page(request: Request):
    return templates.TemplateResponse(request, "warden/applications.html", {"nav_items": WARDEN_NAV_ITEMS, "active_nav": "applications"})


@app.get("/dashboard/warden/purchases", include_in_schema=False)
async def warden_purchases_page(request: Request):
    return templates.TemplateResponse(request, "warden/purchases.html", {"nav_items": WARDEN_NAV_ITEMS, "active_nav": "purchases"})


@app.get("/dashboard/warden/payslips", include_in_schema=False)
async def warden_payslips_page(request: Request):
    return templates.TemplateResponse(request, "warden/payslips.html", {"nav_items": WARDEN_NAV_ITEMS, "active_nav": "payslips"})


@app.get("/dashboard/student", include_in_schema=False)
async def student_portal_page(request: Request):
    """STUDENT-only self-service portal. Auth/role checking happens
    client-side the same way every other portal page does (admin-common.js
    against /auth/me), enforced for real server-side by each API
    endpoint's own RBAC dependency — this route just serves the shell."""
    return templates.TemplateResponse(
        request, "student/home.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "home"}
    )


@app.get("/dashboard/student/library", include_in_schema=False)
async def student_library_page(request: Request):
    return templates.TemplateResponse(
        request, "student/dashboard.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "library"}
    )


@app.get("/dashboard/student/fines", include_in_schema=False)
async def student_fines_page(request: Request):
    """Standalone page, not a Library tab — a fine isn't inherently a
    library concept (it can be disciplinary), so it gets its own nav
    item and its own screen instead of being nested somewhere that
    doesn't describe it."""
    return templates.TemplateResponse(
        request, "student/fines.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "fines"}
    )


@app.get("/dashboard/faculty", include_in_schema=False)
async def faculty_portal_page(request: Request):
    """TEACHER-only self-service portal (see student_portal_page above for
    the same auth-checking pattern)."""
    return templates.TemplateResponse(
        request, "faculty/home.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "home"}
    )


@app.get("/dashboard/faculty/library", include_in_schema=False)
async def faculty_library_page(request: Request):
    return templates.TemplateResponse(
        request, "faculty/dashboard.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "library"}
    )


@app.get("/dashboard/faculty/fines", include_in_schema=False)
async def faculty_fines_page(request: Request):
    """See student_fines_page above for why this is separate from the
    Library tabs."""
    return templates.TemplateResponse(
        request, "faculty/fines.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "fines"}
    )


@app.get("/dashboard/student/complaints", include_in_schema=False)
async def student_complaints_page(request: Request):
    return templates.TemplateResponse(
        request, "student/complaints.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "complaints"}
    )


@app.get("/dashboard/faculty/complaints", include_in_schema=False)
async def faculty_complaints_page(request: Request):
    return templates.TemplateResponse(
        request, "faculty/complaints.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "complaints"}
    )


@app.get("/dashboard/student/profile", include_in_schema=False)
async def student_profile_page(request: Request):
    return templates.TemplateResponse(
        request, "student/profile.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "profile"}
    )


@app.get("/dashboard/faculty/profile", include_in_schema=False)
async def faculty_profile_page(request: Request):
    return templates.TemplateResponse(
        request, "faculty/profile.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "profile"}
    )


@app.get("/dashboard/student/courses", include_in_schema=False)
async def student_courses_page(request: Request):
    return templates.TemplateResponse(
        request, "student/courses.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "courses"}
    )


@app.get("/dashboard/student/timetable", include_in_schema=False)
async def student_timetable_page(request: Request):
    return templates.TemplateResponse(
        request, "student/timetable.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "timetable"}
    )


@app.get("/dashboard/student/attendance", include_in_schema=False)
async def student_attendance_page(request: Request):
    return templates.TemplateResponse(
        request, "student/attendance.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "attendance"}
    )


@app.get("/dashboard/student/results", include_in_schema=False)
async def student_results_page(request: Request):
    return templates.TemplateResponse(
        request, "student/results.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "results"}
    )


@app.get("/dashboard/faculty/courses", include_in_schema=False)
async def faculty_courses_page(request: Request):
    return templates.TemplateResponse(
        request, "faculty/courses.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "courses"}
    )


@app.get("/dashboard/faculty/timetable", include_in_schema=False)
async def faculty_timetable_page(request: Request):
    return templates.TemplateResponse(
        request, "faculty/timetable.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "timetable"}
    )


@app.get("/dashboard/faculty/attendance", include_in_schema=False)
async def faculty_attendance_page(request: Request):
    return templates.TemplateResponse(
        request, "faculty/attendance.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "attendance"}
    )


@app.get("/dashboard/faculty/grading", include_in_schema=False)
async def faculty_grading_page(request: Request):
    return templates.TemplateResponse(
        request, "faculty/grading.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "grading"}
    )