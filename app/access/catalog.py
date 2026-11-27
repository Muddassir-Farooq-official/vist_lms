"""What can be given on Settings -> Access control: every right, grouped by module, with its level.

Each module has three levels (View, Edit, Approve) plus "Advanced" for single rights. A level is a set of rights; Edit
includes View. Approve is separate (a principal may approve without editing). Some rights only make sense for the person
themselves (a student's own fees, a teacher's own assignments, anyone's own requests): they come with the built-in
roles and are not handed out here (OWN_USE). Two rights stay with Administrator only and are never handed out
(LOCKED). Money and control rights need a second administrator to confirm (SENSITIVE).
"""
from __future__ import annotations

from app.auth.permissions import Permissions as P

# (key, label, about, {"view": [...], "edit": [...], "approve": [...]}, admin pages it opens). "support": rights the
# module's pages also read (e.g. the course list for the timetable); added with any level, but they open no page.
MODULES: list[dict] = [
    {"key": "students", "label": "Students", "about": "Student records, admissions, profile changes and certificates.",
     "view": [P.STUDENT_LOOKUP], "edit": [P.STUDENT_CREATE, P.STUDENT_UPDATE, P.STUDENT_IMPORT, P.ENROLLMENT_MANAGE, P.RECORDS_ISSUE,
                                          P.RECORDS_DOCUMENTS, P.DOCUMENT_ISSUE, P.PARENT_ACCESS_MANAGE],
     "approve": [P.PROFILE_CHANGE_DECIDE],
     "pages": ["students", "reg-numbers", "profile-changes", "certificates", "imports"]},
    {"key": "academics", "label": "Programs & courses", "about": "Programs, courses, sections and their teachers, calendar, outcomes.",
     "view": [P.PROGRAM_VIEW, P.COURSE_VIEW, P.ENROLLMENT_VIEW],
     "edit": [P.PROGRAM_CREATE, P.PROGRAM_UPDATE, P.PROGRAM_DELETE, P.COURSE_MANAGE, P.COURSE_OFFERING_MANAGE, P.LECTURE_MANAGE, P.CALENDAR_MANAGE],
     "approve": [], "pages": ["programs", "courses", "calendar"]},
    {"key": "timetable", "label": "Timetable", "about": "Periods, rooms, the weekly timetable and substitute teachers.",
     "view": [], "edit": [P.TIMETABLE_MANAGE], "approve": [], "support": [P.PROGRAM_VIEW, P.COURSE_VIEW],
     "pages": ["timetable"]},
    {"key": "attendance", "label": "Attendance & classes", "about": "Class attendance, locking it, correction requests, substitute teachers, teacher progress and course feedback.",
     "view": [P.ATTENDANCE_VIEW, P.ACADEMIC_OVERSIGHT], "edit": [P.ATTENDANCE_MARK, P.ATTENDANCE_LOCK],
     "approve": [P.ATTENDANCE_CORRECTION_DECIDE], "pages": ["attendance", "progress", "substitutes", "feedback"]},
    {"key": "exams", "label": "Exams & results", "about": "Datesheets, seating, marks, result approval and publishing.",
     "view": [], "support": [P.PROGRAM_VIEW, P.COURSE_VIEW, P.ENROLLMENT_VIEW],
     "edit": [P.EXAM_SCHEDULE_MANAGE, P.MARKS_ENTER, P.RESULT_HISTORY_IMPORT],
     "approve": [P.RESULT_APPROVE, P.MARKS_PUBLISH],
     "pages": ["results", "result-sheets", "datesheets", "seating", "paper-files", "supplementary", "obe"]},
    {"key": "fees", "label": "Fees & fines", "about": "Fee structures, challans, payments, fines, scholarships and refunds.",
     "view": [P.FEE_OFFICE, P.FEE_VIEW, P.FINE_VIEW],
     "support": [P.STUDENT_LOOKUP], "edit": [P.FEE_MANAGE, P.PAYMENT_RECORD, P.FINE_ISSUE, P.FINE_MANAGE, P.OPENING_BALANCE_ADD, P.SCHOLARSHIP_PROPOSE, P.SCHOLARSHIP_MANAGE],
     "approve": [P.PAYMENT_REVERSE, P.FEE_REFUND_APPROVE, P.FINE_WAIVE, P.CHARGES_APPROVE],
     "pages": ["fees", "fines", "refunds", "monthly-charges"]},
    {"key": "accounts", "label": "Accounts (income & expenses)", "about": "The institute's books: income, expenses, vouchers.",
     "view": [P.ACCOUNT_VIEW, P.AUDIT_VIEW_FINANCIAL], "edit": [P.ACCOUNT_RECORD], "approve": [P.ACCOUNT_APPROVE],
     "pages": ["finance", "finance-reports", "fee-defaulters", "onebill"]},
    {"key": "approvals", "label": "Approvals", "about": "Decide requests waiting on the Approvals page (waivers, scholarships, write-offs...).",
     "view": [], "edit": [], "approve": [P.APPROVALS_DECIDE], "pages": ["approvals"]},
    {"key": "staff", "label": "Staff (HR)", "about": "Employee records, job titles, warnings.",
     "view": [P.EMPLOYEE_VIEW], "edit": [P.EMPLOYEE_CREATE, P.EMPLOYEE_UPDATE, P.JOB_ROLE_MANAGE, P.WARNING_ISSUE],
     "approve": [P.WARNING_DECIDE], "pages": ["employees", "warnings"]},
    {"key": "staff_attendance", "label": "Staff attendance", "about": "Staff check-in and out, leave and corrections.",
     "view": [P.STAFF_ATTENDANCE_VIEW], "edit": [P.STAFF_ATTENDANCE_MARK, P.STAFF_ATTENDANCE_MANAGE], "approve": [],
     "pages": ["staff-attendance"]},
    {"key": "payroll", "label": "Payroll", "about": "Salaries, payroll runs, bonuses.",
     "view": [P.PAYROLL_VIEW], "edit": [P.PAYROLL_MANAGE, P.SALARY_MANAGE, P.BONUS_MANAGE], "approve": [P.PAYROLL_APPROVE],
     "pages": ["payroll"]},
    {"key": "library", "label": "Library", "about": "Catalogue, issue and return.",
     "view": [P.LIBRARY_VIEW], "edit": [P.LIBRARY_ISSUE, P.LIBRARY_MANAGE], "approve": [], "pages": ["library"]},
    {"key": "hostel", "label": "Hostel & transport", "about": "Rooms, boarders, buses and routes.",
     "view": [P.HOSTEL_VIEW], "edit": [P.HOSTEL_MANAGE, P.TRANSPORT_MANAGE], "approve": [], "pages": ["hostel", "transport"]},
    {"key": "store", "label": "Store & assets", "about": "Stock, issuing items, fixed assets.",
     "view": [P.INVENTORY_VIEW, P.ASSET_VIEW], "edit": [P.INVENTORY_MANAGE, P.INVENTORY_ADJUST, P.ASSET_MANAGE], "approve": [P.ASSET_APPROVE],
     "pages": ["inventory", "assets"]},
    {"key": "communication", "label": "Communication", "about": "Announcements, SMS, complaints, committees, applications, tasks.",
     "view": [], "edit": [P.ANNOUNCEMENT_MANAGE, P.SMS_MANAGE, P.COMPLAINT_MANAGE, P.COMMITTEE_MANAGE, P.APPLICATION_MANAGE, P.TASK_MANAGE],
     "approve": [], "pages": ["announcements", "sms", "complaints", "committees", "applications", "tasks", "top-bars"]},
    {"key": "student_care", "label": "Mentoring, clinical & alumni", "about": "Mentors, clinical rotations, graduation and alumni.",
     "view": [], "edit": [P.MENTORING_MANAGE, P.CLINICAL_MANAGE, P.CLINICAL_SUPERVISE, P.ALUMNI_MANAGE], "approve": [],
     "pages": ["mentoring", "clinical", "alumni"]},
    {"key": "gate", "label": "Gate", "about": "Gate in and out, visitors.",
     "view": [], "edit": [P.GATE_OPERATE, P.GATE_MANAGE], "approve": [], "pages": ["gate"]},
    {"key": "reports", "label": "Reports & oversight", "about": "Reports, the institute overview, the activity log.",
     "view": [P.REPORTS_VIEW, P.INSTITUTE_OVERVIEW, P.AUDIT_VIEW], "edit": [P.AUDIT_EXPORT], "approve": [],
     "pages": ["overview", "reports", "monthly", "activity"]},
    {"key": "system", "label": "System & settings", "about": "Institute details, rules, backups, server health.",
     "view": [], "edit": [P.INSTITUTE_PROFILE_MANAGE, P.POLICY_MANAGE, P.BACKUP_MANAGE, P.SYSTEM_HEALTH], "approve": [],
     "pages": ["settings", "backups", "server-health", "data-check", "morning-summary", "rollover"]},
]

