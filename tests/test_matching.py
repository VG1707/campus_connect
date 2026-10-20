"""
Comprehensive deterministic pytest suite for the CampusConnect Matching Engine.
Verifies all 10 core scheduling & availability requirements:
1. Student class conflict
2. Multi-period lab block conflict
3. Student existing booking conflict
4. Expiry / past slots dropped
5. Duration fit (5-50 min in 5-min increments)
6. Capacity limits and auto-confirm behavior
7. Holiday shift of day orders
8. Unverified change warning vs verified change recalculation
9. On-demand slots never auto-confirmed
10. Optional timetable mode (manual selection when no timetable is uploaded)
11. Rate limit (max 3 pending requests)
12. No-show blocking (2 no-shows blocks booking)
"""

from datetime import datetime, date, timedelta
import pytest
from sqlmodel import Session, select

from app.models import (
    User, Course, OfficeHour, TimetableEntry, MeetingRequest,
    ScheduleChange, RequestState, RequestSource, VerificationStatus,
    ScheduleChangeKind
)
from app.matching import find_matching_slots, MatchingError
from app.calendar_engine import shift_calendar_on_holiday


def get_user_by_email(session: Session, email: str) -> User:
    """Helper to fetch a user fixture by email."""
    stmt = select(User).where(User.email == email)
    return session.exec(stmt).one()


# ---------------------------------------------------------------------------
# Test 1 & 2: Student Class & Lab Block Conflicts
# ---------------------------------------------------------------------------

def test_student_class_conflict(session: Session):
    """
    Meera has CS203 Digital Logic class on Day 3 Period 1 (08:30-09:20).
    A consultation slot at 08:45-09:00 on Day 3 MUST be excluded.
    """
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    # Day 3 date is 2026-10-21
    # Try finding slots during morning Period 1: 08:30 to 09:30
    w_start = datetime(2026, 10, 21, 8, 30)
    w_end = datetime(2026, 10, 21, 9, 30)

    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=15,
        window_start=w_start,
        window_end=w_end,
        include_on_demand=True,
        current_time=datetime(2026, 10, 20, 8, 0)
    )

    # All slots overlapping Period 1 (08:30-09:20) must be filtered out for Meera
    for s in slots:
        assert not (s.slot_start < datetime(2026, 10, 21, 9, 20) and s.slot_end > datetime(2026, 10, 21, 8, 30))


def test_student_lab_block_conflict(session: Session):
    """
    Arjun (Student E) has EC204 Lab on Day 3 spanning Period 5 & 6 (13:00 to 14:45).
    Dr. Raman has office hours from 14:00 to 15:00 on Day 3.
    Slots between 14:00 and 14:45 must be blocked for Arjun.
    Only slots from 14:45 to 15:00 are available for Arjun.
    """
    arjun = get_user_by_email(session, "arjun.rao@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    w_start = datetime(2026, 10, 21, 14, 0)
    w_end = datetime(2026, 10, 21, 15, 0)

    slots = find_matching_slots(
        session=session,
        student_id=arjun.id,
        faculty_id=raman.id,
        duration_minutes=15,
        window_start=w_start,
        window_end=w_end,
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )

    # For Arjun, slots between 14:00 and 14:45 should NOT exist
    for s in slots:
        assert s.slot_start >= datetime(2026, 10, 21, 14, 45)


# ---------------------------------------------------------------------------
# Test 3: Existing Student Booking Overlap Prevention
# ---------------------------------------------------------------------------

def test_existing_student_booking_conflict(session: Session):
    """
    A student who already has a confirmed or pending booking at 14:00-14:30
    cannot be offered an overlapping slot.
    """
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    # In seed_data, Meera already has a pending request at 14:00-14:30 on 2026-10-21
    w_start = datetime(2026, 10, 21, 14, 0)
    w_end = datetime(2026, 10, 21, 15, 0)

    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=15,
        window_start=w_start,
        window_end=w_end,
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )

    # Slots overlapping 14:00 to 14:30 should NOT be in the results for Meera
    for s in slots:
        assert not (s.slot_start < datetime(2026, 10, 21, 14, 30) and s.slot_end > datetime(2026, 10, 21, 14, 0))


# ---------------------------------------------------------------------------
# Test 4: Expiry (Past Slots Dropped)
# ---------------------------------------------------------------------------

