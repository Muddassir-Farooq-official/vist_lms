"""Canonical role and permission codes (Section 18/19).

Authorization checks use these string constants — never raw role
comparisons like `if user.role == "teacher"`. Routers depend on
`require_permission(...)`; resource-scope checks happen in each domain's
own `permissions.py` (see app/students/permissions.py for an example).
"""
from __future__ import annotations


class Roles:
    ADMIN = "ADMIN"
    TEACHER = "TEACHER"
    STUDENT = "STUDENT"
    ACCOUNTS_OFFICER = "ACCOUNTS_OFFICER"
    # NOTE: Library and Hostel are placeholder roles — there is no library
    # or hostel domain module built yet (same situation as fees/attendance/
    # exams before their modules existed). These roles exist so accounts
    # and a login portal can be created now, but they carry NO permissions
    # below until an actual library/hostel module is designed and approved.
    # ASSUMPTION — REQUIRES APPROVAL: role names, scope, and eventual
    # permission set for these two are not yet defined by the business.
    LIBRARIAN = "LIBRARIAN"
    HOSTEL_WARDEN = "HOSTEL_WARDEN"
    SECURITY_GUARD = "SECURITY_GUARD"      # marks students and staff in and out at the gate
    # Head of Department / Coordinator: academic oversight below the administration —
    # decides teachers' attendance correction requests, follows up classes left
    # unmarked, and watches every teacher's course progress. Usually given to a
    # senior teacher as EXTRA access (Employees -> Extra access).
    HOD = "HOD"
    # Principal / Director: sees how the whole institute is doing (students, attendance, results, fees,
    # teacher progress) without being able to change anything.
    PRINCIPAL = "PRINCIPAL"
    PARENT = "PARENT"   # a student's guardian: read-only view of their own children (attendance, results, fees)
    EXAM_CONTROLLER = "EXAM_CONTROLLER"    # the exam department: approves, publishes and corrects results, and keeps the grading scale

    ALL = (ADMIN, TEACHER, STUDENT, ACCOUNTS_OFFICER, LIBRARIAN, HOSTEL_WARDEN, SECURITY_GUARD, EXAM_CONTROLLER)


