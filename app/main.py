from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.templating import Jinja2Templates

from app.admissions.router import router as admissions_router
from app.fees.refund_router import router as refund_router
from app.inventory.router import router as inventory_router
from app.assets.router import router as assets_router
from app.approvals.router import router as approvals_router
from app.branding.router import public_router as branding_public_router
from app.branding.router import router as branding_router
from app.supplementary.router import router as supplementary_router
from app.sms.router import public_router as sms_public_router, router as sms_router
from app.exams.router import router as exams_router
from app.policy.router import router as policy_router
from app.notifications.email_router import me_router as email_me_router, router as email_settings_router, status_router as system_status_router
from app.parents.router import router as parents_router
from app.academics.router import router as academics_router
from app.attendance.router import router as attendance_router
from app.audit.activity import router as activity_router
from app.audit.router import router as audit_router
from app.auth.permissions import Roles
from app.auth.redirects import home_for_roles, profile_for_roles
from app.auth.router import router as auth_router
from app.auth.view_as import READ_ONLY_ALLOWED, read_only_reason
from app.auth.view_as import router as view_as_router
from app.selfservice.router import router as profile_changes_router
from app.performance.router import router as performance_router
from app.announcements.router import router as announcements_router
from app.applications.router import router as applications_router
from app.tasks.router import router as tasks_router
from app.gate.router import router as gate_router
from app.timekeeping.router import router as timekeeping_router
from app.accounts.router import router as accounts_router
from app.purchasing.router import router as purchasing_router
from app.payroll.router import router as payroll_router
from app.academics.result_router import router as result_router
from app.assignments.router import router as assignments_router
from app.complaints.router import router as complaints_router
from app.core.config import settings
from app.core.database import check_db_connection, get_db, query_counter
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

if settings.is_production:
    _problems = settings.production_problems()
    if _problems:
        raise SystemExit("VIST LMS will not start in production until these settings are fixed in .env:\n  - " + "\n  - ".join(_problems))

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="VIST Learning Management System API.",
    # The API map is a convenience for developers, and a map for attackers on a live server.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

from app.core.duplicate_guard import DuplicateSubmissionGuard  # noqa: E402
from app.core.background import start_web_sender  # noqa: E402

start_web_sender(app)       # sends queued SMS / emails when the 'sms' service is not running (app/core/background.py)

app.add_middleware(DuplicateSubmissionGuard)
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
# `brand` in every template: the institute's current name, logo, colour... (Settings -> Institute).
from app.core.brand import BrandView  # noqa: E402

templates.env.globals["brand"] = BrandView()
from app.core.releases import current_version  # noqa: E402

templates.env.globals["app_version"] = current_version


def ui_lang(request) -> str:
    """"ur" or "en", from the vist_lang cookie (set by the language button and at sign-in from the account)."""
    try:
        return "ur" if request.cookies.get("vist_lang") == "ur" else "en"
    except Exception:  # noqa: BLE001
        return "en"


templates.env.globals["ui_lang"] = ui_lang

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
        "description": "Library catalogue, borrowing, returns and fines.",
    },
    {
        "slug": "hostel",
        "code": Roles.HOSTEL_WARDEN,
        "label": "Hostel",
        "description": "Hostel rooms, residents and the monthly hostel charge.",
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
    _nav("admissions", "Admissions", "imports", "/dashboard/admin/admissions", group="People"),
    _nav("employees", "Employees", "employees", "/dashboard/admin/employees", group="People"),
    _nav("hostel", "Hostel", "students", "/dashboard/admin/hostel", group="People"),
    _nav("profile-changes", "Profile changes", "user", "/dashboard/admin/profile-changes", group="People"),
    _nav("certificates", "Certificate requests", "file", "/dashboard/admin/certificates", group="People"),
    _nav("warnings", "Warnings", "alert", "/dashboard/admin/warnings", group="People"),
    _nav("programs", "Programs", "programs", "/dashboard/admin/programs", group="Academics"),
    _nav("courses", "Courses", "graduation-cap", "/dashboard/admin/courses", group="Academics", mobile=True),
    _nav("timetable", "Timetable", "clock", "/dashboard/admin/timetable", group="Academics"),
    _nav("attendance", "Attendance", "attendance", "/dashboard/admin/attendance", group="Academics", mobile=True),
    _nav("progress", "Teacher Progress", "exams", "/dashboard/admin/progress", group="Academics"),
    _nav("feedback", "Course feedback", "check-circle", "/dashboard/admin/feedback", group="Academics"),
    _nav("overview", "Institute Overview", "dashboard", "/dashboard/admin/overview", group="Academics"),
    _nav("results", "Result Types", "exams", "/dashboard/admin/results", group="Academics"),
    _nav("result-sheets", "Result Approval", "tasks", "/dashboard/admin/result-sheets", group="Academics"),
    _nav("datesheets", "Datesheets", "clock", "/dashboard/admin/datesheets", group="Academics"),
    _nav("supplementary", "Supplementary Exams", "exams", "/dashboard/admin/supplementary", group="Academics"),
    _nav("calendar", "Calendar", "clock", "/dashboard/admin/calendar", group="Academics"),
    _nav("fees", "Fees", "fees", "/dashboard/admin/fees", group="Finance"),
    _nav("refunds", "Refunds", "fees", "/dashboard/admin/refunds", group="Finance"),
    _nav("inventory", "Inventory / Store", "tasks", "/dashboard/admin/inventory", group="Finance"),
    _nav("assets", "Fixed assets", "tasks", "/dashboard/admin/assets", group="Finance"),
    _nav("approvals", "Approvals", "tasks", "/dashboard/admin/approvals", group="Finance"),
    _nav("finance", "Accounts", "tasks", "/dashboard/admin/finance", group="Finance"),
    _nav("reports", "Reports", "exams", "/dashboard/admin/reports", group="Reports"),
    _nav("monthly", "Monthly report", "exams", "/dashboard/admin/monthly", group="Reports"),
    _nav("finance-reports", "Finance reports", "exams", "/dashboard/admin/finance-reports", group="Finance"),
    _nav("onebill", "1Bill payments", "fees", "/dashboard/admin/onebill", group="Finance"),
    _nav("payroll", "Payroll", "fees", "/dashboard/admin/payroll", group="Finance"),
    _nav("staff-attendance", "Staff attendance", "clock", "/dashboard/admin/staff-attendance", group="Finance"),
    _nav("fines", "Fines", "ban", "/dashboard/admin/fines", group="Finance"),
    _nav("library", "Library", "library", "/dashboard/library", group="Library"),
    # Every other office's panel, opened with the administrator's own login (like Library), and "view as" a person.
    _nav("panel-accounts", "Accounts office", "fees", "/dashboard/accounts", group="Panels"),
    _nav("panel-exam", "Exam department", "exams", "/dashboard/exam/home", group="Panels"),
    _nav("panel-principal", "Principal", "dashboard", "/dashboard/principal", group="Panels"),
    _nav("panel-gate", "Gate", "shield", "/dashboard/gate/home", group="Panels"),
    _nav("panel-warden", "Hostel warden", "hostel", "/dashboard/warden/home", group="Panels"),
    _nav("panel-staff", "General staff", "employees", "/dashboard/staff", group="Panels"),
    _nav("view-as", "View as a person", "user", "/dashboard/admin/view-as", group="Panels"),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/admin/announcements", group="Support"),
    _nav("top-bars", "Top bars", "megaphone", "/dashboard/admin/top-bars", group="Support"),
    _nav("gate", "Gate visitors", "shield", "/dashboard/admin/gate", group="Support"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/admin/tasks", group="Support"),
    _nav("applications", "Applications", "file", "/dashboard/admin/applications", group="Support"),
    _nav("complaints", "Complaints", "ban", "/dashboard/admin/complaints", group="Support"),
    _nav("imports", "CSV Imports", "imports", "/dashboard/admin/imports", group="System"),
    _nav("sms", "SMS & WhatsApp", "megaphone", "/dashboard/admin/sms", group="System"),
    _nav("activity", "Activity log", "clock", "/dashboard/admin/activity", group="System"),
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
    _nav("payslips", "My pay & attendance", "file", "/dashboard/library/payslips", group="Services"),
    _nav("calendar", "Calendar", "clock", "/dashboard/library/calendar", group="Services"),
    _nav("profile", "My Profile", "user", "/dashboard/library/profile", group="Services"),
    _nav("my-warnings", "My warnings", "alert", "/dashboard/library/my-warnings", group="Services"),
]

# Accounts Officer's own portal.
GATE_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/gate/home", mobile=True, short="Home"),
    _nav("gate", "Visitors", "shield", "/dashboard/gate", mobile=True),
    _nav("log", "Visitor log book", "file", "/dashboard/gate/log", mobile=True, short="Log book"),
    _nav("staff", "Staff in / out", "clock", "/dashboard/gate/staff", mobile=True, short="Staff"),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/gate/announcements"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/gate/tasks"),
    _nav("applications", "Applications", "file", "/dashboard/gate/applications"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/gate/purchases"),
    _nav("payslips", "My pay & attendance", "file", "/dashboard/gate/payslips"),
    _nav("calendar", "Calendar", "clock", "/dashboard/gate/calendar"),
    _nav("profile", "My Profile", "user", "/dashboard/gate/profile"),
    _nav("my-warnings", "My warnings", "alert", "/dashboard/gate/my-warnings"),
]

