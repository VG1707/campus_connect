"""
Deterministic Slot Matching and Ranking Engine for CampusConnect.
Strictly deterministic Python (no AI, no non-deterministic logic).
Respects all 3 availability layers, calendar day orders, capacity limits,
and schedule change verification rules.
"""

from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple, Dict
from pydantic import BaseModel
from sqlmodel import Session, select, and_, or_

from app.models import (
    User, Course, TimetableEntry, OfficeHour, MeetingRequest,
    ScheduleChange, RequestState, RequestSource, VerificationStatus,
    ScheduleChangeKind
)
from app.calendar_engine import (
    get_day_label_for_date, is_working_day, get_period_times,
    get_period_interval, parse_date, DEFAULT_PERIOD_TIMINGS
)


class MatchedSlot(BaseModel):
    """Structured representation of a recommended consultation slot."""
    slot_start: datetime
    slot_end: datetime
    duration_minutes: int
    source: str                          # "office_hour" (Layer 2) or "on_demand" (Layer 1)
    office_hour_id: Optional[int] = None
    faculty_id: int
    faculty_name: str
    location: str
    capacity: int
    current_bookings: int
    remaining_capacity: int
    can_auto_confirm: bool
    is_manual_mode: bool                 # True if student has no timetable loaded
    has_unverified_change: bool = False
    unverified_change_warning: Optional[str] = None
    score: float = 0.0
    ranking_reason: str = ""


class MatchingError(Exception):
    """Custom exception for scheduling rule violations (e.g. rate limit, blocked user)."""
    pass


