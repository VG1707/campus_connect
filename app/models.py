"""
Data models for CampusConnect.
Uses SQLModel to combine Pydantic validation and SQLAlchemy table definitions.
All entities are beginner-friendly, strongly typed, and heavily commented.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List
import json
from sqlmodel import SQLModel, Field, Relationship


class UserRole(str, Enum):
    """Possible user roles in the mock authentication system."""
    STUDENT = "student"
    FACULTY = "faculty"
    ADMIN = "admin"


class RequestType(str, Enum):
    """Categorization of what the student needs assistance with."""
    DOUBT = "doubt"
    PROJECT = "project"
    ASSIGNMENT = "assignment"
    OTHER = "other"


class RequestState(str, Enum):
    """Lifecycle states of a consultation request."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    COUNTERED = "countered"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class RequestSource(str, Enum):
    """Origin of the meeting slot."""
    OFFICE_HOUR = "office_hour"   # Faculty commitment (Layer 2)
    ON_DEMAND = "on_demand"       # Free teaching period prediction (Layer 1)


class ScheduleChangeKind(str, Enum):
    """Types of temporary schedule alterations."""
    SWAP = "swap"
    CANCEL = "cancel"
    EXTRA_CLASS = "extra_class"
    HOLIDAY = "holiday"


class VerificationStatus(str, Enum):
    """Trust level of a reported schedule change."""
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


class GroupSessionState(str, Enum):
    """State machine for suggested group consultations."""
    SUGGESTED = "suggested"
    HOSTED = "hosted"
    DECLINED = "declined"


# ==========================================
# Core Tables
# ==========================================

class User(SQLModel, table=True):
    """
    Represents a student, faculty member, or campus administrator.
    Includes rate-limiting counters and no-show blocking.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    role: str = Field(index=True)                    # UserRole value: student, faculty, admin
    name: str                                        # e.g., "Meera Nair", "Dr. Rajesh Raman"
    department: str                                  # e.g., "Computer Science & Engineering"
    email: str = Field(index=True, unique=True)
    section: Optional[str] = None                    # e.g., "CSE-A" for students
    semester: Optional[int] = None                   # e.g., 3 for 2nd-year students
    no_show_count: int = Field(default=0)            # 2 no-shows trigger automated booking block
    is_blocked: bool = Field(default=False)          # Admin reset flag


class Course(SQLModel, table=True):
    """
    Academic course with associated subject topics.
    Topic tags power smart doubt grouping without using AI.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)        # e.g., "CS201"
    name: str                                        # e.g., "Data Structures and Algorithms"
    department: str                                  # e.g., "Computer Science & Engineering"
    topic_tags_json: str = Field(default="[]")       # JSON list: '["Binary Trees", "Graphs", "Sorting"]'

    @property
    def topic_tags(self) -> List[str]:
        """Convenience helper to read topic tags as a Python list."""
        try:
            return json.loads(self.topic_tags_json)
        except Exception:
            return []

    def set_topic_tags(self, tags: List[str]) -> None:
        """Convenience helper to store topic tags as a JSON string."""
        self.topic_tags_json = json.dumps(tags)


class FacultyCourse(SQLModel, table=True):
    """Association table linking which faculty teaches which course/section."""
    id: Optional[int] = Field(default=None, primary_key=True)
    faculty_id: int = Field(foreign_key="user.id", index=True)
    course_id: int = Field(foreign_key="course.id", index=True)
    section: Optional[str] = None                    # e.g., "CSE-A"


class AcademicCalendar(SQLModel, table=True):
    """
    Configurable academic calendar.
    Supports either plain weekday mode or rotating day-order mode (Day 1 through Day 6).
    Holidays and exam days are flagged and skipped by the matching engine.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(index=True, unique=True)       # ISO format: "YYYY-MM-DD"
    day_order: Optional[int] = None                  # 1 to 6, or None for Sunday/Holiday
    is_holiday: bool = Field(default=False)
    is_exam: bool = Field(default=False)
    note: Optional[str] = None                       # e.g., "Diwali", "Mid-term Exam 1"


class SystemConfig(SQLModel, table=True):
    """Key-value table for persistent system runtime configuration."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: str


