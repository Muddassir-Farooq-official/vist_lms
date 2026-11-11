from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class EnrollmentStatus(str, enum.Enum):
    ENROLLED = "ENROLLED"  # currently taking it, this term
    COMPLETED = "COMPLETED"  # term finished — see outcome for pass/fail
    WITHDRAWN = "WITHDRAWN"  # dropped before completion, no outcome recorded


class EnrollmentOutcome(str, enum.Enum):
    """Null until the course is actually finished — a currently-ENROLLED
    row has no outcome yet. Grading itself belongs to the future
    Exams/Grading module; this is just where the final result lands
    once that exists, and what retake-eligibility checks read."""

    PASSED = "PASSED"
    FAILED = "FAILED"


class LectureResourceType(str, enum.Enum):
    NOTES = "NOTES"
    SLIDES = "SLIDES"
    OTHER = "OTHER"


class Course(UUIDPKMixin, TimestampMixin, Base):
    """The abstract subject — 'Anatomy I', 3 credit hours. Deliberately
    NOT tied to a single program directly (approved decision: a course
    can be shared across programs) — see CourseProgram for where a
    course's per-program semester slot actually lives."""

    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("code", name="uq_courses_code"),)

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    credit_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CourseProgram(Base):
    """Join row: which program(s) a course belongs to, and at which
    semester within EACH program — the same course can sit at semester 2
    in one program and semester 1 in another (approved decision), so the
    semester number lives here, not on Course itself. Composite PK on
    (course_id, program_id) — a course can't be listed twice in the same
    program's curriculum, but can appear once per distinct program."""

    __tablename__ = "course_programs"
    __table_args__ = (CheckConstraint("semester_number > 0", name="positive_semester_number"),)

    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True)
    semester_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # lazy="selectin": async SQLAlchemy raises MissingGreenlet on a
    # lazy-loaded relationship accessed outside an active await — this
    # codebase's established discipline elsewhere is to avoid relationship
    # attribute access in service/business logic entirely (explicit
    # session.get() calls instead, see app/academics/service.py). This
    # eager-load setting is a safety net for read-only/admin listing
    # code paths only, not something service logic should depend on.
    course: Mapped[Course] = relationship(lazy="selectin")