# General staff (office boy, IT, driver, cleaner...): only their own things.
STAFF_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/staff", mobile=True, short="Home"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/staff/tasks", mobile=True),
    _nav("payslips", "My pay & attendance", "file", "/dashboard/staff/payslips", mobile=True, short="Pay"),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/staff/announcements", mobile=True, short="News"),
    _nav("applications", "Applications", "file", "/dashboard/staff/applications", group="Requests"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/staff/purchases", group="Requests"),
    _nav("complaints", "Feedback & Complaints", "ban", "/dashboard/staff/complaints", group="Requests"),
    _nav("calendar", "Calendar", "clock", "/dashboard/staff/calendar", group="Account"),
    _nav("profile", "My Profile", "user", "/dashboard/staff/profile", group="Account"),
    _nav("my-warnings", "My warnings", "alert", "/dashboard/staff/my-warnings", group="Account"),
]

PARENT_NAV_ITEMS = [
    _nav("children", "My children", "students", "/dashboard/parent", mobile=True, short="Children"),
    _nav("fees", "Fees", "fees", "/dashboard/parent/fees", mobile=True, short="Fees"),
    _nav("marks", "Marks", "exams", "/dashboard/parent/marks", mobile=True, short="Marks"),
    _nav("timetable", "Timetable & exams", "clock", "/dashboard/parent/timetable"),
    _nav("notices", "Notices", "megaphone", "/dashboard/parent/notices"),
    _nav("calendar", "Calendar", "clock", "/dashboard/parent/calendar"),
    _nav("account", "My account", "settings", "/dashboard/parent/account", mobile=True, short="Account"),
]

PRINCIPAL_NAV_ITEMS = [
    _nav("overview", "Institute overview", "dashboard", "/dashboard/principal", mobile=True, short="Home"),
    _nav("progress", "Teacher progress", "exams", "/dashboard/principal/progress", mobile=True, short="Progress"),
    _nav("feedback", "Course feedback", "check-circle", "/dashboard/principal/feedback"),
    _nav("students", "Student lookup", "students", "/dashboard/principal/students", mobile=True, short="Students"),
    _nav("monthly", "Monthly report", "exams", "/dashboard/principal/monthly"),
    _nav("warnings", "Warnings", "alert", "/dashboard/principal/warnings"),
    _nav("reports", "Reports", "exams", "/dashboard/principal/reports"),
    _nav("staff-attendance", "Staff attendance", "clock", "/dashboard/principal/staff-attendance"),
    _nav("calendar", "Calendar", "clock", "/dashboard/principal/calendar"),
    _nav("hostel", "Hostel", "students", "/dashboard/principal/hostel"),
    _nav("assets", "Fixed assets", "tasks", "/dashboard/principal/assets"),
    _nav("activity", "Activity log", "clock", "/dashboard/principal/activity"),
]

HOD_NAV_ITEMS = [
    _nav("attendance", "Attendance oversight", "attendance", "/dashboard/hod", mobile=True, short="Attendance"),
    _nav("progress", "Teacher progress", "exams", "/dashboard/hod/progress", mobile=True, short="Progress"),
    _nav("feedback", "Course feedback", "check-circle", "/dashboard/hod/feedback"),
    _nav("students", "Student lookup", "students", "/dashboard/hod/students", mobile=True, short="Students"),
    _nav("reports", "Reports", "exams", "/dashboard/hod/reports"),
    _nav("calendar", "Calendar", "clock", "/dashboard/hod/calendar"),
]

EXAM_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/exam/home", mobile=True, short="Home"),
    _nav("results", "Result approval", "tasks", "/dashboard/exam", mobile=True, short="Results"),
    _nav("result-types", "Result types", "exams", "/dashboard/exam/result-types"),
    _nav("datesheets", "Datesheets & admit cards", "clock", "/dashboard/exam/datesheets"),
    _nav("supplementary", "Supplementary exams", "exams", "/dashboard/exam/supplementary"),
    _nav("students", "Student lookup", "students", "/dashboard/exam/students"),
    _nav("reports", "Reports", "exams", "/dashboard/exam/reports"),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/exam/announcements", mobile=True, short="News"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/exam/tasks", mobile=True),
    _nav("applications", "Applications", "file", "/dashboard/exam/applications"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/exam/purchases"),
    _nav("payslips", "My pay & attendance", "file", "/dashboard/exam/payslips"),
    _nav("calendar", "Calendar", "clock", "/dashboard/exam/calendar"),
    _nav("profile", "My Profile", "user", "/dashboard/exam/profile"),
    _nav("my-warnings", "My warnings", "alert", "/dashboard/exam/my-warnings"),
]

WARDEN_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/warden/home", mobile=True, short="Home"),
    _nav("hostel", "Hostel", "students", "/dashboard/warden/hostel", mobile=True),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/warden", mobile=True, short="News"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/warden/tasks", mobile=True),
    _nav("applications", "Applications", "file", "/dashboard/warden/applications"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/warden/purchases"),
    _nav("payslips", "My pay & attendance", "file", "/dashboard/warden/payslips"),
    _nav("calendar", "Calendar", "clock", "/dashboard/warden/calendar"),
    _nav("profile", "My Profile", "user", "/dashboard/warden/profile"),
    _nav("my-warnings", "My warnings", "alert", "/dashboard/warden/my-warnings"),
]