def test_past_slots_dropped(session: Session):
    """Slots earlier than current_time must be strictly dropped."""
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    # Current time is 14:20 on Day 3 (2026-10-21)
    mock_now = datetime(2026, 10, 21, 14, 20)
    w_start = datetime(2026, 10, 21, 14, 0)
    w_end = datetime(2026, 10, 21, 15, 0)

    # Clean existing meetings for Meera for this isolated check
    for m in session.exec(select(MeetingRequest).where(MeetingRequest.student_id == meera.id)).all():
        session.delete(m)
    session.commit()

    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=15,
        window_start=w_start,
        window_end=w_end,
        include_on_demand=False,
        current_time=mock_now
    )

    for s in slots:
        assert s.slot_start > mock_now


# ---------------------------------------------------------------------------
# Test 5: Duration Fit (5-50 min in 5-min increments)
# ---------------------------------------------------------------------------

def test_duration_validation_and_fit(session: Session):
    """Invalid durations (e.g., 7 min, 55 min) must raise MatchingError."""
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    w_start = datetime(2026, 10, 21, 14, 0)
    w_end = datetime(2026, 10, 21, 15, 0)

    # 7 min is not a multiple of 5
    with pytest.raises(MatchingError, match="in 5-minute increments"):
        find_matching_slots(
            session=session,
            student_id=meera.id,
            faculty_id=raman.id,
            duration_minutes=7,
            window_start=w_start,
            window_end=w_end
        )

    # 55 min exceeds 50 min maximum
    with pytest.raises(MatchingError, match="between 5 and 50 minutes"):
        find_matching_slots(
            session=session,
            student_id=meera.id,
            faculty_id=raman.id,
            duration_minutes=55,
            window_start=w_start,
            window_end=w_end
        )


# ---------------------------------------------------------------------------
# Test 6: Capacity Limits and Auto-Confirm Behavior
# ---------------------------------------------------------------------------

def test_capacity_limits_and_auto_confirm(session: Session):
    """
    Office hour has capacity=3 and auto_confirm=True.
    When 2 seats are filled, remaining_capacity is 1, and can_auto_confirm is True.
    When all 3 seats are filled, the slot is full and disappears.
    """
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    # Clear existing requests to start fresh
    for m in session.exec(select(MeetingRequest)).all():
        session.delete(m)
    session.commit()

    slot_time_start = datetime(2026, 10, 21, 14, 0)
    slot_time_end = datetime(2026, 10, 21, 14, 30)

    # 1. Check initially: capacity=3, remaining=3, can_auto_confirm=True
    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=30,
        window_start=slot_time_start,
        window_end=slot_time_end,
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )
    assert len(slots) == 1
    assert slots[0].remaining_capacity == 3
    assert slots[0].can_auto_confirm is True

    # 2. Add 2 confirmed meetings from other students
    rohan = get_user_by_email(session, "rohan.verma@campusconnect.edu")
    karthik = get_user_by_email(session, "karthik.s@campusconnect.edu")

    session.add(MeetingRequest(
        student_id=rohan.id, faculty_id=raman.id, description="Booking 1",
        preferred_window_start=slot_time_start.isoformat(), preferred_window_end=slot_time_end.isoformat(),
        slot_start=slot_time_start.isoformat(), slot_end=slot_time_end.isoformat(),
        duration=30, state=RequestState.CONFIRMED.value, source=RequestSource.OFFICE_HOUR.value,
        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat()
    ))
    session.add(MeetingRequest(
        student_id=karthik.id, faculty_id=raman.id, description="Booking 2",
        preferred_window_start=slot_time_start.isoformat(), preferred_window_end=slot_time_end.isoformat(),
        slot_start=slot_time_start.isoformat(), slot_end=slot_time_end.isoformat(),
        duration=30, state=RequestState.CONFIRMED.value, source=RequestSource.OFFICE_HOUR.value,
        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat()
    ))
    session.commit()

    # Query again for Meera: remaining capacity must now be 1
    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=30,
        window_start=slot_time_start,
        window_end=slot_time_end,
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )
    assert len(slots) == 1
    assert slots[0].remaining_capacity == 1
    assert slots[0].can_auto_confirm is True

    # 3. Add 3rd confirmed booking to reach capacity limit of 3
    arjun = get_user_by_email(session, "arjun.rao@campusconnect.edu")
    session.add(MeetingRequest(
        student_id=arjun.id, faculty_id=raman.id, description="Booking 3",
        preferred_window_start=slot_time_start.isoformat(), preferred_window_end=slot_time_end.isoformat(),
        slot_start=slot_time_start.isoformat(), slot_end=slot_time_end.isoformat(),
        duration=30, state=RequestState.CONFIRMED.value, source=RequestSource.OFFICE_HOUR.value,
        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat()
    ))
    session.commit()

    # Query again for Meera: slot is full, so 0 slots available
    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=30,
        window_start=slot_time_start,
        window_end=slot_time_end,
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )
    assert len(slots) == 0


