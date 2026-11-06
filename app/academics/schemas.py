from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field

from app.academics.models import ClassSessionStatus, DayOfWeek, EnrollmentOutcome, EnrollmentStatus, LectureResourceType


class CourseCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    credit_hours: int = Field(gt=0, le=10)
    description: str | None = Field(default=None, max_length=1000)


class CourseUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=20)        # only while the course has never been offered
    title: str | None = Field(default=None, min_length=1, max_length=200)
    credit_hours: int | None = Field(default=None, gt=0, le=10)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    title: str
    credit_hours: int
    description: str | None
    is_active: bool


class CourseProgramLinkRequest(BaseModel):
    program_id: uuid.UUID
    semester_number: int = Field(gt=0, le=20)


class CourseProgramOut(BaseModel):
    course_id: uuid.UUID
    program_id: uuid.UUID
    semester_number: int
    course_code: str
    course_title: str
    credit_hours: int
    program_name: str


class CourseOfferingCreateRequest(BaseModel):
    course_id: uuid.UUID
    term_label: str = Field(min_length=1, max_length=50)
    assigned_teacher_id: uuid.UUID | None = None
    term_start_date: date | None = None
    term_end_date: date | None = None
    section_label: str = Field(default="A", min_length=1, max_length=30)
    capacity: int | None = Field(default=None, ge=1, le=2000)


class CurriculumMoveRequest(BaseModel):
    semester_number: int = Field(ge=1, le=20)


class CourseOfferingUpdateRequest(BaseModel):
    assigned_teacher_id: uuid.UUID | None = None
    is_open_for_enrollment: bool | None = None
    term_start_date: date | None = None
    term_end_date: date | None = None
    section_label: str | None = Field(default=None, min_length=1, max_length=30)
    capacity: int | None = Field(default=None, ge=1, le=2000)


class GenerateOfferingsRequest(BaseModel):
    """Open a whole semester: one offering per curriculum course."""

    term_label: str = Field(min_length=1, max_length=50)
    program_id: uuid.UUID
    semester_number: int = Field(ge=1, le=20)
    section_label: str = Field(default="A", min_length=1, max_length=30)
    # Optional: taken from the registered academic term (Courses -> Academic Terms) when left out.
    term_start_date: date | None = None
    term_end_date: date | None = None
    capacity: int | None = Field(default=None, ge=1, le=2000)
    open_for_enrollment: bool = False


class GenerateOfferingsResponse(BaseModel):
    term_label: str
    section_label: str
    created: list[dict]
    skipped: list[dict]


class AdminEnrollRequest(BaseModel):
    student_id: uuid.UUID


class BatchEnrollRequest(BaseModel):
    """Either an explicit student list, or filters (program / semester / section)."""

    student_ids: list[uuid.UUID] | None = None
    program_id: uuid.UUID | None = None
    semester_number: int | None = Field(default=None, ge=1, le=20)
    section_label: str | None = Field(default=None, max_length=30)


class BatchEnrollResponse(BaseModel):
    enrolled: int
    already_enrolled: int
    failed_count: int
    failed: list[dict]


class CourseOfferingOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    course_code: str
    course_title: str
    credit_hours: int
    term_label: str
    assigned_teacher_id: uuid.UUID | None
    teacher_name: str | None
    is_open_for_enrollment: bool
    has_syllabus: bool
    syllabus_original_name: str | None
    enrolled_count: int = 0
    attendance_locked_at: datetime | None = None
    term_start_date: date | None = None
    term_end_date: date | None = None
    section_label: str = "A"
    capacity: int | None = None
    content_locked: bool = False
    # Where the course sits in the curriculum, e.g. ["BSCS · Semester 3"] (one per program).
    curriculum: list[str] = []


class EnrollRequest(BaseModel):
    course_offering_id: uuid.UUID


class SetOutcomeRequest(BaseModel):
    """Admin-only placeholder ahead of a real Exams/Grading module — this
    is where a final Pass/Fail eventually gets recorded from, whatever
    records it (manual entry today, an exams module later)."""

    outcome: EnrollmentOutcome


class EnrollmentOut(BaseModel):
    id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    roll_number: str | None
    course_offering_id: uuid.UUID
    course_code: str
    course_title: str
    credit_hours: int
    term_label: str
    status: EnrollmentStatus
    outcome: EnrollmentOutcome | None
    is_retake: bool
    enrolled_at: datetime


class PeriodCreateRequest(BaseModel):
    period_number: int = Field(gt=0, le=50)
    start_time: time
    end_time: time


class PeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_number: int
    start_time: time
    end_time: time