ACCOUNTS_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/accounts", mobile=True, short="Home"),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/accounts/announcements"),
    _nav("fees", "Fees", "fees", "/dashboard/accounts/fees", mobile=True),
    _nav("finance", "Accounts", "tasks", "/dashboard/accounts/finance", mobile=True),
    _nav("reports", "Reports", "exams", "/dashboard/accounts/reports", mobile=False),
    _nav("finance-reports", "Finance reports", "exams", "/dashboard/accounts/finance-reports"),
    _nav("refunds", "Refunds", "fees", "/dashboard/accounts/refunds"),
    _nav("onebill", "1Bill payments", "fees", "/dashboard/accounts/onebill"),
    _nav("inventory", "Inventory / Store", "tasks", "/dashboard/accounts/inventory"),
    _nav("assets", "Fixed assets", "tasks", "/dashboard/accounts/assets"),
    _nav("approvals", "My approval requests", "tasks", "/dashboard/accounts/approvals"),
    _nav("payroll", "Payroll", "fees", "/dashboard/accounts/payroll"),
    _nav("payslips", "My pay & attendance", "file", "/dashboard/accounts/payslips"),
    _nav("fines", "Fines", "ban", "/dashboard/accounts/fines", mobile=True),
    _nav("tasks", "Tasks", "tasks", "/dashboard/accounts/tasks"),
    _nav("applications", "Applications", "file", "/dashboard/accounts/applications"),
    _nav("calendar", "Calendar", "clock", "/dashboard/accounts/calendar"),
    _nav("profile", "My Profile", "user", "/dashboard/accounts/profile"),
    _nav("my-warnings", "My warnings", "alert", "/dashboard/accounts/my-warnings"),
]

# Student self-service. Home is a real overview; Library is one service among
# several (it used to BE the home page).
STUDENT_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/student", mobile=True),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/student/announcements"),
    _nav("assignments", "Assignments", "exams", "/dashboard/student/assignments", group="Study"),
    _nav("courses", "My Courses", "graduation-cap", "/dashboard/student/courses", group="Study"),
    _nav("timetable", "My Timetable", "clock", "/dashboard/student/timetable", group="Study", mobile=True, short="Timetable"),
    _nav("attendance", "My Attendance", "attendance", "/dashboard/student/attendance", group="Study", mobile=True, short="Attendance"),
    _nav("exams", "My Exams", "clock", "/dashboard/student/exams", group="Study"),
    _nav("results", "My Results", "exams", "/dashboard/student/results", group="Study", mobile=True, short="Results"),
    _nav("feedback", "Course feedback", "check-circle", "/dashboard/student/feedback", group="Study"),
    _nav("records", "Degree progress & documents", "graduation-cap", "/dashboard/student/records", group="Study"),
    _nav("library", "Library", "library", "/dashboard/student/library", group="Services"),
    _nav("applications", "Applications", "file", "/dashboard/student/applications", group="Services"),
    _nav("certificates", "Certificates", "file", "/dashboard/student/certificates", group="Services"),
    _nav("fees", "My Fees", "fees", "/dashboard/student/fees", group="Services"),
    _nav("fines", "My Fines", "fees", "/dashboard/student/fines", group="Services"),
    _nav("complaints", "Feedback & Complaints", "ban", "/dashboard/student/complaints", group="Services"),
    _nav("profile", "My Profile", "user", "/dashboard/student/profile", group="Account"),
    _nav("my-warnings", "My warnings", "alert", "/dashboard/student/my-warnings", group="Account"),
    _nav("calendar", "Calendar", "clock", "/dashboard/student/calendar", group="Services"),
]

# Teacher self-service.
FACULTY_NAV_ITEMS = [
    _nav("home", "Home", "dashboard", "/dashboard/faculty", mobile=True),
    _nav("announcements", "Announcements", "megaphone", "/dashboard/faculty/announcements"),
    _nav("assignments", "Assignments", "exams", "/dashboard/faculty/assignments", group="Teaching"),
    _nav("courses", "My Courses", "graduation-cap", "/dashboard/faculty/courses", group="Teaching", mobile=True, short="Courses"),
    _nav("attendance", "Attendance", "attendance", "/dashboard/faculty/attendance", group="Teaching", mobile=True),
    _nav("grading", "Grading", "exams", "/dashboard/faculty/grading", group="Teaching", mobile=True),
    _nav("watch", "Students to watch", "students", "/dashboard/faculty/watch", group="Teaching"),
    _nav("feedback", "Student feedback", "check-circle", "/dashboard/faculty/feedback", group="Teaching"),
    _nav("timetable", "My Timetable", "clock", "/dashboard/faculty/timetable", group="Teaching"),
    _nav("exams", "Exam duties", "exams", "/dashboard/faculty/exams", group="Teaching"),
    _nav("library", "Library", "library", "/dashboard/faculty/library", group="Services"),
    _nav("tasks", "Tasks", "tasks", "/dashboard/faculty/tasks", group="Services"),
    _nav("applications", "Applications", "file", "/dashboard/faculty/applications", group="Services"),
    _nav("purchases", "Purchase requests", "tasks", "/dashboard/faculty/purchases", group="Services"),
    _nav("payslips", "My pay & attendance", "file", "/dashboard/faculty/payslips", group="Services"),
    _nav("fines", "My Fines", "fees", "/dashboard/faculty/fines", group="Services"),
    _nav("complaints", "Feedback & Complaints", "ban", "/dashboard/faculty/complaints", group="Services"),
    _nav("profile", "My Profile", "user", "/dashboard/faculty/profile", group="Account"),
    _nav("my-warnings", "My warnings", "alert", "/dashboard/faculty/my-warnings", group="Account"),
    _nav("calendar", "Calendar", "clock", "/dashboard/faculty/calendar", group="Services"),
]


@app.middleware("http")
async def policy_refresh(request: Request, call_next):
    """Keeps this worker's copy of the administrator-editable policy and institute profile at most a minute old."""
    if not request.url.path.startswith("/static/"):
        from app.policy.service import refresh_if_stale

        await refresh_if_stale()
    return await call_next(request)