def test_auto_confirm_false_flag(session: Session):
    """
    Prof. Vikram Iyer has auto_confirm=False.
    Matching slots must return can_auto_confirm=False so the request stays pending.
    """
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    iyer = get_user_by_email(session, "vikram.iyer@campusconnect.edu")

    # In seed_data, Day 4 is 2026-10-23. Office hour: 15:00-16:00, allowed duration [20, 40]
    w_start = datetime(2026, 10, 23, 15, 0)
    w_end = datetime(2026, 10, 23, 16, 0)

    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=iyer.id,
        duration_minutes=20,
        window_start=w_start,
        window_end=w_end,
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )
    assert len(slots) > 0
    for s in slots:
        assert s.can_auto_confirm is False


# ---------------------------------------------------------------------------
# Test 7: Holiday Shift of Day Order
# ---------------------------------------------------------------------------

def test_holiday_shifts_office_hour_slots(session: Session):
    """
    If 2026-10-21 (originally Day 3) is declared a holiday,
    no slots are available on 2026-10-21.
    Day 3 office hours shift to the next working day (2026-10-23)!
    """
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    # Clear existing requests
    for m in session.exec(select(MeetingRequest)).all():
        session.delete(m)
    session.commit()

    # Declare holiday on 2026-10-21
    shift_calendar_on_holiday(session, "2026-10-21", note="Monsoon Storm Holiday")

    # 1. On 2026-10-21: 0 slots must match
    slots_on_holiday = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=30,
        window_start=datetime(2026, 10, 21, 14, 0),
        window_end=datetime(2026, 10, 21, 15, 0),
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )
    assert len(slots_on_holiday) == 0

    # 2. On 2026-10-23 (which shifted to Day 3): Day 3 office hour is now active!
    slots_on_shifted_day = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=30,
        window_start=datetime(2026, 10, 23, 14, 0),
        window_end=datetime(2026, 10, 23, 15, 0),
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )
    assert len(slots_on_shifted_day) > 0
    assert slots_on_shifted_day[0].slot_start.date() == date(2026, 10, 23)


# ---------------------------------------------------------------------------
# Test 8: Unverified vs Verified Schedule Change
# ---------------------------------------------------------------------------

def test_unverified_vs_verified_schedule_change(session: Session):
    """
    - Unverified change: flags a clear warning badge on the slot, but does not alter schedule.
    - Verified change: alters the schedule calculation immediately.
    """
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    # Clear requests
    for m in session.exec(select(MeetingRequest)).all():
        session.delete(m)
    session.commit()

    # 1. Add UNVERIFIED change reported by a student
    unverified_change = ScheduleChange(
        owner_id=raman.id,
        kind=ScheduleChangeKind.SWAP.value,
        details_json='{"date": "2026-10-21", "original_period": 4, "new_period": 6}',
        verification=VerificationStatus.UNVERIFIED.value,
        reported_by="student",
        created_at=datetime.now().isoformat()
    )
    session.add(unverified_change)
    session.commit()

    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=30,
        window_start=datetime(2026, 10, 21, 14, 0),
        window_end=datetime(2026, 10, 21, 15, 0),
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )

    # Slots still exist, but must be flagged with unverified warning
    assert len(slots) > 0
    assert slots[0].has_unverified_change is True
    assert "unverified schedule change" in slots[0].unverified_change_warning.lower()

    # 2. Now mark the change as VERIFIED (faculty swapped class into Period 6: 13:55-14:45!)
    unverified_change.verification = VerificationStatus.VERIFIED.value
    session.add(unverified_change)
    session.commit()

    slots_after_verification = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=raman.id,
        duration_minutes=30,
        window_start=datetime(2026, 10, 21, 14, 0),
        window_end=datetime(2026, 10, 21, 15, 0),
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )

    # Because Dr. Raman's verified class now runs during Period 6 (13:55-14:45),
    # office hour slots between 14:00-14:30 must be dropped!
    for s in slots_after_verification:
        assert s.slot_start >= datetime(2026, 10, 21, 14, 45)