class TimetableSlotCreateRequest(BaseModel):
    period_id: uuid.UUID
    day_of_week: DayOfWeek
    room: str = Field(min_length=1, max_length=50)
    # Added in the middle of a term: classes only from this day on (empty = the whole term, as before).
    starts_from: date | None = None


class TimetableSlotChangeRequest(BaseModel):
    from_date: date
    period_id: uuid.UUID
    day_of_week: DayOfWeek
    room: str = Field(min_length=1, max_length=50)


class TimetableSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_offering_id: uuid.UUID
    period_id: uuid.UUID | None
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    room: str


class TimetableSlotCreatedOut(BaseModel):
    slot: TimetableSlotOut
    sessions_generated: int


class AutoAssignRequest(BaseModel):
    sessions_per_week: int = Field(ge=1, le=5)
    room: str = Field(min_length=1, max_length=50)
    starts_from: date | None = None          # in the middle of a term: classes only from this day on


class AutoAssignResultOut(BaseModel):
    slots: list[TimetableSlotOut]
    sessions_generated: int


class MyScheduleEntryOut(BaseModel):
    id: uuid.UUID
    course_offering_id: uuid.UUID
    course_code: str
    course_title: str
    term_label: str
    session_date: date
    start_time: time
    end_time: time
    room: str
    title: str | None
    teacher_name: str | None = None
    section_label: str = "A"


class TeacherCourseOverviewOut(BaseModel):
    """One course a teacher teaches, at a glance."""

    offering_id: uuid.UUID
    course_code: str
    course_title: str
    credit_hours: int
    section_label: str
    term_label: str
    enrolled_count: int
    is_open_for_enrollment: bool
    content_locked: bool
    classes_total: int                # on the timetable (cancelled ones excluded)
    classes_held: int                 # attendance has been recorded
    topics_covered: int               # marked "covered"
    average_attendance: float | None  # % present across every record, None until a class is recorded
    unmarked_past_classes: int        # already happened but nobody recorded attendance
    next_class_date: date | None


class OfferingBrowseOut(CourseOfferingOut):
    """An open course in some term, plus whether THIS student may enroll in it right now (and why not)."""

    can_enroll: bool
    reason: str | None = None
    is_retake: bool = False
    already_enrolled: bool = False


class CourseOverviewOut(BaseModel):
    """One enrolled course at a glance: how much of it has been taught and how often the student attended."""

    offering_id: uuid.UUID
    course_code: str
    course_title: str
    section_label: str
    teacher_name: str | None
    classes_held: int                 # classes whose attendance has been taken (cancelled ones excluded)
    classes_attended: int
    attendance_percentage: float | None
    topics_total: int                 # classes on the timetable (cancelled excluded)
    topics_covered: int               # marked "covered" by the teacher


class InstituteTimetableSlotOut(BaseModel):
    id: uuid.UUID
    course_offering_id: uuid.UUID
    course_code: str
    course_title: str
    term_label: str
    teacher_name: str | None
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    room: str


class WeeklyPlacementOut(BaseModel):
    program_code: str
    semester: int


class SemesterCountOut(BaseModel):
    semester: int
    courses: int


class WeeklyEntryOut(BaseModel):
    offering_id: uuid.UUID
    course_code: str
    course_title: str
    teacher_name: str | None
    room: str
    section_label: str
    program_codes: list[str]
    # Where the course sits in the curriculum, e.g. BSN semester 3. This is what the Semester filter matches.
    placements: list["WeeklyPlacementOut"] = []


class WeeklyRowOut(BaseModel):
    key: str
    label: str
    start_time: time
    end_time: time
    period_number: int | None


class WeeklyCellOut(BaseModel):
    day: DayOfWeek
    row_key: str
    entries: list[WeeklyEntryOut]


class WeeklyTimetableOut(BaseModel):
    term_label: str | None
    title: str
    scope: str
    generated_on: date
    days: list[DayOfWeek]
    rows: list[WeeklyRowOut]
    cells: list[WeeklyCellOut]
    entries_count: int
    # Which semesters have classes on the timetable for the OTHER filters currently chosen
    # (so the page can say "nothing in Semester 2 - this class's classes are in Semester 3").
    semester_counts: list[SemesterCountOut] = []


class TimetableTermOption(BaseModel):
    label: str
    is_current: bool


class TimetableProgramOption(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    total_semesters: int


class TimetableTeacherOption(BaseModel):
    id: uuid.UUID
    name: str


class TimetableFilterOptions(BaseModel):
    allowed: list[str]
    terms: list[TimetableTermOption]
    default_term: str | None
    programs: list[TimetableProgramOption]
    sections: list[str]
    teachers: list[TimetableTeacherOption]


class ClassSessionResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    class_session_id: uuid.UUID
    resource_type: str
    original_name: str
    external_url: str | None


class LinkResourceCreateRequest(BaseModel):
    resource_type: LectureResourceType
    external_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=255)