# Never handed out: only the Administrator role holds them.
LOCKED = frozenset({P.ROLE_MANAGE, P.VIEW_AS})

# Rights that belong to a person's own use (their own fees, assignments, requests...): they come with the built-in roles
# (Student, Teacher, Parent...) and are not handed out here, because given to someone else they would mean nothing or
# too much.
OWN_USE = frozenset({P.APPLICATION_SUBMIT, P.ENROLLMENT_SELF, P.ASSIGNMENT_SUBMIT, P.ASSIGNMENT_MANAGE, P.ALUMNI_SELF, P.COMPLAINT_SUBMIT,
                     P.TASK_USE, P.LIBRARY_REQUEST, P.FINE_REQUEST, P.PURCHASE_REQUEST, P.PARENT_VIEW, P.STUDENT_VIEW})

# Money and control: giving these (to a role or a person) needs a second administrator to confirm.
SENSITIVE = frozenset({
    P.PAYMENT_REVERSE, P.FEE_REFUND_APPROVE, P.FINE_WAIVE, P.CHARGES_APPROVE, P.SCHOLARSHIP_MANAGE, P.OPENING_BALANCE_ADD, P.FEE_MANAGE,
    P.ACCOUNT_APPROVE, P.ACCOUNT_RECORD, P.APPROVALS_DECIDE, P.PAYROLL_MANAGE, P.PAYROLL_APPROVE, P.SALARY_MANAGE, P.BONUS_MANAGE,
    P.INSTITUTE_PROFILE_MANAGE, P.POLICY_MANAGE, P.BACKUP_MANAGE, P.SYSTEM_HEALTH, P.AUDIT_EXPORT, P.EMPLOYEE_CREATE, P.JOB_ROLE_MANAGE,
    P.RESULT_APPROVE, P.MARKS_PUBLISH, P.STUDENT_IMPORT,
})