@app.middleware("http")
async def view_as_is_read_only(request: Request, call_next):
    """While an administrator is viewing someone's screens ("view as"), nothing can be changed: every request that could
    change data is refused here, before it reaches any route. Leaving the view, refreshing and signing out still work."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path not in READ_ONLY_ALLOWED:
        viewer = read_only_reason(request.cookies.get("access_token"))
        if viewer:
            return JSONResponse(status_code=403, content={"error": {"code": "READ_ONLY_VIEW", "details": None, "message":
                "You are viewing this person's screens read-only. Exit the view to make changes in your own account."}})
    return await call_next(request)


# Someone who is not an administrator but opens an /dashboard/admin/... page that their own portal also has (e.g. the
# accounts office following a link to /dashboard/admin/fees) is sent to their portal's copy, so they see their own
# menu instead of the administration's. Pages only the administration has are left to the page's own role check.
# Only where the portal copy is the SAME screen (same template) with the portal's own menu: e.g. a teacher's
# /dashboard/faculty/attendance is a different screen from the administration's, so it is not a redirect target.
_SAME_SCREEN = {"ACCOUNTS_OFFICER": ("accounts", {"fees", "fines", "refunds", "inventory", "assets", "payroll", "finance", "finance-reports",
                                                 "approvals", "staff-attendance"})}
_page_routes: set[str] | None = None


@app.middleware("http")
async def own_portal_redirect(request: Request, call_next):
    global _page_routes
    path = request.url.path
    if request.method == "GET" and path.startswith("/dashboard/admin/") and request.cookies.get("access_token"):
        try:
            from app.core.security import decode_access_token

            roles = decode_access_token(request.cookies["access_token"]).get("roles") or []
        except Exception:  # noqa: BLE001 - an expired or bad token is the page's business, not ours
            roles = []
        if roles and "ADMIN" not in roles:
            if _page_routes is None:
                _page_routes = {r.path for r in app.routes if getattr(r, "path", "").startswith("/dashboard/") and "GET" in (getattr(r, "methods", None) or set())}
            rest = path[len("/dashboard/admin/"):]
            for role in roles:
                portal, pages = _SAME_SCREEN.get(role, (None, set()))
                target = f"/dashboard/{portal}/{rest}" if portal and rest in pages else None
                if target and target in _page_routes:
                    from fastapi.responses import RedirectResponse

                    return RedirectResponse(target + (("?" + request.url.query) if request.url.query else ""), status_code=307)
    return await call_next(request)


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
    request.state.request_id = request_id
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    counter = {"n": 0}
    query_counter.set(counter)
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not settings.is_production:  # timing and query counts help developers, but tell outsiders too much
        response.headers["X-Response-Time"] = f"{elapsed_ms:.0f}ms"
        response.headers["X-DB-Queries"] = str(counter["n"])
    if elapsed_ms >= settings.slow_request_ms and not request.url.path.startswith("/static"):
        logger.warning("slow_request", method=request.method, path=request.url.path, ms=round(elapsed_ms), db_queries=counter["n"])
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Only this site's own scripts run: injected <script> or on*= handlers are refused by the browser.
    # (Inline style attributes are allowed: the screens set widths and colours that way.)
    if not request.url.path.startswith(("/docs", "/redoc")):          # the API docs page loads its viewer from a CDN (development only)
        response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    structlog.contextvars.clear_contextvars()
    return response


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
       "connect-src 'self'; frame-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Centralized error shape (Section 28). Raw exceptions/SQL/stack traces
    never reach the client — only the structured code + message."""
    logger.warning("app_error", code=exc.code, path=str(request.url.path), message=exc.message)
    _flag_security_event(request, exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


# Refusals that are part of normal use (finish setting up your account first) are not security events.
_ROUTINE_REFUSALS = ("You must change your password", "Verify your email address")


def _flag_security_event(request: Request, exc: AppError) -> None:
    """Mark a refused action or a forged sign-in token; app.security.middleware records it once the answer is sent."""
    from app.core.exceptions import AuthenticationError, AuthorizationError

    try:
        if isinstance(exc, AuthorizationError) and not exc.message.startswith(_ROUTINE_REFUSALS):
            request.state.security_event = ("ACCESS_DENIED", exc.message[:300])
        elif isinstance(exc, AuthenticationError) and exc.message == "Invalid session.":
            request.state.security_event = ("BAD_TOKEN", "The sign-in token was not one this server issued (forged or altered)")
    except Exception:  # noqa: BLE001
        pass


def _wants_page(request: Request) -> bool:
    """A person opening an address in the browser (not the API, files or health checks): show a page, not JSON."""
    path = request.scope.get("path", "")
    if request.scope.get("method") not in ("GET", "HEAD") or path.startswith(("/api/", "/static/", "/health", "/media/", "/uploads/")):
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept or (accept in ("", "*/*") and path.startswith("/dashboard"))


def error_page(request: Request, status_code: int):
    """The friendly "Page not found" / "Something went wrong" screen (templates/error.html)."""
    response = templates.TemplateResponse(request, "error.html", {
        "status_code": status_code, "path": request.url.path[:200],
        "request_id": getattr(request.state, "request_id", None) or request.headers.get("x-request-id"),
    }, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    """Unknown addresses opened in a browser get a real "Page not found" page instead of {"detail":"Not Found"}.
    The API and every other status keep their JSON answers exactly as before."""
    if exc.status_code == 404 and _wants_page(request):
        return error_page(request, 404)
    return await default_http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception", path=str(request.url.path), exc_info=exc)
    from app.core.alerts import alert_unhandled

    alert_unhandled(exc, path=str(request.url.path), request_id=request.headers.get("x-request-id") or getattr(request.state, "request_id", None))
    # A page that needs a table or column the database does not have is an installation problem, not a mystery: say so.
    import re as _re

    detail = str(getattr(exc, "orig", "") or exc)
    found = _re.search(r'(relation|column|type) "([^"]+)" (?:of relation "[^"]+" )?does not exist', detail)
    if found and settings.is_production:
        return JSONResponse(status_code=503, content={"error": {"code": "DATABASE_OUT_OF_DATE",
                            "message": "This part of the system needs a database update that has not been installed yet. Ask whoever manages the server to run: "
                                       + migration_guard.FIX_COMMAND}})
    if found:
        return JSONResponse(status_code=503, content={"error": {"code": "DATABASE_OUT_OF_DATE", "details": {"missing": f"{found.group(1)} {found.group(2)}"},
                            "message": f"This part of the system needs something the database does not have yet ({found.group(1)} \u201c{found.group(2)}\u201d). The update files for the newest features were probably not installed. "
                                       f"Ask whoever manages the server to copy every file from the migrations folder into migrations/versions, then run: {migration_guard.FIX_COMMAND}"}})
    if _wants_page(request):
        return error_page(request, 500)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred."}},
    )


app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(policy_router, prefix=settings.api_v1_prefix)
app.include_router(email_settings_router, prefix=settings.api_v1_prefix)
app.include_router(email_me_router, prefix=settings.api_v1_prefix)
app.include_router(system_status_router, prefix=settings.api_v1_prefix)
from app.core.release_router import router as release_router  # noqa: E402

app.include_router(release_router, prefix=settings.api_v1_prefix)
from app.feedback.router import router as feedback_router  # noqa: E402

app.include_router(feedback_router, prefix=settings.api_v1_prefix)
from app.fees.onebill_router import report_router as onebill_report_router, router as onebill_router  # noqa: E402

app.include_router(onebill_router, prefix=settings.api_v1_prefix)
app.include_router(onebill_report_router, prefix=settings.api_v1_prefix)
app.include_router(exams_router, prefix=settings.api_v1_prefix)
app.include_router(sms_router, prefix=settings.api_v1_prefix)
app.include_router(sms_public_router)                          # /wa/d/<token>: the document sent with a WhatsApp message
from app.reports.finance_router import router as finance_reports_router  # noqa: E402
from app.reports.center_router import router as report_center_router  # noqa: E402
from app.reports.monthly_router import router as monthly_report_router  # noqa: E402
from app.discipline.router import router as discipline_router  # noqa: E402

app.include_router(report_center_router, prefix=settings.api_v1_prefix)
app.include_router(monthly_report_router, prefix=settings.api_v1_prefix)
app.include_router(discipline_router, prefix=settings.api_v1_prefix)

app.include_router(finance_reports_router, prefix=settings.api_v1_prefix)
from app.calendar.router import router as calendar_router  # noqa: E402

app.include_router(calendar_router, prefix=settings.api_v1_prefix)
from app.records.router import public_router as records_public_router, router as records_router  # noqa: E402

app.include_router(records_router, prefix=settings.api_v1_prefix)
app.include_router(records_public_router, prefix=settings.api_v1_prefix)
from app.hostel.router import router as hostel_router  # noqa: E402

app.include_router(hostel_router, prefix=settings.api_v1_prefix)
app.include_router(supplementary_router, prefix=settings.api_v1_prefix)
app.include_router(refund_router, prefix=settings.api_v1_prefix)
app.include_router(inventory_router, prefix=settings.api_v1_prefix)
app.include_router(assets_router, prefix=settings.api_v1_prefix)
app.include_router(approvals_router, prefix=settings.api_v1_prefix)
app.include_router(branding_router, prefix=settings.api_v1_prefix)
app.include_router(branding_public_router)
app.include_router(view_as_router, prefix=settings.api_v1_prefix)
app.include_router(activity_router, prefix=settings.api_v1_prefix)
app.include_router(profile_changes_router, prefix=settings.api_v1_prefix)
app.include_router(performance_router, prefix=settings.api_v1_prefix)
app.include_router(admissions_router, prefix=settings.api_v1_prefix)
app.include_router(parents_router, prefix=settings.api_v1_prefix)
app.include_router(audit_router, prefix=settings.api_v1_prefix)
app.include_router(academics_router, prefix=settings.api_v1_prefix)
app.include_router(attendance_router, prefix=settings.api_v1_prefix)
app.include_router(complaints_router, prefix=settings.api_v1_prefix)
app.include_router(announcements_router, prefix=settings.api_v1_prefix)
app.include_router(applications_router, prefix=settings.api_v1_prefix)
app.include_router(tasks_router, prefix=settings.api_v1_prefix)
app.include_router(gate_router, prefix=settings.api_v1_prefix)
app.include_router(timekeeping_router, prefix=settings.api_v1_prefix)
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
from app.security.router import router as security_router  # noqa: E402

app.include_router(security_router, prefix=settings.api_v1_prefix)
from app.todos.router import router as todos_router  # noqa: E402

app.include_router(todos_router, prefix=settings.api_v1_prefix)
from app.teaching.router import router as teaching_router  # noqa: E402

app.include_router(teaching_router, prefix=settings.api_v1_prefix)
from app.banners.router import router as banners_router  # noqa: E402

app.include_router(banners_router, prefix=settings.api_v1_prefix)
from app.push.router import router as push_router  # noqa: E402
from app.push.app_shell import router as app_shell_router  # noqa: E402

app.include_router(push_router, prefix=settings.api_v1_prefix)
app.include_router(app_shell_router)            # /manifest.webmanifest, /sw.js, /branding/app-icon-*.png
# NOTE — deliberately NOT wired in: app.fees.webhook_router. It has no
# signature/HMAC verification (there's no real 1LINK shared secret to
# verify against yet), which makes it an open "mark any challan paid"
# endpoint if exposed. The current design is staff-driven instead —
# ADMIN/ACCOUNTS_OFFICER mark challans paid/unpaid directly via
# POST /fees/challans/{id}/mark-paid|mark-unpaid (see
# app/fees/challan_router.py). Re-wire this router only once real 1LINK
# credentials exist AND payment_webhook() has a signature check added.


@app.get("/health", tags=["health"], summary="Liveness — is the process up (a browser gets the status page)")
async def health(request: Request):
    # Monitors, Docker and scripts get the same tiny JSON as always; a person opening it in a browser gets a page.
    if "text/html" not in request.headers.get("accept", ""):
        return {"status": "ok"}
    db_ok = await check_db_connection()
    redis_ok = await check_redis_connection()
    from app.core.releases import version_info

    from app.core import brand
    from app.core.share import meta as share_meta

    info = version_info()
    share = share_meta(request, kind="version", title=f"{brand.short_name()} — version {info['version']}",
                       description="See what's new in this version.", path=f"/health?v={info['version']}")
    response = templates.TemplateResponse(request, "health.html", {"db_ok": db_ok, "redis_ok": redis_ok, "info": info, "share": share})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/share/preview.png", include_in_schema=False)
async def share_preview(kind: str = "portal", v: str | None = None):
    """The picture shown when the portal's or the "what's new" address is shared (WhatsApp, Facebook, email...)."""
    from fastapi.responses import Response as _R

    from app.core.releases import current_version
    from app.core.share import preview_png

    body = preview_png("version" if kind == "version" else "portal", current_version(), str(settings.brand_version))
    return _R(content=body, media_type="image/png", headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})


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
    if settings.is_production:  # outsiders only learn up / not up
        return JSONResponse(status_code=200 if ready else 503, content={"status": "ok" if ready else "degraded"})
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
    from urllib.parse import urlparse

    host = urlparse(settings.public_url or "").hostname or request.url.hostname   # behind a proxy, the address people actually use
    from app.core import brand
    from app.core.share import meta as share_meta

    share = share_meta(request, kind="portal", title=f"{brand.short_name()} — student & staff portal",
                       description=f"Sign in to {brand.name()}: classes, attendance, results, fees and more.", path="/")
    response = templates.TemplateResponse(request, "login.html", {"site_host": host, "share": share})
    response.headers["Cache-Control"] = "no-store"
    return response