class ClassSessionOut(BaseModel):
    id: uuid.UUID
    course_offering_id: uuid.UUID
    timetable_slot_id: uuid.UUID | None
    session_date: date
    start_time: time
    end_time: time
    room: str
    status: ClassSessionStatus
    cancelled_reason: str | None
    title: str | None
    description: str | None
    topic_completed: bool
    content_locked: bool = False
    late_marking_note: str | None
    resources: list[ClassSessionResourceOut] = []


class SetTopicCompletedRequest(BaseModel):
    completed: bool


class CourseProgressOut(BaseModel):
    course_offering_id: uuid.UUID
    total_sessions: int
    sessions_due: int
    sessions_completed: int
    sessions_missing: int
    completion_percentage: float
    status: str


class TeacherProgressEntry(BaseModel):
    teacher_id: uuid.UUID
    teacher_name: str
    employee_code: str
    course_offering_id: uuid.UUID
    course_code: str
    course_title: str
    term_label: str
    total_sessions: int
    sessions_due: int
    sessions_completed: int
    sessions_missing: int
    completion_percentage: float
    status: str


class ClassSessionUpdateRequest(BaseModel):
    session_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    room: str | None = Field(default=None, min_length=1, max_length=50)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class ContentLockRequest(BaseModel):
    """Lock or unlock what the assigned teacher may change.

    scope COURSE        -> the whole course: syllabus + every class (incl. future ones)
    scope PAST_CLASSES  -> only classes whose date has passed (teacher can still add to upcoming ones)
    scope ALL_CLASSES   -> every class individually (the course-level switch is untouched)
    """

    locked: bool
    scope: str = Field(default="COURSE", pattern="^(COURSE|PAST_CLASSES|ALL_CLASSES)$")


class ClassContentLockRequest(BaseModel):
    locked: bool


class ContentLockResult(BaseModel):
    content_locked: bool
    classes_changed: int


class LecturePlanItem(BaseModel):
    class_session_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)


class LecturePlanRequest(BaseModel):
    """Set the topic of many class sessions in one save (a teacher's course plan)."""

    items: list[LecturePlanItem] = Field(min_length=1, max_length=200)


class AutoScheduleTermRequest(BaseModel):
    term_label: str = Field(min_length=1, max_length=50)
    # Rooms to try, in order, for each course (a room already in use at that time is skipped).
    rooms: list[str] = Field(min_length=1, max_length=30)
    # Classes per week for every course. Empty = use each course's credit hours (1-5).
    sessions_per_week: int | None = Field(default=None, ge=1, le=5)


class CopyTimetableRequest(BaseModel):
    source_term: str = Field(min_length=1, max_length=50)
    target_term: str = Field(min_length=1, max_length=50)


class BulkTimetableResult(BaseModel):
    scheduled: list[dict]
    skipped: list[dict]
    failed: list[dict]


class ClassSessionCancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class CancelDayRequest(BaseModel):
    session_date: date
    reason: str = Field(min_length=1, max_length=255)
    term_label: str | None = Field(default=None, max_length=50)


class EndSemesterResponse(BaseModel):
    term_label: str
    offerings_closed: int
    enrollments_completed: int


class TeacherPerformanceEntry(BaseModel):
    teacher_id: uuid.UUID
    teacher_name: str
    employee_code: str
    offerings_count: int
    total_enrollments: int
    passed_count: int
    failed_count: int
    pass_rate: float | None  # null until at least one enrollment has a recorded outcome
    complaints_against: int


class AssessmentTypeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    max_marks: float = Field(gt=0)


class AssessmentTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str | None
    max_marks: float


class AddOfferingAssessmentRequest(BaseModel):
    assessment_type_id: uuid.UUID


class OfferingAssessmentOut(BaseModel):
    id: uuid.UUID
    course_offering_id: uuid.UUID
    assessment_type_id: uuid.UUID
    assessment_type_name: str
    category: str | None
    max_marks: float


class EnterMarkRequest(BaseModel):
    obtained_marks: float = Field(ge=0)
    # Required (and kept on record) when the administration corrects a mark in results that are already published.
    correction_reason: str | None = Field(default=None, max_length=500)


class StudentMarkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    enrollment_id: uuid.UUID
    offering_assessment_id: uuid.UUID
    obtained_marks: float
    entered_at: datetime


class StudentMarkDetail(StudentMarkOut):
    assessment_type_name: str
    category: str | None
    max_marks: float