class CourseOffering(UUIDPKMixin, TimestampMixin, Base):
    """One term's actual instance of a course — 'Anatomy I, Fall 2026,
    taught by EMP-0012'. This is what a teacher is assigned to and what
    students enroll in — never the abstract Course directly. Deliberately
    NOT program-scoped itself (a single offering can serve students from
    any program the course belongs to); a student's OWN program is what
    determines their credit-hour cap, checked at enrollment time."""

    __tablename__ = "course_offerings"
    __table_args__ = (
        # A course can run several SECTIONS in the same term (Section A/B,
        # Morning/Evening), each with its own teacher, timetable, roster,
        # attendance and marks -- an offering IS a section.
        UniqueConstraint("course_id", "term_label", "section_label", name="uq_course_offerings_course_term_section"),
        CheckConstraint("capacity IS NULL OR capacity > 0", name="positive_capacity"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    term_label: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "Fall 2026"
    # Section / shift label: "A", "B", "Morning", "Evening". Students whose own
    # section_label matches attend the offerings with the same label.
    section_label: Mapped[str] = mapped_column(String(30), nullable=False, default="A", server_default="A")
    # Max students that can be ENROLLED at once; NULL = unlimited.
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Admin lock on the whole course's content: syllabus and every class. Individual
    # classes can also be locked on their own (ClassSession.content_locked_at).
    content_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    # Admin-controlled toggle, not a date-based automatic window —
    # keeps "enrollment period is open/closed" a deliberate admin
    # decision rather than something that silently flips on a calendar.
    is_open_for_enrollment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Combined class (app/academics/combined.py): sections of the SAME course in the same term that one teacher
    # teaches together, e.g. AHS201 for BS Anesthesia (A) and BS Dental (B). They share one timetable, one teacher,
    # one attendance list and one exam paper, while each section keeps its own students, attendance and marks.
    combined_group_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)

    # One combined syllabus document per offering (same "one supporting
    # PDF" pattern as the student/employee certificate upload) —
    # per-lecture resources (notes/slides) are separate, see
    # LectureResource below.
    syllabus_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    syllabus_original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Set once by AttendanceService.lock_attendance() — a deliberate,
    # one-way admin action per offering (separate from End Semester)
    # that finalizes attendance for the term: stops further marking and
    # evaluates every enrolled student's final % against the
    # institute's fine/drop thresholds. NULL = still open for marking.
    attendance_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The actual calendar boundary of this offering's term — required
    # before a TimetableSlot can be created for it, since slot creation
    # generates one ClassSession per matching weekday in this range.
    # term_label (above) stays a free-text display label ("Fall 2026")
    # separate from these real dates.
    term_start_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    term_end_date: Mapped[date | None] = mapped_column(Date(), nullable=True)

    course: Mapped[Course] = relationship(lazy="selectin")  # see CourseProgram.course above re: lazy="selectin"


class Enrollment(UUIDPKMixin, TimestampMixin, Base):
    """A student's registration in one course offering. Unique per
    (student, offering) — you can't enroll in the exact same term's
    offering twice, but CAN have multiple enrollment rows across
    different terms for the same underlying course (that's exactly what
    a retake is — a new Enrollment row against a later offering, with
    is_retake=True)."""

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "course_offering_id", name="uq_enrollments_student_offering"),
        Index("ix_enrollments_student_id", "student_id"),
        Index("ix_enrollments_course_offering_id", "course_offering_id"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    course_offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[EnrollmentStatus] = mapped_column(
        Enum(EnrollmentStatus, name="enrollment_status"), default=EnrollmentStatus.ENROLLED, nullable=False
    )
    outcome: Mapped[EnrollmentOutcome | None] = mapped_column(
        Enum(EnrollmentOutcome, name="enrollment_outcome"), nullable=True
    )
    is_retake: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Why and when a WITHDRAWN row ended: ATTENDANCE (dropped for low attendance), STUDENT_LEFT, LATE (office withdrew after the deadline), or a free reason.
    withdrawal_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    course_offering: Mapped[CourseOffering] = relationship(lazy="selectin")  # see CourseProgram.course above re: lazy="selectin"


class DayOfWeek(str, enum.Enum):
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"


# Python's date.weekday(): Monday=0 ... Sunday=6 — used to translate a
# generated calendar date to/from DayOfWeek without a lookup table.
DAY_OF_WEEK_BY_PYTHON_WEEKDAY = [
    DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY,
    DayOfWeek.FRIDAY, DayOfWeek.SATURDAY, DayOfWeek.SUNDAY,
]


class ClassSessionStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    CANCELLED = "CANCELLED"


class Period(UUIDPKMixin, TimestampMixin, Base):
    """One institute-wide daily period — 'Period 3, 10:00-10:50' — the
    same times every day, admin-defined once. Not tied to any offering
    or day of week; TimetableSlot references one via period_id so both
    manual slot creation and TimetableService.auto_assign pick from a
    fixed, consistent daily structure instead of arbitrary times."""

    __tablename__ = "periods"
    __table_args__ = (UniqueConstraint("period_number", name="uq_periods_period_number"),)

    period_number: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time(), nullable=False)
    end_time: Mapped[time] = mapped_column(Time(), nullable=False)