_CALENDAR_PAGES = {
    "admin": ("admin/_base.html", "ADMIN_NAV_ITEMS", "ADMIN"), "accounts": ("accounts/_base.html", "ACCOUNTS_NAV_ITEMS", None),
    "library": ("library/_base.html", "LIBRARY_NAV_ITEMS", None), "gate": ("gate/_base.html", "GATE_NAV_ITEMS", None),
    "exam": ("exam/_base.html", "EXAM_NAV_ITEMS", None), "warden": ("warden/_base.html", "WARDEN_NAV_ITEMS", None),
    "faculty": ("faculty/_base.html", "FACULTY_NAV_ITEMS", None), "student": ("student/_base.html", "STUDENT_NAV_ITEMS", None),
    "parent": ("parent/_base.html", "PARENT_NAV_ITEMS", None), "principal": ("admin/_base.html", "PRINCIPAL_NAV_ITEMS", "ADMIN,PRINCIPAL"),
    "hod": ("admin/_base.html", "HOD_NAV_ITEMS", "ADMIN,HOD"), "staff": ("staff/_base.html", "STAFF_NAV_ITEMS", None),
}


@app.get("/dashboard/{portal}/calendar", include_in_schema=False)
async def calendar_page(portal: str, request: Request):
    if portal not in _CALENDAR_PAGES:
        from fastapi import HTTPException

        raise HTTPException(status_code=404)
    base, nav, roles = _CALENDAR_PAGES[portal]
    return templates.TemplateResponse(request, "shared/calendar.html", {"base_template": base, "nav_items": globals()[nav], "active_nav": "calendar",
                                                                       "allowed_roles": roles})


@app.get("/verify/{code}", include_in_schema=False)
async def verify_certificate_page(code: str, request: Request, db=Depends(get_db)):
    """Public: anyone holding a certificate or ID card can check it is genuine (the QR code points here)."""
    from app.records.service import RecordsService

    result = await RecordsService(db).verify(code[:20])
    response = templates.TemplateResponse(request, "verify.html", {"result": result, "code": code[:20], "app_name": settings.app_name},
                                          status_code=200 if result else 404)
    response.headers["Cache-Control"] = "no-store"
    return response


_REPORT_PAGES = {
    "admin": ("admin/_base.html", "ADMIN_NAV_ITEMS", "ADMIN"), "accounts": ("accounts/_base.html", "ACCOUNTS_NAV_ITEMS", "ADMIN,ACCOUNTS_OFFICER"),
    "principal": ("admin/_base.html", "PRINCIPAL_NAV_ITEMS", "ADMIN,PRINCIPAL"), "hod": ("admin/_base.html", "HOD_NAV_ITEMS", "ADMIN,HOD"),
    "exam": ("exam/_base.html", "EXAM_NAV_ITEMS", "ADMIN,EXAM_CONTROLLER"),
}