LABELS: dict[str, str] = {
    P.STUDENT_LOOKUP: "See any student's record", P.STUDENT_CREATE: "Add students", P.STUDENT_UPDATE: "Edit students",
    P.STUDENT_IMPORT: "Import students (CSV)", P.ENROLLMENT_MANAGE: "Enrol and withdraw students", P.RECORDS_ISSUE: "Issue certificates",
    P.RECORDS_DOCUMENTS: "Student documents", P.DOCUMENT_ISSUE: "Issue transcripts and documents", P.PARENT_ACCESS_MANAGE: "Parent logins",
    P.PROFILE_CHANGE_DECIDE: "Approve profile changes",
    P.PROGRAM_VIEW: "See programs", P.COURSE_VIEW: "See courses", P.ENROLLMENT_VIEW: "See enrolments", P.PROGRAM_CREATE: "Add programs",
    P.PROGRAM_UPDATE: "Edit programs", P.PROGRAM_DELETE: "Delete programs", P.COURSE_MANAGE: "Courses and curriculum",
    P.COURSE_OFFERING_MANAGE: "Course sections and teachers", P.LECTURE_MANAGE: "Syllabus and lecture files", P.CALENDAR_MANAGE: "Academic calendar",
    P.TIMETABLE_MANAGE: "Edit the timetable", P.ATTENDANCE_VIEW: "See attendance", P.ACADEMIC_OVERSIGHT: "Teacher progress and unmarked classes",
    P.ATTENDANCE_MARK: "Mark attendance", P.ATTENDANCE_LOCK: "Lock and unlock attendance", P.ATTENDANCE_CORRECTION_DECIDE: "Approve attendance corrections",
    P.EXAM_SCHEDULE_MANAGE: "Datesheets, seating, duties", P.MARKS_ENTER: "Enter marks", P.RESULT_HISTORY_IMPORT: "Import past results",
    P.RESULT_APPROVE: "Approve results", P.MARKS_PUBLISH: "Publish results",
    P.FEE_OFFICE: "Every student's fee account", P.FEE_VIEW: "See fees", P.FINE_VIEW: "See fines", P.FEE_MANAGE: "Fee structures and challans", P.PAYMENT_RECORD: "Take payments",
    P.FINE_ISSUE: "Issue fines", P.FINE_MANAGE: "Manage fines", P.OPENING_BALANCE_ADD: "Previous dues", P.SCHOLARSHIP_PROPOSE: "Propose scholarships",
    P.SCHOLARSHIP_MANAGE: "Give scholarships and discounts", P.PAYMENT_REVERSE: "Reverse payments", P.FEE_REFUND_APPROVE: "Approve refunds",
    P.FINE_WAIVE: "Waive fines", P.CHARGES_APPROVE: "Approve monthly charges",
    P.ACCOUNT_VIEW: "See the books", P.AUDIT_VIEW_FINANCIAL: "Money activity log", P.ACCOUNT_RECORD: "Record income and expenses",
    P.ACCOUNT_APPROVE: "Approve vouchers", P.APPROVALS_DECIDE: "Decide approval requests",
    P.EMPLOYEE_VIEW: "See staff records", P.EMPLOYEE_CREATE: "Add staff", P.EMPLOYEE_UPDATE: "Edit staff", P.JOB_ROLE_MANAGE: "Job titles",
    P.WARNING_ISSUE: "Issue warnings", P.WARNING_DECIDE: "Decide warnings",
    P.STAFF_ATTENDANCE_VIEW: "See staff attendance", P.STAFF_ATTENDANCE_MARK: "Mark staff in and out", P.STAFF_ATTENDANCE_MANAGE: "Manage staff attendance and leave",
    P.PAYROLL_VIEW: "See payroll", P.PAYROLL_MANAGE: "Run payroll", P.SALARY_MANAGE: "Set salaries", P.BONUS_MANAGE: "Bonuses", P.PAYROLL_APPROVE: "Approve payroll",
    P.LIBRARY_VIEW: "See the library", P.LIBRARY_ISSUE: "Issue and return books", P.LIBRARY_MANAGE: "Manage the catalogue",
    P.HOSTEL_VIEW: "See the hostel", P.HOSTEL_MANAGE: "Manage the hostel", P.TRANSPORT_MANAGE: "Manage transport",
    P.INVENTORY_VIEW: "See the store", P.ASSET_VIEW: "See fixed assets", P.INVENTORY_MANAGE: "Store: receive and issue", P.INVENTORY_ADJUST: "Store: corrections",
    P.ASSET_MANAGE: "Manage fixed assets", P.ASSET_APPROVE: "Approve asset write-offs",
    P.ANNOUNCEMENT_MANAGE: "Announcements", P.SMS_MANAGE: "SMS and WhatsApp", P.COMPLAINT_MANAGE: "Complaints", P.COMMITTEE_MANAGE: "Committees",
    P.APPLICATION_MANAGE: "Applications", P.TASK_MANAGE: "Tasks",
    P.MENTORING_MANAGE: "Mentors", P.CLINICAL_MANAGE: "Clinical rotations", P.CLINICAL_SUPERVISE: "Clinical supervision", P.ALUMNI_MANAGE: "Graduation and alumni",
    P.GATE_OPERATE: "Gate in and out", P.GATE_MANAGE: "Gate settings and visitors",
    P.REPORTS_VIEW: "Reports", P.INSTITUTE_OVERVIEW: "Institute overview", P.AUDIT_VIEW: "Activity log", P.AUDIT_EXPORT: "Download the activity log",
    P.INSTITUTE_PROFILE_MANAGE: "Institute details and logo", P.POLICY_MANAGE: "Rules (Settings)", P.BACKUP_MANAGE: "Backups", P.SYSTEM_HEALTH: "Server health and tools",
    P.ROLE_MANAGE: "Access control", P.VIEW_AS: "View as a person",
    # own use (come with the built-in roles; not handed out)
    P.APPLICATION_SUBMIT: "Send applications", P.ENROLLMENT_SELF: "Enrol themselves", P.ASSIGNMENT_SUBMIT: "Hand in assignments",
    P.ASSIGNMENT_MANAGE: "Set assignments (own classes)", P.ALUMNI_SELF: "Own alumni record", P.COMPLAINT_SUBMIT: "Make complaints",
    P.TASK_USE: "Own tasks", P.LIBRARY_REQUEST: "Ask to borrow books", P.FINE_REQUEST: "Ask for a fine", P.PURCHASE_REQUEST: "Purchase requests",
    P.PARENT_VIEW: "Their children's records", P.STUDENT_VIEW: "Students in their own classes / own record",
}

GRANTABLE = frozenset(c for m in MODULES for lvl in ("view", "edit", "approve") for c in m[lvl]) - LOCKED - OWN_USE


def label(code: str) -> str:
    return LABELS.get(code, code)


def module_of(code: str) -> dict | None:
    for m in MODULES:
        if code in m["view"] or code in m["edit"] or code in m["approve"]:
            return m
    return None


def page_rights() -> dict[str, list[str]]:
    """Admin page (menu slug) -> the rights that open it (any one of them; "support" rights open nothing)."""
    out: dict[str, list[str]] = {}
    for m in MODULES:
        rights = sorted(set(m["view"]) | set(m["edit"]) | set(m["approve"]))
        for page in m["pages"]:
            out.setdefault(page, [])
            out[page] = sorted(set(out[page]) | set(rights))
    return out


def describe() -> list[dict]:
    """The catalogue as the Access control page shows it."""
    return [{"key": m["key"], "label": m["label"], "about": m["about"], "pages": m["pages"], "support": list(m.get("support", [])),
             "levels": {lvl: [{"code": c, "label": label(c), "sensitive": c in SENSITIVE} for c in m[lvl] if c in GRANTABLE]
                        for lvl in ("view", "edit", "approve")}}
            for m in MODULES]