class TimetableSlot(UUIDPKMixin, TimestampMixin, Base):
    """A recurring weekly pattern — 'ANAT101, every Monday, 09:00-10:00,
    Room 204, for the whole term'. Creating one immediately generates a
    ClassSession for every matching weekday between the offering's
    term_start_date and term_end_date (see TimetableService.create_slot)
    — this row is the template, not itself something attendance or
    content ever attaches to. Deleting a slot cancels its own
    not-yet-occurred sessions (today or later) and leaves past sessions,
    with whatever attendance/content history they have, untouched.

    start_time/end_time remain the authoritative fields everything else
    (conflict checks, session generation) reads — period_id is set
    alongside them purely so the UI can show 'Period 3' instead of raw
    times; it's a display/provenance link, not a second source of truth.
    Nullable so a Period can later be deleted without cascading onto
    every slot that was created from it."""

    __tablename__ = "timetable_slots"

    course_offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False
    )
    period_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("periods.id", ondelete="SET NULL"), nullable=True)
    day_of_week: Mapped[DayOfWeek] = mapped_column(Enum(DayOfWeek, name="day_of_week"), nullable=False)
    start_time: Mapped[time] = mapped_column(Time(), nullable=False)
    end_time: Mapped[time] = mapped_column(Time(), nullable=False)
    room: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ClassSession(UUIDPKMixin, TimestampMixin, Base):
    """One actual dated occurrence of a class — what attendance is
    marked against and where per-session content (topic, description,
    uploaded notes/slides — see ClassSessionResource) lives. Fully
    replaces the old Lecture model (see migration
    e6b3a9f27d5c_timetable_and_class_sessions for the history): a
    Lecture used to combine "syllabus topic" and "scheduling" into one
    manually-created row; a ClassSession is machine-generated from a
    TimetableSlot for scheduling, and the topic/description/resources
    are filled in afterward, per session, as the term progresses —
    same as filling in a real class register day by day.

    start_time/end_time/room are copied from the generating slot AT
    GENERATION TIME, not read live from it — editing a slot later (or
    this one session, via the per-session override endpoints) never
    retroactively changes any other session's already-recorded
    schedule, matching the same snapshot discipline used everywhere
    else in this codebase (e.g. the fee ledger)."""

    __tablename__ = "class_sessions"
    __table_args__ = (
        UniqueConstraint("course_offering_id", "session_date", "start_time", name="uq_class_sessions_offering_date_start"),
        Index("ix_class_sessions_offering_id", "course_offering_id"),
        Index("ix_class_sessions_slot_id", "timetable_slot_id"),
        Index("ix_class_sessions_session_date", "session_date"),
    )

    course_offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable so a slot can be deleted (see TimetableSlot's docstring)
    # without cascading that delete onto sessions that already happened
    # and have real attendance/content history — ON DELETE SET NULL,
    # not CASCADE.
    timetable_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("timetable_slots.id", ondelete="SET NULL"), nullable=True
    )
    session_date: Mapped[date] = mapped_column(Date(), nullable=False)
    start_time: Mapped[time] = mapped_column(Time(), nullable=False)
    end_time: Mapped[time] = mapped_column(Time(), nullable=False)
    room: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[ClassSessionStatus] = mapped_column(
        Enum(ClassSessionStatus, name="class_session_status"), default=ClassSessionStatus.SCHEDULED, nullable=False
    )
    cancelled_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Topic/content — blank at generation time, filled in per session as
    # the term progresses (see this class's own docstring).
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Set by the teacher alongside marking attendance — "did we actually
    # get through today's topic" — feeds CourseProgressService's
    # on-time/delayed/missing computation. Independent of attendance
    # itself: a session can have attendance marked but the topic left
    # incomplete (ran out of time), or vice versa.
    topic_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Teachers can only mark attendance within 15 minutes of
    # start_time (approved rule) — set only when ADMIN marks outside
    # that window on the teacher's behalf; its mere presence IS the
    # "marked late by admin" flag, so no separate boolean is needed.
    late_marking_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Set the moment attendance is submitted for this class (even when there
    # were zero enrolled students, so no records exist). This -- not "does
    # any record exist" -- is what says a class has been taken; it is what
    # the "needs attention" list and the lock check rely on.
    attendance_taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Admin lock on this class's CONTENT (topic, description, files, links).
    # While set, the assigned teacher can view but not change or delete it.
    content_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClassSessionResource(UUIDPKMixin, TimestampMixin, Base):
    """One resource attached to a class session — either an uploaded
    file (storage_key set, external_url null) or a link like a Slides
    URL (external_url set, storage_key null) — a session can have
    several of either kind. Exactly one of the two is ever set;
    original_name doubles as the display name either way (the file's
    original filename, or the link's user-given title)."""

    __tablename__ = "class_session_resources"
    __table_args__ = (
        Index("ix_class_session_resources_session_id", "class_session_id"),
        CheckConstraint(
            "(storage_key IS NOT NULL) != (external_url IS NOT NULL)",
            name="ck_class_session_resources_exactly_one_of_file_or_link",
        ),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), nullable=False)
    resource_type: Mapped[LectureResourceType] = mapped_column(
        Enum(LectureResourceType, name="lecture_resource_type"), nullable=False
    )
    storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)