@app.get("/dashboard/{portal}/reports", include_in_schema=False)
async def reports_page(portal: str, request: Request):
    """One Reports page for every office; each person sees only the reports their permissions allow (checked again by the API)."""
    if portal not in _REPORT_PAGES:
        raise HTTPException(status_code=404)
    base, nav, roles = _REPORT_PAGES[portal]
    return templates.TemplateResponse(request, "shared/reports.html", {"base_template": base, "nav_items": globals()[nav], "active_nav": "reports",
                                                                      "allowed_roles": roles, "app_name": settings.institute_name})


@app.get("/dashboard/admin/approvals", include_in_schema=False)
async def approvals_page_admin(request: Request):
    return templates.TemplateResponse(request, "admin/approvals.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "approvals", "allowed_roles": "ADMIN"})


@app.get("/dashboard/accounts/approvals", include_in_schema=False)
async def approvals_page_accounts(request: Request):
    return templates.TemplateResponse(request, "admin/approvals.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "approvals",
                                                                       "allowed_roles": "ADMIN,ACCOUNTS_OFFICER"})


_ASSET_PAGES = {"admin": ("ADMIN_NAV_ITEMS", "ADMIN"), "accounts": ("ACCOUNTS_NAV_ITEMS", "ADMIN,ACCOUNTS_OFFICER"),
                "principal": ("PRINCIPAL_NAV_ITEMS", "ADMIN,PRINCIPAL")}


@app.get("/dashboard/assets/tag", include_in_schema=False)
async def asset_label_scan(request: Request):
    """Where a scanned asset label (QR) lands: sends the person to the fixed-asset page of their own portal, keeping #tag=..."""
    return HTMLResponse("""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Asset</title>
<p>Opening the asset&hellip;</p>
<script src="/static/js/asset-scan.js"></script>""")


@app.get("/dashboard/{portal}/assets", include_in_schema=False)
async def assets_page(portal: str, request: Request):
    if portal not in _ASSET_PAGES:
        raise HTTPException(status_code=404)
    nav, roles = _ASSET_PAGES[portal]
    return templates.TemplateResponse(request, "admin/assets.html", {"nav_items": globals()[nav], "active_nav": "assets", "allowed_roles": roles,
                                                                     "app_name": settings.institute_name})


@app.get("/dashboard/student/records", include_in_schema=False)
async def student_records_page(request: Request):
    return templates.TemplateResponse(request, "student/records.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "records"})


_HOSTEL_PAGES = {"admin": ("admin/_base.html", "ADMIN_NAV_ITEMS", "ADMIN"), "warden": ("warden/_base.html", "WARDEN_NAV_ITEMS", None),
                 "principal": ("admin/_base.html", "PRINCIPAL_NAV_ITEMS", "ADMIN,PRINCIPAL")}


@app.get("/dashboard/{portal}/hostel", include_in_schema=False)
async def hostel_page(portal: str, request: Request):
    if portal not in _HOSTEL_PAGES:
        raise HTTPException(status_code=404)
    base, nav, roles = _HOSTEL_PAGES[portal]
    return templates.TemplateResponse(request, "shared/hostel.html", {"base_template": base, "nav_items": globals()[nav], "active_nav": "hostel",
                                                                     "allowed_roles": roles})


@app.get("/forgot-password", include_in_schema=False)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {})


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


@app.get("/verify-email", include_in_schema=False)
async def verify_email_page(request: Request):
    """Prove an email address with a one-time code: required before using any portal when the administrator turns it on
    (Settings -> Email), and the place to change one's email later. Like /change-password it stands alone, outside the
    portal pages, because those need an account that is allowed in."""
    response = templates.TemplateResponse(request, "verify_email.html", {})
    response.headers["Cache-Control"] = "no-store"
    return response


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