class Permissions:
    # Students
    STUDENT_VIEW = "student.view"
    STUDENT_CREATE = "student.create"
    STUDENT_UPDATE = "student.update"
    STUDENT_IMPORT = "student.import"

    # Programs
    PROGRAM_VIEW = "program.view"
    PROGRAM_CREATE = "program.create"
    PROGRAM_UPDATE = "program.update"
    PROGRAM_DELETE = "program.delete"

    # Academics — curriculum (Course/CourseProgram), offerings (term
    # instances + teacher assignment), enrollment, and lecture resources.
    # COURSE_VIEW is broad (everyone browsing the curriculum needs it);
    # every *_MANAGE permission here is ADMIN-only, including
    # LECTURE_MANAGE — approved decision: uploading/setting the syllabus
    # and lecture resources is an admin action, not a teacher one.
    # Faculty can VIEW everything for their own assigned offerings
    # (COURSE_VIEW + an ownership check, not a separate permission) but
    # never upload, replace, or remove anything.
    COURSE_VIEW = "course.view"
    COURSE_MANAGE = "course.manage"
    COURSE_OFFERING_MANAGE = "course_offering.manage"
    ENROLLMENT_VIEW = "enrollment.view"  # scoped to own record for STUDENT/TEACHER; unscoped for ADMIN
    ENROLLMENT_SELF = "enrollment.self"  # a student enrolling/withdrawing themselves
    ENROLLMENT_MANAGE = "enrollment.manage"  # ADMIN: enroll/withdraw anyone, end-of-semester reset
    LECTURE_MANAGE = "lecture.manage"  # upload/replace/remove syllabus + lecture resources — ADMIN ONLY

    # Attendance (reserved for future phase)
    ATTENDANCE_MARK = "attendance.mark"
    ATTENDANCE_VIEW = "attendance.view"
    # Admin-only: finalizes an offering's attendance for the term —
    # stops further marking and evaluates every enrolled student's
    # final % against the fine/drop thresholds. A deliberate, one-way
    # action per offering, separate from End Semester.
    ATTENDANCE_LOCK = "attendance.lock"

    # Marks / results (reserved for future phase)
    MARKS_ENTER = "marks.enter"
    MARKS_PUBLISH = "marks.publish"

    # Fees (reserved for future phase). Per approved governance: ADMIN
    # defines what's chargeable (fee.manage); ACCOUNTS_OFFICER can only
    # record payments against what ADMIN already defined, never create or
    # edit fee structures/installment plans themselves.
    FEE_VIEW = "fee.view"
    FEE_MANAGE = "fee.manage"
    PAYMENT_RECORD = "payment.record"
    # Reversing a recorded payment (mark-unpaid) is a correction/undo
    # action, not day-to-day collection — ADMIN only, separate from
    # PAYMENT_RECORD so Accounts Officer can mark something paid without
    # also being able to reverse it.
    PAYMENT_REVERSE = "payment.reverse"
    # Granting a scholarship or concession reduces what a student owes, so it stays with the administration (the accounts office records balances and payments).
    SCHOLARSHIP_MANAGE = "scholarship.manage"

    # Fines — deliberately separate from Fees: ad-hoc, reason-driven,
    # single-entry (a library late fee, a disciplinary fine), not a
    # structured installment plan.
    #
    # UPDATED DECISION: issuing authority is ADMIN-ONLY now (superseding
    # the earlier "any concern role" design) — Librarian, Accounts
    # Officer, and Teacher can all still VIEW fines (and Accounts
    # Officer can still resolve/mark-paid ones via FINE_MANAGE, a
    # financial-processing duty distinct from originating a charge),
    # but only Admin can create one. The library Overdue page's
    # "Issue Fine" button (and the shared Fines page's own button)
    # already gate on this exact permission client-side, so removing
    # the grant is the entire fix — no frontend code changes needed.
    FINE_ISSUE = "fine.issue"  # create a fine DIRECTLY, already-approved — ADMIN ONLY. Also required to approve/reject a request (see FINE_REQUEST below) — approving IS the admin-only act of issuing, just against someone else's requested amount/reason instead of typing in your own.
    # LIBRARIAN and ACCOUNTS_OFFICER hold this instead of FINE_ISSUE —
    # they can REQUEST a fine (PENDING_APPROVAL, not yet chargeable,
    # not yet visible to the payer) but not issue one directly.
    # FineService.request() restricts each role to its own domain
    # regardless of what a client sends: librarian -> LIBRARY source
    # only, accounts officer -> OTHER source only.
    FINE_REQUEST = "fine.request"
    FINE_VIEW = "fine.view"  # scoped to own record for STUDENT/TEACHER; unscoped for staff roles below
    FINE_MANAGE = "fine.manage"  # mark paid, full visibility across all borrowers
    # Waiving (forgiving without payment) is an administrative decision,
    # not a collection action — ADMIN only, separate from FINE_MANAGE so
    # Accounts Officer can mark a fine paid without also being able to
    # dismiss it without collecting anything.
    FINE_WAIVE = "fine.waive"

    # Feedback & Complaints — scalable by design (approved decision):
    # who can FILE one is controlled entirely by this permission grant.
    # STUDENT and TEACHER hold it today ("first priority"); extending to
    # another role later (e.g. LIBRARIAN filing about a vendor) is a
    # one-line grant, nothing else changes. Resolution authority
    # (COMPLAINT_MANAGE) stays ADMIN-only per approved decision — the
    # complainant's own withdraw/self-resolve actions are gated on
    # COMPLAINT_SUBMIT instead (if you can file one, you can manage your
    # own), not a separate permission.
    COMPLAINT_SUBMIT = "complaint.submit"
    COMPLAINT_MANAGE = "complaint.manage"

    # Writing, attaching and publishing official announcements (everyone can READ the ones meant for them).
    ANNOUNCEMENT_MANAGE = "announcement.manage"

    # Assignments: a teacher sets work for their own classes and reviews it; a student submits a Google Drive link.
    ASSIGNMENT_MANAGE = "assignment.manage"
    ASSIGNMENT_SUBMIT = "assignment.submit"

    # Applications (leave, certificates, concessions...): anyone with an account may apply; the administrator decides.
    APPLICATION_SUBMIT = "application.submit"
    APPLICATION_MANAGE = "application.manage"

    # Tasks between staff: anyone on staff (and the admin) can assign and receive; the administration sees everything.
    TASK_USE = "task.use"
    TASK_MANAGE = "task.manage"

    # The gate: the guard (and the administration) mark people in and out; only the administration sets timings, rules and decides fines.
    GATE_OPERATE = "gate.operate"
    GATE_MANAGE = "gate.manage"

    # Results: the administration (or, later, an exam controller) approves and publishes a course's marks. Teachers enter and submit them.
    RESULT_APPROVE = "result.approve"

    # Accounts: expenses, other income, vendors, the day book and reports. The accounts office records; the administration approves large expenses and voids.
    ACCOUNT_VIEW = "account.view"
    ACCOUNT_RECORD = "account.record"
    ACCOUNT_APPROVE = "account.approve"
    # Purchasing: any staff member may ask for something to be bought and follow the request.
    PURCHASE_REQUEST = "purchase.request"
    # Payroll: the accounts office prepares and pays it; the administration approves it. Employees see only their own payslips (no permission needed).
    PAYROLL_VIEW = "payroll.view"
    PAYROLL_MANAGE = "payroll.manage"
    PAYROLL_APPROVE = "payroll.approve"

    # Employees — entire module is ADMIN-only for now (see
    # ROLE_PERMISSION_SEED). Not extended to ACCOUNTS_OFFICER because that
    # role's authority was explicitly scoped to fees, not staff comp.
    EMPLOYEE_VIEW = "employee.view"
    EMPLOYEE_CREATE = "employee.create"
    EMPLOYEE_UPDATE = "employee.update"
    JOB_ROLE_MANAGE = "job_role.manage"
    SALARY_MANAGE = "salary.manage"

    # Library — LIBRARIAN owns this domain end-to-end (catalogue,
    # inventory, issue/return), same "job role IS the authority over their
    # own module" pattern as ACCOUNTS_OFFICER-over-fees. ADMIN also has
    # full access as supreme authority. Student/teacher borrowing access
    # is a separate, later phase gated on the not-yet-built Fee module —
    # not implemented here.
    LIBRARY_VIEW = "library.view"
    LIBRARY_MANAGE = "library.manage"  # catalogue + inventory (books, copies)
    LIBRARY_ISSUE = "library.issue"  # issue/return operations, and approving/rejecting borrow requests
    # Self-service: a student/faculty member asking the librarian for a
    # specific title. Distinct from LIBRARY_ISSUE (which is "I decide who
    # gets what") — this only lets you ask, never issue to yourself.
    LIBRARY_REQUEST = "library.request"
    BONUS_MANAGE = "bonus.manage"

    # Reports
    REPORTS_VIEW = "reports.view"

    # Admin / RBAC management
    ROLE_MANAGE = "role.manage"
    AUDIT_VIEW = "audit.view"
    # Change institute policy values (attendance thresholds, promotion rules, fee grace days) from Settings.
    POLICY_MANAGE = "policy.manage"
    # Import past (pre-system) results so existing students' transcripts and CGPA are complete.
    RESULT_HISTORY_IMPORT = "result.history.import"
    # Decide teachers' attendance correction requests and see classes left unmarked (admin + HOD).
    ATTENDANCE_CORRECTION_DECIDE = "attendance.correction.decide"
    # See every teacher's course progress (admin + HOD).
    ACADEMIC_OVERSIGHT = "academic.oversight"
    # Read-only institute-wide overview and dashboards (admin + principal).
    INSTITUTE_OVERVIEW = "institute.overview"
    # Build and publish exam datesheets, print any student's admit card (admin + exam controller).
    EXAM_SCHEDULE_MANAGE = "exam.schedule.manage"
    # See the SMS log, send queued messages, retry failures, send a test SMS (admin).
    SMS_MANAGE = "sms.manage"
    # Approve or reject fee refunds requested by the accounts office (admin; never the requester).
    FEE_REFUND_APPROVE = "fee.refund.approve"
    # Run admissions: rounds, applications, verification, merit lists, admitting (admin).
    ADMISSION_MANAGE = "admission.manage"
    # Parent portal: see one's OWN linked children only.
    PARENT_VIEW = "parent.view"
    # Create/link/unlink a guardian's parent login from a student's record (administration).
    PARENT_ACCESS_MANAGE = "parent.access.manage"
    # Scoped audit access for ACCOUNTS_OFFICER — financial actions only,
    # not the full system audit trail (student status changes, role
    # changes, etc.). Kept distinct from AUDIT_VIEW rather than widening
    # it, to preserve separation of duties.
    AUDIT_VIEW_FINANCIAL = "audit.view.financial"