class AssessmentType(UUIDPKMixin, TimestampMixin, Base):
    """One entry in the institute-wide result-type catalog — 'Quiz 1'
    (max_marks=10), 'Mid Term' (max_marks=50), 'Assignment 2'
    (max_marks=20) — admin-defined once, then picked by any teacher
    for any subject via OfferingAssessment. max_marks is fixed here
    and applies everywhere this type gets used — a subject needing a
    different total gets its own catalog entry (e.g. 'Quiz 1 (15
    marks)'), rather than each subject setting its own total for the
    same named type. category is a free-text grouping label (e.g.
    'QUIZ', 'ASSIGNMENT', 'EXAM') purely for display/filtering — not
    an enum, since different institutes (school/college/university)
    group things differently and a fixed list would only fit one."""

    __tablename__ = "assessment_types"
    __table_args__ = (UniqueConstraint("name", name="uq_assessment_types_name"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    max_marks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)


class OfferingAssessment(UUIDPKMixin, TimestampMixin, Base):
    """One catalog AssessmentType applied to one course offering — the
    teacher (or admin) picks which types from the institute-wide
    catalog apply to their subject (e.g. this offering uses Quiz 1,
    Quiz 2, Mid Term, and Final, but not Assignment 3). A subject's
    overall result is the raw sum of obtained marks across every
    OfferingAssessment here, divided by the sum of their max_marks —
    see GradingService._maybe_recompute_outcome. Only auto-computed
    once every OfferingAssessment defined here has a mark entered for
    that student."""

    __tablename__ = "offering_assessments"
    __table_args__ = (
        UniqueConstraint("course_offering_id", "assessment_type_id", name="uq_offering_assessments_offering_type"),
        Index("ix_offering_assessments_offering_id", "course_offering_id"),
    )

    course_offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False
    )
    assessment_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_types.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class StudentMark(UUIDPKMixin, TimestampMixin, Base):
    """One student's obtained score on one OfferingAssessment (a
    catalog AssessmentType applied to their offering), for one
    specific Enrollment — scoped to the enrollment, not just the
    student+assessment, so a retake (a separate Enrollment row for the
    same course) always gets its own fresh marks, never inheriting or
    colliding with a prior attempt's scores."""

    __tablename__ = "student_marks"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "offering_assessment_id", name="uq_student_marks_enrollment_assessment"),
        Index("ix_student_marks_enrollment_id", "enrollment_id"),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False)
    offering_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offering_assessments.id", ondelete="CASCADE"), nullable=False
    )
    obtained_marks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    entered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResultSheetStatus(str, enum.Enum):
    DRAFT = "DRAFT"              # the teacher is still entering marks
    SUBMITTED = "SUBMITTED"      # sent for approval: locked for everyone
    RETURNED = "RETURNED"        # sent back to the teacher with a reason: editable again
    APPROVED = "APPROVED"        # checked and correct, waiting for the administration to publish it
    PUBLISHED = "PUBLISHED"      # students can see their marks; pass/fail is decided


class ResultSheet(UUIDPKMixin, TimestampMixin, Base):
    """The approval state of ONE course offering's marks. The marks themselves stay in student_marks; this decides whether they
    can still be edited and whether students can see them. (Offerings with no row here are simply in DRAFT.)"""

    __tablename__ = "result_sheets"
    __table_args__ = (UniqueConstraint("course_offering_id", name="uq_result_sheets_offering"),)

    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[ResultSheetStatus] = mapped_column(Enum(ResultSheetStatus, name="result_sheet_status"), nullable=False, default=ResultSheetStatus.DRAFT)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    returned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")      # marks corrected after publication
    resubmission_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")    # times it was sent back


class GradeScaleEntry(UUIDPKMixin, TimestampMixin, Base):
    """One row of the institute's grading scale: from this percentage upward the student gets this letter and these grade points."""

    __tablename__ = "grade_scale_entries"
    __table_args__ = (UniqueConstraint("letter", name="uq_grade_scale_letter"), UniqueConstraint("min_percent", name="uq_grade_scale_min_percent"))

    letter: Mapped[str] = mapped_column(String(4), nullable=False)
    min_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    grade_points: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    is_passing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EnrollmentResult(UUIDPKMixin, TimestampMixin, Base):
    """A student's published result for one course attempt, frozen at the moment it was published (or corrected). Changing the grading scale
    later never rewrites a result that was already given -- this is what the transcript is built from."""

    __tablename__ = "enrollment_results"
    __table_args__ = (UniqueConstraint("enrollment_id", name="uq_enrollment_results_enrollment"),)

    enrollment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False)
    percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    total_obtained: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    total_max: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    letter_grade: Mapped[str] = mapped_column(String(4), nullable=False)
    grade_points: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    credit_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)               # PASSED or FAILED
    attendance_percentage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    failed_for_attendance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True when this pass was earned in a supplementary (reappear) exam; shown as (S) on the result card.
    supplementary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class CoursePrerequisite(UUIDPKMixin, Base):
    """Course `course_id` may only be taken (first attempt) after passing `requires_course_id` (a published pass).
    An administrator can still enrol a student with a written reason. See migration c7e9f1a3b5d7."""

    __tablename__ = "course_prerequisites"
    __table_args__ = (
        UniqueConstraint("course_id", "requires_course_id", name="uq_course_prerequisite_pair"),
        CheckConstraint("course_id <> requires_course_id", name="not_itself"),
        Index("ix_course_prerequisites_course", "course_id"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    requires_course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