def intervals_overlap(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    """Return True if two datetime intervals overlap."""
    return max(start1, start2) < min(end1, end2)


def get_student_class_intervals(
    session: Session,
    student_id: int,
    target_date: date,
    day_label: str
) -> List[Tuple[datetime, datetime, str]]:
    """
    Retrieve all class and lab time intervals for a student on a given calendar day.
    Respects lab blocks (which span multi-periods).
    """
    stmt = select(TimetableEntry).where(
        TimetableEntry.owner_id == student_id,
        TimetableEntry.day_order_or_weekday == day_label
    )
    entries = session.exec(stmt).all()
    intervals = []

    for entry in entries:
        dt_start, dt_end = get_period_interval(target_date, entry.period)
        intervals.append((dt_start, dt_end, f"Period {entry.period} ({entry.room})"))

    return intervals


def get_faculty_teaching_intervals(
    session: Session,
    faculty_id: int,
    target_date: date,
    day_label: str,
    verified_changes: List[ScheduleChange]
) -> List[Tuple[datetime, datetime, str]]:
    """
    Retrieve faculty teaching intervals on a calendar day,
    applying only VERIFIED schedule changes.
    """
    stmt = select(TimetableEntry).where(
        TimetableEntry.owner_id == faculty_id,
        TimetableEntry.day_order_or_weekday == day_label
    )
    entries = session.exec(stmt).all()

    # Map of period -> is_cancelled / moved
    cancelled_periods = set()
    extra_periods = []

    for chg in verified_changes:
        details = chg.details
        if details.get("date") == target_date.isoformat():
            if chg.kind == ScheduleChangeKind.CANCEL.value:
                cancelled_periods.add(details.get("period"))
            elif chg.kind == ScheduleChangeKind.SWAP.value:
                # Cancel original period and add new period
                cancelled_periods.add(details.get("original_period"))
                if details.get("new_period"):
                    extra_periods.append(details.get("new_period"))
            elif chg.kind == ScheduleChangeKind.EXTRA_CLASS.value:
                if details.get("period"):
                    extra_periods.append(details.get("period"))

    intervals = []
    for entry in entries:
        if entry.period in cancelled_periods:
            continue
        dt_start, dt_end = get_period_interval(target_date, entry.period)
        intervals.append((dt_start, dt_end, f"Teaching Period {entry.period}"))

    for p in extra_periods:
        if p in DEFAULT_PERIOD_TIMINGS:
            dt_start, dt_end = get_period_interval(target_date, p)
            intervals.append((dt_start, dt_end, f"Verified Extra Period {p}"))

    return intervals


def get_unverified_warnings_for_date(
    unverified_changes: List[ScheduleChange],
    target_date: date
) -> Optional[str]:
    """Check if there is an unverified schedule change pending for that date."""
    for chg in unverified_changes:
        details = chg.details
        if details.get("date") == target_date.isoformat():
            return f"Note: An unverified schedule change ({chg.kind}) was reported for this day. Availability may shift."
    return None


def calculate_slot_score(
    slot_start: datetime,
    window_start: datetime,
    window_end: datetime,
    remaining_capacity: int,
    capacity: int,
    can_auto_confirm: bool,
    student_class_intervals: List[Tuple[datetime, datetime, str]],
    is_on_demand: bool
) -> Tuple[float, str]:
    """
    Transparent scoring function (0-100 pts) with human-readable rationale.
    1. Closeness to preferred window center (0-50 pts).
    2. Campus idle gap with student's classes (0-30 pts).
    3. Capacity & auto-confirm ease (0-20 pts).
    """
    reasons = []

    # 1. Proximity score
    window_center = window_start + (window_end - window_start) / 2
    delta_seconds = abs((slot_start - window_center).total_seconds())
    max_delta = max((window_end - window_start).total_seconds() / 2, 3600)
    proximity_ratio = max(0.0, 1.0 - (delta_seconds / max_delta))
    proximity_pts = proximity_ratio * 50.0
    if proximity_ratio > 0.8:
        reasons.append("Optimal alignment with requested time")
    else:
        reasons.append("Within requested window")

    # 2. Campus idle gap
    gap_pts = 10.0  # default
    if student_class_intervals:
        min_gap_mins = float("inf")
        for c_start, c_end, _ in student_class_intervals:
            # Check gap if class ends before slot
            if c_end <= slot_start:
                gap = (slot_start - c_end).total_seconds() / 60
                if gap < min_gap_mins:
                    min_gap_mins = gap
            # Check gap if class starts after slot
            elif c_start >= slot_start:
                gap = (c_start - slot_start).total_seconds() / 60
                if gap < min_gap_mins:
                    min_gap_mins = gap

        if min_gap_mins <= 15:
            gap_pts = 30.0
            reasons.append(f"Zero idle gap ({int(min_gap_mins)}m after/before your class)")
        elif min_gap_mins <= 45:
            gap_pts = 20.0
            reasons.append(f"Short {int(min_gap_mins)}m campus break")
        else:
            gap_pts = 5.0
            reasons.append(f"{int(min_gap_mins)}m gap between classes")
    else:
        gap_pts = 15.0

    # 3. Capacity & Auto-confirm
    cap_pts = 0.0
    if is_on_demand:
        cap_pts = 5.0
        reasons.append("On-demand teaching gap (requires faculty review)")
    else:
        if can_auto_confirm:
            cap_pts += 10.0
            reasons.append("Auto-confirms instantly")
        else:
            reasons.append("Faculty confirmation required")

        if remaining_capacity > 1:
            cap_pts += 10.0
            reasons.append(f"{remaining_capacity} of {capacity} seats open")
        else:
            cap_pts += 5.0
            reasons.append(f"1 of {capacity} seats remaining")

    total_score = round(proximity_pts + gap_pts + cap_pts, 1)
    reason_str = "; ".join(reasons)
    return total_score, reason_str


def find_matching_slots(
    session: Session,
    student_id: int,
    faculty_id: int,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    include_on_demand: bool = True,
    current_time: Optional[datetime] = None
) -> List[MatchedSlot]:
    """
    Primary deterministic slot matching algorithm.
    Validates rules, iterates academic calendar, checks teacher & student schedules,
    and returns transparently ranked consultation slots.
    """
    now = current_time or datetime.now()

    # Rule 1: Validate duration
    if duration_minutes < 5 or duration_minutes > 50 or (duration_minutes % 5 != 0):
        raise MatchingError("Meeting duration must be between 5 and 50 minutes, in 5-minute increments.")

    # Rule 2: Check student status (no-show lock & rate limit)
    student = session.get(User, student_id)
    if not student:
        raise MatchingError("Student record not found.")

    if student.is_blocked or student.no_show_count >= 2:
        raise MatchingError(
            f"Booking blocked: You have {student.no_show_count} recorded no-shows. "
            "Please contact an administrator to reset your account."
        )

    # Rate limit: max 3 pending requests per student
    stmt_pending = select(MeetingRequest).where(
        MeetingRequest.student_id == student_id,
        MeetingRequest.state == RequestState.PENDING.value
    )
    pending_count = len(session.exec(stmt_pending).all())
    if pending_count >= 3:
        raise MatchingError("Rate limit reached: You already have 3 pending consultation requests.")

    faculty = session.get(User, faculty_id)
    if not faculty:
        raise MatchingError("Faculty record not found.")

    # Rule 3: Check student timetable status
    stmt_tt = select(TimetableEntry).where(TimetableEntry.owner_id == student_id)
    student_timetable_entries = session.exec(stmt_tt).all()
    student_has_timetable = len(student_timetable_entries) > 0

    # Rule 4: Retrieve schedule changes (verified vs unverified)
    stmt_changes = select(ScheduleChange).where(
        ScheduleChange.owner_id == faculty_id
    )
    faculty_changes = session.exec(stmt_changes).all()
    verified_changes = [c for c in faculty_changes if c.verification == VerificationStatus.VERIFIED.value]
    unverified_changes = [c for c in faculty_changes if c.verification == VerificationStatus.UNVERIFIED.value]

    # Pre-fetch existing confirmed meetings for faculty and student
    stmt_student_meetings = select(MeetingRequest).where(
        MeetingRequest.student_id == student_id,
        MeetingRequest.state.in_([RequestState.CONFIRMED.value, RequestState.PENDING.value])
    )
    student_existing_meetings = session.exec(stmt_student_meetings).all()

    stmt_faculty_meetings = select(MeetingRequest).where(
        MeetingRequest.faculty_id == faculty_id,
        MeetingRequest.state == RequestState.CONFIRMED.value
    )
    faculty_confirmed_meetings = session.exec(stmt_faculty_meetings).all()

    # Pre-fetch office hours for faculty (Layer 2)
    stmt_oh = select(OfficeHour).where(OfficeHour.faculty_id == faculty_id)
    office_hours = session.exec(stmt_oh).all()

    matched_slots: List[MatchedSlot] = []

    # Iterate day by day in the requested window
    cur_date = window_start.date()
    end_date = window_end.date()

    while cur_date <= end_date:
        # Check if date is working day (not Sunday, not holiday, not exam day)
        if not is_working_day(session, cur_date):
            cur_date += timedelta(days=1)
            continue

        day_label = get_day_label_for_date(session, cur_date)
        if not day_label:
            cur_date += timedelta(days=1)
            continue

        # Get student class intervals for this day (if timetable exists)
        student_classes = []
        if student_has_timetable:
            student_classes = get_student_class_intervals(session, student_id, cur_date, day_label)

        # Get faculty teaching intervals (respecting verified changes)
        faculty_teaching = get_faculty_teaching_intervals(
            session, faculty_id, cur_date, day_label, verified_changes
        )

        unverified_warning = get_unverified_warnings_for_date(unverified_changes, cur_date)

        # -------------------------------------------------------------
        # LAYER 2: Evaluate Faculty Office Hours
        # -------------------------------------------------------------
        for oh in office_hours:
            # Check recurrence match: e.g. "day_order:Day 3" or "weekday:Thursday"
            matches_recurrence = False
            rec = oh.recurrence_pattern
            if ":" in rec:
                kind, val = rec.split(":", 1)
                if kind == "day_order" and val.strip() == day_label:
                    matches_recurrence = True
                elif kind == "weekday" and val.strip().lower() == cur_date.strftime("%A").lower():
                    matches_recurrence = True
            elif rec.strip() == day_label or rec.strip().lower() == cur_date.strftime("%A").lower():
                matches_recurrence = True

            if not matches_recurrence:
                continue

            # Check paused dates
            if cur_date.isoformat() in oh.paused_dates:
                continue

            # Check allowed durations
            if duration_minutes not in oh.allowed_durations:
                continue

            # Parse office hour time range
            oh_sh, oh_sm = map(int, oh.start_time.split(":"))
            oh_eh, oh_em = map(int, oh.end_time.split(":"))
            oh_start_dt = datetime.combine(cur_date, time(oh_sh, oh_sm))
            oh_end_dt = datetime.combine(cur_date, time(oh_eh, oh_em))

            # Flag if office hour conflicts with faculty's own teaching periods
            oh_overlaps_teaching = any(
                intervals_overlap(oh_start_dt, oh_end_dt, t_start, t_end)
                for t_start, t_end, _ in faculty_teaching
            )
            if oh_overlaps_teaching:
                # Faculty scheduled an office hour during a class period -> skip/drop
                continue

            # Generate candidate sub-slots of length `duration_minutes` (stepping in 5-min increments)
            candidate_start = oh_start_dt
            step = timedelta(minutes=5)
            slot_delta = timedelta(minutes=duration_minutes)

            while candidate_start + slot_delta <= oh_end_dt:
                candidate_end = candidate_start + slot_delta

                # Drop expired past slots
                if candidate_start <= now:
                    candidate_start += step
                    continue

                # Ensure slot falls within student's requested window
                if candidate_start < window_start or candidate_end > window_end:
                    candidate_start += step
                    continue

                # Conflict Check A: Student timetable class/lab
                if student_has_timetable:
                    has_class_clash = any(
                        intervals_overlap(candidate_start, candidate_end, c_start, c_end)
                        for c_start, c_end, _ in student_classes
                    )
                    if has_class_clash:
                        candidate_start += step
                        continue

                # Conflict Check B: Student existing meetings
                has_student_meeting_clash = any(
                    intervals_overlap(
                        candidate_start, candidate_end,
                        datetime.fromisoformat(m.slot_start),
                        datetime.fromisoformat(m.slot_end)
                    )
                    for m in student_existing_meetings
                )
                if has_student_meeting_clash:
                    candidate_start += step
                    continue

                # Conflict Check C: Faculty existing confirmed meetings & capacity
                current_active_bookings = 0
                for fm in faculty_confirmed_meetings:
                    fm_start = datetime.fromisoformat(fm.slot_start)
                    fm_end = datetime.fromisoformat(fm.slot_end)
                    if intervals_overlap(candidate_start, candidate_end, fm_start, fm_end):
                        current_active_bookings += 1

                if current_active_bookings >= oh.capacity:
                    # Slot is full
                    candidate_start += step
                    continue

                remaining_cap = oh.capacity - current_active_bookings
                can_auto = oh.auto_confirm and (remaining_cap > 0)

                score, reason = calculate_slot_score(
                    slot_start=candidate_start,
                    window_start=window_start,
                    window_end=window_end,
                    remaining_capacity=remaining_cap,
                    capacity=oh.capacity,
                    can_auto_confirm=can_auto,
                    student_class_intervals=student_classes,
                    is_on_demand=False
                )

                matched_slots.append(MatchedSlot(
                    slot_start=candidate_start,
                    slot_end=candidate_end,
                    duration_minutes=duration_minutes,
                    source=RequestSource.OFFICE_HOUR.value,
                    office_hour_id=oh.id,
                    faculty_id=faculty.id,
                    faculty_name=faculty.name,
                    location=oh.location,
                    capacity=oh.capacity,
                    current_bookings=current_active_bookings,
                    remaining_capacity=remaining_cap,
                    can_auto_confirm=can_auto,
                    is_manual_mode=not student_has_timetable,
                    has_unverified_change=(unverified_warning is not None),
                    unverified_change_warning=unverified_warning,
                    score=score,
                    ranking_reason=reason
                ))

                candidate_start += step

        # -------------------------------------------------------------
        # LAYER 1: Timetable Prediction ("Likely Free" on-demand)
        # -------------------------------------------------------------
        if include_on_demand:
            # Check standard periods (1 to 8)
            for period_num in range(1, 9):
                p_start, p_end = get_period_interval(cur_date, period_num)

                # Skip if in past or outside requested window
                if p_start <= now or p_start < window_start or p_end > window_end:
                    continue

                # Check if faculty has a teaching class during this period
                faculty_busy = any(
                    intervals_overlap(p_start, p_end, t_start, t_end)
                    for t_start, t_end, _ in faculty_teaching
                )
                if faculty_busy:
                    continue

                # Check if this overlaps with any established office hour
                overlaps_oh = any(
                    intervals_overlap(p_start, p_end,
                                      datetime.combine(cur_date, time(*map(int, oh.start_time.split(":")))),
                                      datetime.combine(cur_date, time(*map(int, oh.end_time.split(":")))))
                    for oh in office_hours
                    if (cur_date.isoformat() not in oh.paused_dates)
                )
                if overlaps_oh:
                    continue

                # Check student availability
                if student_has_timetable:
                    if any(intervals_overlap(p_start, p_start + timedelta(minutes=duration_minutes), c_start, c_end)
                           for c_start, c_end, _ in student_classes):
                        continue

                # Faculty has no class here -> Layer 1 "Likely free"
                candidate_start = p_start
                candidate_end = p_start + timedelta(minutes=duration_minutes)

                score, reason = calculate_slot_score(
                    slot_start=candidate_start,
                    window_start=window_start,
                    window_end=window_end,
                    remaining_capacity=1,
                    capacity=1,
                    can_auto_confirm=False, # Layer 1 is NEVER auto-confirmed!
                    student_class_intervals=student_classes,
                    is_on_demand=True
                )

                matched_slots.append(MatchedSlot(
                    slot_start=candidate_start,
                    slot_end=candidate_end,
                    duration_minutes=duration_minutes,
                    source=RequestSource.ON_DEMAND.value,
                    office_hour_id=None,
                    faculty_id=faculty.id,
                    faculty_name=faculty.name,
                    location="Faculty Cabin (To be confirmed)",
                    capacity=1,
                    current_bookings=0,
                    remaining_capacity=1,
                    can_auto_confirm=False, # Always requires faculty approval
                    is_manual_mode=not student_has_timetable,
                    has_unverified_change=(unverified_warning is not None),
                    unverified_change_warning=unverified_warning,
                    score=score,
                    ranking_reason=reason
                ))

        cur_date += timedelta(days=1)

    # Sort descending by score
    matched_slots.sort(key=lambda s: s.score, reverse=True)
    return matched_slots