@app.get("/dashboard/admin/admissions", include_in_schema=False)
async def admin_admissions_page(request: Request):
    return templates.TemplateResponse(request, "admin/admissions.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "admissions"})


@app.get("/dashboard/admin/refunds", include_in_schema=False)
async def admin_refunds_page(request: Request):
    return templates.TemplateResponse(request, "admin/refunds.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "refunds"})


@app.get("/dashboard/accounts/onebill", include_in_schema=False)
async def accounts_onebill_page(request: Request):
    return templates.TemplateResponse(request, "shared/onebill.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "onebill", "allowed_roles": "ADMIN,ACCOUNTS_OFFICER"})


@app.get("/dashboard/admin/onebill", include_in_schema=False)
async def admin_onebill_page(request: Request):
    return templates.TemplateResponse(request, "shared/onebill.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "onebill", "allowed_roles": "ADMIN"})


@app.get("/dashboard/accounts/refunds", include_in_schema=False)
async def accounts_refunds_page(request: Request):
    return templates.TemplateResponse(request, "admin/refunds.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "refunds"})


@app.get("/dashboard/admin/inventory", include_in_schema=False)
async def admin_inventory_page(request: Request):
    return templates.TemplateResponse(request, "admin/inventory.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "inventory"})


@app.get("/dashboard/accounts/inventory", include_in_schema=False)
async def accounts_inventory_page(request: Request):
    return templates.TemplateResponse(request, "admin/inventory.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "inventory"})


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


@app.get("/dashboard/gate/home", include_in_schema=False)
async def gate_dashboard_page(request: Request):
    """The gate's dashboard: who is inside, today's visitors and staff in / out."""
    return templates.TemplateResponse(request, "gate/dashboard.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "home"})


@app.get("/dashboard/gate", include_in_schema=False)
async def gate_home_page(request: Request):
    return templates.TemplateResponse(request, "gate/home.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "gate"})


@app.get("/dashboard/gate/log", include_in_schema=False)
async def gate_log_page(request: Request):
    return templates.TemplateResponse(request, "gate/log.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "log"})


@app.get("/dashboard/gate/staff", include_in_schema=False)
async def gate_staff_page(request: Request):
    return templates.TemplateResponse(request, "gate/staff.html", {"nav_items": GATE_NAV_ITEMS, "active_nav": "staff"})


@app.get("/dashboard/admin/staff-attendance", include_in_schema=False)
async def admin_staff_attendance_page(request: Request):
    return templates.TemplateResponse(request, "admin/staff_attendance.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "staff-attendance"})


@app.get("/dashboard/accounts/staff-attendance", include_in_schema=False)
async def accounts_staff_attendance_page(request: Request):
    """The accounts office no longer sees staff attendance (old bookmarks land on payroll)."""
    return RedirectResponse("/dashboard/accounts/payroll", status_code=302)


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


@app.get("/dashboard/admin/finance-reports", include_in_schema=False)
async def admin_finance_reports_page(request: Request):
    return templates.TemplateResponse(request, "admin/finance_reports.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "finance-reports"})


@app.get("/dashboard/accounts/finance-reports", include_in_schema=False)
async def accounts_finance_reports_page(request: Request):
    return templates.TemplateResponse(request, "admin/finance_reports.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "finance-reports"})


@app.get("/dashboard/admin/finance", include_in_schema=False)
async def admin_finance_page(request: Request):
    return templates.TemplateResponse(request, "admin/finance.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "finance"})


@app.get("/dashboard/accounts", include_in_schema=False)
async def accounts_home_page(request: Request):
    """The accounts office's dashboard: today's collection, the month's money, what is waiting."""
    return templates.TemplateResponse(request, "accounts/home.html", {"nav_items": ACCOUNTS_NAV_ITEMS, "active_nav": "home"})


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


@app.get("/dashboard/parent", include_in_schema=False)
async def parent_home_page(request: Request):
    return templates.TemplateResponse(request, "parent/home.html", {"nav_items": PARENT_NAV_ITEMS, "active_nav": "children"})


@app.get("/dashboard/parent/account", include_in_schema=False)
async def parent_account_page(request: Request):
    return templates.TemplateResponse(request, "parent/account.html", {"nav_items": PARENT_NAV_ITEMS, "active_nav": "account"})


# One real route per page, so notification links to them are recognised (app/notifications/service._known_page).
@app.get("/dashboard/parent/fees", include_in_schema=False)
async def parent_fees_page(request: Request):
    return templates.TemplateResponse(request, "parent/fees.html", {"nav_items": PARENT_NAV_ITEMS, "active_nav": "fees"})


@app.get("/dashboard/parent/timetable", include_in_schema=False)
async def parent_timetable_page(request: Request):
    return templates.TemplateResponse(request, "parent/timetable.html", {"nav_items": PARENT_NAV_ITEMS, "active_nav": "timetable"})


@app.get("/dashboard/parent/marks", include_in_schema=False)
async def parent_marks_page(request: Request):
    return templates.TemplateResponse(request, "parent/marks.html", {"nav_items": PARENT_NAV_ITEMS, "active_nav": "marks"})


@app.get("/dashboard/parent/notices", include_in_schema=False)
async def parent_notices_page(request: Request):
    return templates.TemplateResponse(request, "parent/notices.html", {"nav_items": PARENT_NAV_ITEMS, "active_nav": "notices"})


@app.get("/dashboard/principal", include_in_schema=False)
async def principal_overview_page(request: Request):
    return templates.TemplateResponse(request, "principal/overview.html", {"nav_items": PRINCIPAL_NAV_ITEMS, "active_nav": "overview"})


@app.get("/dashboard/principal/progress", include_in_schema=False)
async def principal_progress_page(request: Request):
    return templates.TemplateResponse(request, "admin/progress.html", {"nav_items": PRINCIPAL_NAV_ITEMS, "active_nav": "progress",
                                                                        "allowed_roles": "ADMIN,PRINCIPAL"})


def _lookup(request: Request, nav, roles: str):
    return templates.TemplateResponse(request, "shared/student_lookup.html", {"nav_items": nav, "active_nav": "students", "allowed_roles": roles})


@app.get("/dashboard/principal/students", include_in_schema=False)
async def principal_students_page(request: Request):
    return _lookup(request, PRINCIPAL_NAV_ITEMS, "ADMIN,PRINCIPAL")


@app.get("/dashboard/hod/students", include_in_schema=False)
async def hod_students_page(request: Request):
    return _lookup(request, HOD_NAV_ITEMS, "ADMIN,HOD")


@app.get("/dashboard/exam/students", include_in_schema=False)
async def exam_students_page(request: Request):
    return _lookup(request, EXAM_NAV_ITEMS, "ADMIN,EXAM_CONTROLLER")


@app.get("/dashboard/principal/staff-attendance", include_in_schema=False)
async def principal_staff_attendance_page(request: Request):
    return templates.TemplateResponse(request, "admin/staff_attendance.html", {"nav_items": PRINCIPAL_NAV_ITEMS, "active_nav": "staff-attendance",
                                                                                "allowed_roles": "ADMIN,PRINCIPAL"})


@app.get("/dashboard/admin/overview", include_in_schema=False)
async def admin_overview_page(request: Request):
    return templates.TemplateResponse(request, "principal/overview.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "overview"})


@app.get("/dashboard/hod", include_in_schema=False)
async def hod_attendance_page(request: Request):
    # The admin Attendance page without the admin-only tabs (marking, locking, holidays).
    return templates.TemplateResponse(request, "admin/attendance.html", {"nav_items": HOD_NAV_ITEMS, "active_nav": "attendance",
                                                                          "allowed_roles": "ADMIN,HOD", "hod_view": True})


@app.get("/dashboard/hod/progress", include_in_schema=False)
async def hod_progress_page(request: Request):
    return templates.TemplateResponse(request, "admin/progress.html", {"nav_items": HOD_NAV_ITEMS, "active_nav": "progress",
                                                                        "allowed_roles": "ADMIN,HOD"})


@app.get("/dashboard/exam/home", include_in_schema=False)
async def exam_dashboard_page(request: Request):
    """The exam department's dashboard: result sheets by step, who to chase, datesheets."""
    return templates.TemplateResponse(request, "exam/dashboard.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "home"})


@app.get("/dashboard/exam", include_in_schema=False)
async def exam_home_page(request: Request):
    return templates.TemplateResponse(request, "admin/result_sheets.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "results"})


@app.get("/dashboard/exam/datesheets", include_in_schema=False)
async def exam_datesheets_page(request: Request):
    return templates.TemplateResponse(request, "admin/datesheets.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "datesheets"})


@app.get("/dashboard/admin/view-as", include_in_schema=False)
async def admin_view_as_page(request: Request):
    return templates.TemplateResponse(request, "admin/view_as.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "view-as"})


@app.get("/dashboard/admin/profile-changes", include_in_schema=False)
async def admin_profile_changes_page(request: Request):
    return templates.TemplateResponse(request, "admin/profile_changes.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "profile-changes"})


@app.get("/dashboard/admin/top-bars", include_in_schema=False)
async def admin_top_bars_page(request: Request):
    return templates.TemplateResponse(request, "admin/top_bars.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "top-bars"})


@app.get("/dashboard/admin/activity", include_in_schema=False)
async def admin_activity_page(request: Request):
    return templates.TemplateResponse(request, "shared/activity.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "activity", "portal": "admin"})


@app.get("/dashboard/principal/activity", include_in_schema=False)
async def principal_activity_page(request: Request):
    return templates.TemplateResponse(request, "shared/activity.html", {"nav_items": PRINCIPAL_NAV_ITEMS, "active_nav": "activity", "portal": "principal"})


@app.get("/dashboard/admin/sms", include_in_schema=False)
async def admin_sms_page(request: Request):
    return templates.TemplateResponse(request, "admin/sms.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "sms"})


@app.get("/dashboard/exam/supplementary", include_in_schema=False)
async def exam_supplementary_page(request: Request):
    return templates.TemplateResponse(request, "admin/supplementary.html", {"nav_items": EXAM_NAV_ITEMS, "active_nav": "supplementary"})


@app.get("/dashboard/admin/supplementary", include_in_schema=False)
async def admin_supplementary_page(request: Request):
    return templates.TemplateResponse(request, "admin/supplementary.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "supplementary"})


@app.get("/dashboard/admin/datesheets", include_in_schema=False)
async def admin_datesheets_page(request: Request):
    return templates.TemplateResponse(request, "admin/datesheets.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "datesheets"})


@app.get("/dashboard/faculty/exams", include_in_schema=False)
async def faculty_exams_page(request: Request):
    return templates.TemplateResponse(request, "faculty/exams.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "exams"})


@app.get("/dashboard/student/exams", include_in_schema=False)
async def student_exams_page(request: Request):
    return templates.TemplateResponse(request, "student/exams.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "exams"})


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


@app.get("/dashboard/warden/home", include_in_schema=False)
async def warden_dashboard_page(request: Request):
    """The hostel warden's dashboard: rooms, beds, residents and unpaid hostel charges."""
    return templates.TemplateResponse(request, "warden/dashboard.html", {"nav_items": WARDEN_NAV_ITEMS, "active_nav": "home"})


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


_PROFILE_PAGES = {"accounts": ("accounts/_base.html", "ACCOUNTS_NAV_ITEMS"), "library": ("library/_base.html", "LIBRARY_NAV_ITEMS"),
                  "exam": ("exam/_base.html", "EXAM_NAV_ITEMS"), "gate": ("gate/_base.html", "GATE_NAV_ITEMS"),
                  "warden": ("warden/_base.html", "WARDEN_NAV_ITEMS"), "staff": ("staff/_base.html", "STAFF_NAV_ITEMS")}


def _office_profile_page(portal: str):
    base, nav = _PROFILE_PAGES[portal]

    async def page(request: Request):
        """My Profile for an office portal or general staff (same page as faculty): details, photo and change requests."""
        return templates.TemplateResponse(request, "shared/staff_profile.html", {"base_template": base, "nav_items": globals()[nav], "active_nav": "profile"})
    return page


# One real route per portal (not "/dashboard/{portal}/profile"), so notification links and the page checks see each page.
for _portal in _PROFILE_PAGES:
    app.add_api_route(f"/dashboard/{_portal}/profile", _office_profile_page(_portal), methods=["GET"], include_in_schema=False)


_STAFF_PAGES = {"tasks": "tasks", "payslips": "payslips", "announcements": "announcements", "applications": "applications",
                "purchases": "purchases", "complaints": "complaints"}


@app.get("/dashboard/my-profile", include_in_schema=False)
async def my_profile_redirect(request: Request):
    """Sends anyone to the My Profile page of their own portal (used by profile-change notifications)."""
    try:
        roles = decode_access_token(request.cookies.get("access_token") or "").get("roles", [])
    except Exception:
        return RedirectResponse("/?reauth=1", status_code=302)
    return RedirectResponse(profile_for_roles(roles) or "/", status_code=302)


@app.get("/dashboard/staff", include_in_schema=False)
async def staff_home_page(request: Request):
    """General staff (office boy, IT, driver, cleaner...): today's attendance, tasks, requests and notices."""
    return templates.TemplateResponse(request, "staff/home.html", {"nav_items": STAFF_NAV_ITEMS, "active_nav": "home"})


def _staff_page(page: str):
    async def view(request: Request):
        return templates.TemplateResponse(request, f"staff/{_STAFF_PAGES[page]}.html", {"nav_items": STAFF_NAV_ITEMS, "active_nav": page})
    return view


for _page in _STAFF_PAGES:
    app.add_api_route(f"/dashboard/staff/{_page}", _staff_page(_page), methods=["GET"], include_in_schema=False)


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


@app.get("/dashboard/student/feedback", include_in_schema=False)
async def student_feedback_page(request: Request):
    return templates.TemplateResponse(request, "student/feedback.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "feedback"})


@app.get("/dashboard/faculty/feedback", include_in_schema=False)
async def faculty_feedback_page(request: Request):
    return templates.TemplateResponse(request, "faculty/feedback.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "feedback"})


@app.get("/dashboard/{portal}/feedback", include_in_schema=False)
async def oversight_feedback_page(request: Request, portal: str):
    nav, roles = {"admin": (ADMIN_NAV_ITEMS, "ADMIN"), "principal": (PRINCIPAL_NAV_ITEMS, "ADMIN,PRINCIPAL"),
                  "hod": (HOD_NAV_ITEMS, "ADMIN,HOD")}.get(portal, (None, None))
    if nav is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "shared/feedback_overview.html", {"nav_items": nav, "active_nav": "feedback", "allowed_roles": roles,
                                                                                 "can_edit_questions": portal == "admin"})


@app.get("/dashboard/student/certificates", include_in_schema=False)
async def student_certificates_page(request: Request):
    return templates.TemplateResponse(request, "student/certificates.html", {"nav_items": STUDENT_NAV_ITEMS, "active_nav": "certificates"})


@app.get("/dashboard/admin/certificates", include_in_schema=False)
async def admin_certificates_page(request: Request):
    return templates.TemplateResponse(request, "admin/certificates.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "certificates"})


@app.get("/dashboard/admin/monthly", include_in_schema=False)
async def admin_monthly_page(request: Request):
    return templates.TemplateResponse(request, "shared/monthly_report.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "monthly"})


@app.get("/dashboard/principal/monthly", include_in_schema=False)
async def principal_monthly_page(request: Request):
    return templates.TemplateResponse(request, "shared/monthly_report.html", {"nav_items": PRINCIPAL_NAV_ITEMS, "active_nav": "monthly"})


@app.get("/dashboard/admin/warnings", include_in_schema=False)
async def admin_warnings_page(request: Request):
    return templates.TemplateResponse(request, "shared/warnings.html", {"nav_items": ADMIN_NAV_ITEMS, "active_nav": "warnings", "can_issue": True,
                                                                         "allowed_roles": "ADMIN"})


@app.get("/dashboard/principal/warnings", include_in_schema=False)
async def principal_warnings_page(request: Request):
    return templates.TemplateResponse(request, "shared/warnings.html", {"nav_items": PRINCIPAL_NAV_ITEMS, "active_nav": "warnings", "can_issue": False,
                                                                         "allowed_roles": "ADMIN,PRINCIPAL"})


def _my_warnings_page(portal: str):
    async def view(request: Request):
        base, nav, _roles = _CALENDAR_PAGES[portal]
        return templates.TemplateResponse(request, "shared/my_warnings.html", {"base_template": base, "nav_items": globals()[nav], "active_nav": "my-warnings",
                                                                               "allowed_roles": _roles})
    return view


# "My warnings" in every portal a student or employee lands on (explicit paths, so notification links can be checked)
for _portal in ("student", "faculty", "accounts", "library", "exam", "warden", "gate", "staff", "principal", "hod"):
    app.add_api_route(f"/dashboard/{_portal}/my-warnings", _my_warnings_page(_portal), methods=["GET"], include_in_schema=False)


@app.get("/dashboard/my-warnings", include_in_schema=False)
async def my_warnings_redirect(request: Request):
    """Sends anyone to the My warnings page of their own portal (used by warning notifications)."""
    try:
        roles = decode_access_token(request.cookies.get("access_token") or "").get("roles", [])
    except Exception:
        return RedirectResponse("/?reauth=1", status_code=302)
    home = profile_for_roles(roles)
    if home:
        return RedirectResponse(home.rsplit("/", 1)[0] + "/my-warnings", status_code=302)
    for role, portal in (("PRINCIPAL", "principal"), ("HOD", "hod")):
        if role in roles:
            return RedirectResponse(f"/dashboard/{portal}/my-warnings", status_code=302)
    return RedirectResponse("/", status_code=302)


@app.get("/dashboard/faculty/watch", include_in_schema=False)
async def faculty_watch_page(request: Request):
    return templates.TemplateResponse(request, "faculty/watch.html", {"nav_items": FACULTY_NAV_ITEMS, "active_nav": "watch"})


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


# Outermost: sees every request and its final status, records attack-looking and refused attempts (app/security).
from app.security.middleware import SecurityWatch  # noqa: E402

app.add_middleware(SecurityWatch)

class HeadLikeGet:
    """Answer HEAD like GET, without the body. WhatsApp, Facebook and other link-preview robots often ask with HEAD
    first (is the page / picture there?); without this every page answered 405 and no preview was shown.
    Outermost, so the routes, the security checks and the logging all see an ordinary GET."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "HEAD":
            await self.app(scope, receive, send)
            return
        scope = dict(scope, method="GET")

        async def no_body(message):
            if message["type"] == "http.response.body":
                message = {"type": "http.response.body", "body": b"", "more_body": message.get("more_body", False)}
            await send(message)

        await self.app(scope, receive, no_body)


app.add_middleware(HeadLikeGet)