# Role -> permission seed mapping used by scripts/seed_permissions.py.
# This is the single source of truth for what each role can do; changing
# authorization behavior means changing this table (and re-running the
# seed script), not editing scattered `if` statements.
ROLE_PERMISSION_SEED: dict[str, tuple[str, ...]] = {
    Roles.ADMIN: (
        Permissions.POLICY_MANAGE,
        Permissions.RESULT_HISTORY_IMPORT,
        Permissions.ATTENDANCE_CORRECTION_DECIDE,
        Permissions.ACADEMIC_OVERSIGHT,
        Permissions.INSTITUTE_OVERVIEW,
        Permissions.EXAM_SCHEDULE_MANAGE,
        Permissions.SMS_MANAGE,
        Permissions.FEE_REFUND_APPROVE,
        Permissions.ADMISSION_MANAGE,
        Permissions.PARENT_ACCESS_MANAGE,
        Permissions.STUDENT_VIEW,
        Permissions.STUDENT_CREATE,
        Permissions.STUDENT_UPDATE,
        Permissions.STUDENT_IMPORT,
        Permissions.PROGRAM_VIEW,
        Permissions.PROGRAM_CREATE,
        Permissions.PROGRAM_UPDATE,
        Permissions.PROGRAM_DELETE,
        Permissions.COURSE_VIEW,
        Permissions.COURSE_MANAGE,
        Permissions.COURSE_OFFERING_MANAGE,
        Permissions.ENROLLMENT_VIEW,
        Permissions.ENROLLMENT_MANAGE,
        Permissions.LECTURE_MANAGE,
        Permissions.ATTENDANCE_MARK,
        Permissions.ATTENDANCE_VIEW,
        Permissions.ATTENDANCE_LOCK,
        Permissions.MARKS_ENTER,
        Permissions.MARKS_PUBLISH,
        Permissions.FEE_VIEW,
        Permissions.FEE_MANAGE,
        Permissions.PAYMENT_RECORD,  # pre-existing gap: ADMIN never actually had this until the Fee module needed it — fixed
        Permissions.PAYMENT_REVERSE,
        Permissions.EMPLOYEE_VIEW,
        Permissions.EMPLOYEE_CREATE,
        Permissions.EMPLOYEE_UPDATE,
        Permissions.JOB_ROLE_MANAGE,
        Permissions.SALARY_MANAGE,
        Permissions.BONUS_MANAGE,
        Permissions.LIBRARY_VIEW,
        Permissions.LIBRARY_MANAGE,
        Permissions.LIBRARY_ISSUE,
        Permissions.REPORTS_VIEW,
        Permissions.ROLE_MANAGE,
        Permissions.AUDIT_VIEW,
        Permissions.FINE_ISSUE,
        Permissions.FINE_VIEW,
        Permissions.FINE_MANAGE,
        Permissions.FINE_WAIVE,
        Permissions.COMPLAINT_MANAGE,
        Permissions.ANNOUNCEMENT_MANAGE,
        Permissions.APPLICATION_MANAGE,
        Permissions.TASK_USE,
        Permissions.TASK_MANAGE,
        Permissions.GATE_OPERATE,
        Permissions.GATE_MANAGE,
        Permissions.RESULT_APPROVE,
        Permissions.SCHOLARSHIP_MANAGE,
        Permissions.ACCOUNT_VIEW,
        Permissions.ACCOUNT_RECORD,
        Permissions.ACCOUNT_APPROVE,
        Permissions.PURCHASE_REQUEST,
        Permissions.PAYROLL_VIEW,
        Permissions.PAYROLL_MANAGE,
        Permissions.PAYROLL_APPROVE,
    ),
    Roles.TEACHER: (
        Permissions.STUDENT_VIEW,  # scoped to assigned sections at the service layer
        Permissions.PROGRAM_VIEW,
        Permissions.COURSE_VIEW,
        Permissions.ENROLLMENT_VIEW,  # scoped to own assigned offerings' rosters
        # NOTE: no LECTURE_MANAGE here — per approved decision, uploading/
        # setting the syllabus and lecture resources is ADMIN-only now.
        # Faculty can VIEW everything for their own assigned offerings
        # (gated by COURSE_VIEW + the ownership check in
        # _can_view_offering_content) but cannot upload, replace, or
        # remove anything.
        Permissions.ATTENDANCE_MARK,
        Permissions.ATTENDANCE_VIEW,
        Permissions.MARKS_ENTER,
        Permissions.REPORTS_VIEW,  # scoped subset
        Permissions.LIBRARY_VIEW,  # browse the catalogue (books + journals)
        Permissions.LIBRARY_REQUEST,  # ask the librarian for a title; own loans are always visible via /library/me/*
        Permissions.FINE_VIEW,  # scoped to own record only (their own fines, as a fine payer) — issuing is ADMIN-only now
        Permissions.COMPLAINT_SUBMIT,
        Permissions.ASSIGNMENT_MANAGE,
        Permissions.APPLICATION_SUBMIT,
        Permissions.TASK_USE,
        Permissions.PURCHASE_REQUEST,
    ),
    Roles.STUDENT: (
        Permissions.STUDENT_VIEW,  # scoped to own record only
        Permissions.ATTENDANCE_VIEW,  # scoped to own record only
        Permissions.FEE_VIEW,  # scoped to own record only
        Permissions.COURSE_VIEW,
        Permissions.ENROLLMENT_VIEW,  # scoped to own record only
        Permissions.ENROLLMENT_SELF,
        Permissions.LIBRARY_VIEW,  # browse the catalogue (books + journals)
        Permissions.LIBRARY_REQUEST,  # ask the librarian for a title; own loans are always visible via /library/me/*
        Permissions.FINE_VIEW,  # scoped to own record only
        Permissions.COMPLAINT_SUBMIT,
        Permissions.ASSIGNMENT_SUBMIT,
        Permissions.APPLICATION_SUBMIT,
    ),
    Roles.ACCOUNTS_OFFICER: (
        Permissions.STUDENT_VIEW,  # read-only, for billing context
        Permissions.FEE_VIEW,
        # NOTE: deliberately NO Permissions.FEE_MANAGE — per approved
        # governance, fee structures/installment plans are ADMIN-defined
        # only.
        #
        # UPDATED DECISION (supersedes the earlier "1LINK automation means
        # no manual entry" note, and further narrowed by a later approved
        # decision): until real 1LINK credentials and a signature-verified
        # webhook exist, the payment gateway is fully simulated and its
        # webhook is disabled — so the accounts officer is the actual,
        # primary way a payment gets recorded day to day. But collecting
        # (mark-paid) and reversing (mark-unpaid) are NOT the same
        # authority level — reversing a recorded payment is a
        # correction/undo action, ADMIN-only via PAYMENT_REVERSE.
        # ACCOUNTS_OFFICER holds PAYMENT_RECORD (mark-paid) only.
        Permissions.PAYMENT_RECORD,
        Permissions.REPORTS_VIEW,  # scoped to financial reports
        Permissions.AUDIT_VIEW_FINANCIAL,  # scoped audit, financial actions only
        Permissions.FINE_VIEW,
        # FINE_MANAGE here covers mark-paid + full cross-borrower
        # visibility only — NOT waive (see FINE_WAIVE, ADMIN-only),
        # same "collect yes, forgive/reverse no" split as fees above.
        Permissions.FINE_MANAGE,
        # Can REQUEST a fine (with a note explaining why) for admin
        # review — not issue one directly. Domain-restricted to
        # source=OTHER by FineService.request(), same "stick to your
        # own domain" rule as LIBRARIAN below.
        Permissions.FINE_REQUEST,
        Permissions.APPLICATION_SUBMIT,
        Permissions.TASK_USE,
        Permissions.ACCOUNT_VIEW,
        Permissions.ACCOUNT_RECORD,
        Permissions.PURCHASE_REQUEST,
        Permissions.PAYROLL_VIEW,
        Permissions.PAYROLL_MANAGE,
    ),
    # Placeholder role — Hostel module not built yet.
    Roles.HOSTEL_WARDEN: (Permissions.APPLICATION_SUBMIT, Permissions.TASK_USE, Permissions.PURCHASE_REQUEST),
    Roles.EXAM_CONTROLLER: (Permissions.EXAM_SCHEDULE_MANAGE, Permissions.COURSE_VIEW, Permissions.RESULT_APPROVE, Permissions.MARKS_ENTER, Permissions.STUDENT_VIEW, Permissions.ENROLLMENT_VIEW, Permissions.APPLICATION_SUBMIT, Permissions.TASK_USE, Permissions.PURCHASE_REQUEST),
    Roles.PARENT: (Permissions.PARENT_VIEW,),
    Roles.PRINCIPAL: (Permissions.INSTITUTE_OVERVIEW, Permissions.ACADEMIC_OVERSIGHT, Permissions.ATTENDANCE_VIEW,
                      Permissions.COURSE_VIEW, Permissions.ENROLLMENT_VIEW, Permissions.STUDENT_VIEW,
                      Permissions.APPLICATION_SUBMIT, Permissions.TASK_USE, Permissions.PURCHASE_REQUEST),
    Roles.HOD: (Permissions.ATTENDANCE_CORRECTION_DECIDE, Permissions.ACADEMIC_OVERSIGHT, Permissions.ATTENDANCE_VIEW,
                Permissions.COURSE_VIEW, Permissions.ENROLLMENT_VIEW, Permissions.STUDENT_VIEW,
                Permissions.APPLICATION_SUBMIT, Permissions.TASK_USE, Permissions.PURCHASE_REQUEST),
    Roles.SECURITY_GUARD: (Permissions.GATE_OPERATE, Permissions.APPLICATION_SUBMIT, Permissions.TASK_USE, Permissions.PURCHASE_REQUEST),
    # LIBRARIAN owns the full library domain, same reasoning as
    # ACCOUNTS_OFFICER owning fee submission — this is their job.
    Roles.LIBRARIAN: (
        Permissions.LIBRARY_VIEW,
        Permissions.LIBRARY_MANAGE,
        Permissions.LIBRARY_ISSUE,
        Permissions.STUDENT_VIEW,  # scoped: needed to search borrowers by name/CNIC
        Permissions.REPORTS_VIEW,  # scoped to library reports
        Permissions.FINE_VIEW,  # needs to check a borrower's outstanding fines
        # Can REQUEST a fine (e.g. a damaged/lost book) for admin
        # review — not issue one directly, and restricted to
        # source=LIBRARY by FineService.request() regardless of what a
        # client sends. "Stick to your own domain" — a librarian has no
        # business requesting a disciplinary or general-purpose fine.
        Permissions.FINE_REQUEST,
        Permissions.APPLICATION_SUBMIT,
        Permissions.TASK_USE,
        Permissions.PURCHASE_REQUEST,
    ),
}