class TimetableEntry(SQLModel, table=True):
    """
    A single scheduled class/lab period in an owner's regular weekly timetable.
    Owned by a student or a faculty member.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True)
    day_order_or_weekday: str = Field(index=True)    # e.g., "Day 1", "Day 3" OR "Monday", "Thursday"
    period: int                                      # 1 through 8 (e.g., P1 to P8)
    course_id: Optional[int] = Field(default=None, foreign_key="course.id")
    room: str                                        # e.g., "LH-201", "CS-Lab-2"
    section: Optional[str] = None                    # e.g., "CSE-A"
    is_lab_block: bool = Field(default=False)        # Multi-period laboratory session


class OfficeHour(SQLModel, table=True):
    """
    Layer 2: Faculty commitment.
    Recurring consultation window published by a faculty member.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    faculty_id: int = Field(foreign_key="user.id", index=True)
    recurrence_pattern: str                          # e.g., "day_order:Day 3" or "weekday:Thursday"
    start_time: str                                  # "14:00"
    end_time: str                                    # "15:00"
    location: str                                    # e.g., "Cabin 304, Academic Block A"
    capacity: int = Field(default=1)                 # Max simultaneous students (1 for 1:1, up to 5)
    allowed_durations_json: str = Field(default="[15, 30]") # Durations 5-50 min in 5-min steps
    auto_confirm: bool = Field(default=True)         # Auto-confirms if capacity remains
    paused_dates_json: str = Field(default="[]")     # JSON list of "YYYY-MM-DD" dates

    @property
    def allowed_durations(self) -> List[int]:
        """Parsed list of allowed consultation lengths in minutes."""
        try:
            return json.loads(self.allowed_durations_json)
        except Exception:
            return [15, 30]

    @property
    def paused_dates(self) -> List[str]:
        """Parsed list of paused calendar dates."""
        try:
            return json.loads(self.paused_dates_json)
        except Exception:
            return []


class GroupSession(SQLModel, table=True):
    """
    Automated cohort doubt sessions.
    Suggested when >= threshold students share a topic and are free in that exact slot.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    faculty_id: int = Field(foreign_key="user.id", index=True)
    office_hour_id: int = Field(foreign_key="officehour.id", index=True)
    occurrence_date: str = Field(index=True)         # "YYYY-MM-DD"
    slot_start: str                                  # ISO string: "2026-10-22T14:00:00"
    slot_end: str                                    # ISO string: "2026-10-22T15:00:00"
    topic_tag: str
    capacity: int = Field(default=4)
    state: str = Field(default=GroupSessionState.SUGGESTED.value) # suggested, hosted, declined
    member_student_ids_json: str = Field(default="[]")
    created_at: str

    @property
    def member_student_ids(self) -> List[int]:
        try:
            return json.loads(self.member_student_ids_json)
        except Exception:
            return []


class MeetingRequest(SQLModel, table=True):
    """
    A student consultation booking or pending inquiry.
    Contains full state lifecycle, transparent source, and timestamps.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id", index=True)
    faculty_id: int = Field(foreign_key="user.id", index=True)
    course_id: Optional[int] = Field(default=None, foreign_key="course.id")
    topic_tag: Optional[str] = None
    type: str = Field(default=RequestType.DOUBT.value) # doubt, project, assignment, other
    description: str
    preferred_window_start: str                      # ISO string
    preferred_window_end: str                        # ISO string
    slot_start: str = Field(index=True)              # ISO string
    slot_end: str                                    # ISO string
    duration: int = Field(default=15)                # minutes (5-50 in 5-min increments)
    state: str = Field(default=RequestState.PENDING.value, index=True)
    source: str = Field(default=RequestSource.OFFICE_HOUR.value) # office_hour vs on_demand
    location: str = ""
    counter_note: Optional[str] = None
    group_session_id: Optional[int] = Field(default=None, foreign_key="groupsession.id")
    created_at: str
    updated_at: str


class ScheduleChange(SQLModel, table=True):
    """
    Reports of timetable swaps, cancellations, or extra classes.
    Faculty reports are auto-verified; student reports require faculty/admin confirmation.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True) # affected faculty or student
    kind: str = Field(default=ScheduleChangeKind.SWAP.value)
    details_json: str                                # JSON describing date, periods, course
    verification: str = Field(default=VerificationStatus.UNVERIFIED.value, index=True)
    reported_by: str = Field(default="faculty")       # faculty, student, admin
    created_at: str

    @property
    def details(self) -> dict:
        try:
            return json.loads(self.details_json)
        except Exception:
            return {}


class ExtractionLog(SQLModel, table=True):
    """
    Anonymous metrics log for timetable extraction review screen.
    Records only numerical counts and timing: zero image data or student identifiers.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id", index=True)
    source_type: str = Field(default="mock")         # "mock" or "gemini_vision"
    cells_extracted: int = Field(default=0)
    cells_edited: int = Field(default=0)
    seconds_to_save: float = Field(default=0.0)
    created_at: str


class Notification(SQLModel, table=True):
    """System notification for user updates (confirmations, changes, suggestions)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    text: str
    read: bool = Field(default=False)
    created_at: str


class AuditLog(SQLModel, table=True):
    """Immutable audit trail for every booking, status change, or cancellation."""
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: str = Field(index=True)
    actor_id: Optional[int] = Field(default=None, foreign_key="user.id")
    action: str                                      # e.g., "AUTO_CONFIRM", "REQUEST_CREATED"
    entity_type: str                                 # e.g., "MeetingRequest", "OfficeHour"
    entity_id: Optional[int] = None
    details: str