# ---------------------------------------------------------------------------
# Test 9: On-Demand Slots Never Auto-Confirm
# ---------------------------------------------------------------------------

def test_on_demand_never_auto_confirms(session: Session):
    """Layer 1 on-demand suggestions must NEVER auto-confirm."""
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    meenakshi = get_user_by_email(session, "meenakshi.k@campusconnect.edu")

    # Dr. Meenakshi has NO office hours seeded (on-demand only)
    w_start = datetime(2026, 10, 21, 8, 30)
    w_end = datetime(2026, 10, 21, 12, 0)

    slots = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=meenakshi.id,
        duration_minutes=15,
        window_start=w_start,
        window_end=w_end,
        include_on_demand=True,
        current_time=datetime(2026, 10, 20, 8, 0)
    )

    assert len(slots) > 0
    for s in slots:
        if s.source == RequestSource.ON_DEMAND.value:
            assert s.can_auto_confirm is False
            assert "On-demand teaching gap" in s.ranking_reason


# ---------------------------------------------------------------------------
# Test 10: Optional Student Timetable Mode
# ---------------------------------------------------------------------------

def test_optional_student_timetable_mode(session: Session):
    """
    Divya (Student D) has NO timetable seeded.
    Matching must succeed without errors, show all valid office hours,
    and set is_manual_mode=True so the UI can display the active mode badge.
    """
    divya = get_user_by_email(session, "divya.patel@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    # Verify Divya has no timetable entries
    tt_count = len(session.exec(select(TimetableEntry).where(TimetableEntry.owner_id == divya.id)).all())
    assert tt_count == 0

    slots = find_matching_slots(
        session=session,
        student_id=divya.id,
        faculty_id=raman.id,
        duration_minutes=30,
        window_start=datetime(2026, 10, 21, 14, 0),
        window_end=datetime(2026, 10, 21, 15, 0),
        include_on_demand=False,
        current_time=datetime(2026, 10, 20, 8, 0)
    )

    assert len(slots) > 0
    for s in slots:
        assert s.is_manual_mode is True


# ---------------------------------------------------------------------------
# Test 11 & 12: Rate Limits and No-Show Blocking
# ---------------------------------------------------------------------------

def test_rate_limit_max_three_pending_requests(session: Session):
    """A student with 3 active pending requests must be blocked from requesting another."""
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    # Clear and set exactly 3 pending requests for Meera
    for m in session.exec(select(MeetingRequest).where(MeetingRequest.student_id == meera.id)).all():
        session.delete(m)
    session.commit()

    for i in range(3):
        session.add(MeetingRequest(
            student_id=meera.id, faculty_id=raman.id, description=f"Pending {i+1}",
            preferred_window_start="2026-10-21T14:00:00", preferred_window_end="2026-10-21T15:00:00",
            slot_start="2026-10-21T14:00:00", slot_end="2026-10-21T14:15:00",
            duration=15, state=RequestState.PENDING.value, source=RequestSource.OFFICE_HOUR.value,
            created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat()
        ))
    session.commit()

    with pytest.raises(MatchingError, match="Rate limit reached"):
        find_matching_slots(
            session=session,
            student_id=meera.id,
            faculty_id=raman.id,
            duration_minutes=15,
            window_start=datetime(2026, 10, 21, 14, 0),
            window_end=datetime(2026, 10, 21, 15, 0)
        )


def test_no_show_blocking(session: Session):
    """A student with 2 no-shows must be blocked from booking."""
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")

    meera.no_show_count = 2
    session.add(meera)
    session.commit()

    with pytest.raises(MatchingError, match="Booking blocked: You have 2 recorded no-shows"):
        find_matching_slots(
            session=session,
            student_id=meera.id,
            faculty_id=raman.id,
            duration_minutes=15,
            window_start=datetime(2026, 10, 21, 14, 0),
            window_end=datetime(2026, 10, 21, 15, 0)
        )
