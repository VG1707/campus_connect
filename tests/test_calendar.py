"""
Unit tests for the configurable Academic Calendar and Day-Order Engine.
"""

from datetime import date, datetime, time, timedelta
from sqlmodel import Session, select
from app.models import AcademicCalendar
from app.calendar_engine import (
    get_calendar_mode, set_calendar_mode, MODE_DAY_ORDER, MODE_WEEKDAY,
    get_day_label_for_date, is_working_day, get_period_times,
    get_period_interval, shift_calendar_on_holiday, generate_calendar_schedule
)


def test_calendar_mode_toggle(session: Session):
    """Verify switching between day-order mode and plain weekday mode."""
    assert get_calendar_mode(session) == MODE_DAY_ORDER
    set_calendar_mode(session, MODE_WEEKDAY)
    assert get_calendar_mode(session) == MODE_WEEKDAY
    set_calendar_mode(session, MODE_DAY_ORDER)
    assert get_calendar_mode(session) == MODE_DAY_ORDER


def test_day_order_rotation_and_sunday_skip(clean_session: Session):
    """Verify Day 1 through Day 6 sequence pauses on Sundays and continues on Monday."""
    # Start on Friday 2026-10-23 (Day 4)
    start_d = date(2026, 10, 23)
    entries = generate_calendar_schedule(
        start_date=start_d,
        days_count=5, # Fri, Sat, Sun, Mon, Tue
        start_day_order=4,
        cycle_size=6
    )
    for e in entries:
        clean_session.add(e)
    clean_session.commit()

    # Friday (Day 4)
    assert get_day_label_for_date(clean_session, "2026-10-23") == "Day 4"
    # Saturday (Day 5)
    assert get_day_label_for_date(clean_session, "2026-10-24") == "Day 5"
    # Sunday (skipped)
    assert get_day_label_for_date(clean_session, "2026-10-25") is None
    assert not is_working_day(clean_session, "2026-10-25")
    # Monday (Day 6)
    assert get_day_label_for_date(clean_session, "2026-10-26") == "Day 6"
    # Tuesday (wraps to Day 1)
    assert get_day_label_for_date(clean_session, "2026-10-27") == "Day 1"


def test_holiday_shifts_subsequent_day_orders(session: Session):
    """
    Verify declaring a holiday shifts the day-order sequence forward:
    The targeted day becomes holiday, and the next working day assumes that day order.
    """
    # In seeded data:
    # 2026-10-20 is Day 2
    # 2026-10-21 is Day 3
    # 2026-10-22 is Holiday
    # 2026-10-23 is Day 4
    assert get_day_label_for_date(session, "2026-10-21") == "Day 3"
    assert get_day_label_for_date(session, "2026-10-23") == "Day 4"

    # Now declare an unexpected holiday on 2026-10-21
    shift_calendar_on_holiday(session, "2026-10-21", note="Unexpected Heavy Rain Holiday")

    # 2026-10-21 is now a holiday
    assert get_day_label_for_date(session, "2026-10-21") is None
    assert not is_working_day(session, "2026-10-21")

    # 2026-10-22 was already a holiday
    assert get_day_label_for_date(session, "2026-10-22") is None

    # 2026-10-23 (next working day) now gets Day 3!
    assert get_day_label_for_date(session, "2026-10-23") == "Day 3"


def test_exam_day_is_not_working_day(session: Session):
    """Exam days must return is_working_day == False and no consultation matching."""
    # 2026-10-28 is seeded as Mid-term Exam Day
    assert not is_working_day(session, "2026-10-28")
    assert get_day_label_for_date(session, "2026-10-28") is None


def test_period_timing_intervals():
    """Verify standard Indian engineering college period bounds."""
    t_start, t_end = get_period_times(1)
    assert t_start == time(8, 30)
    assert t_end == time(9, 20)

    # Period 4 is before lunch
    t_start, t_end = get_period_times(4)
    assert t_start == time(11, 25)
    assert t_end == time(12, 15)

    # Period 5 is after lunch
    t_start, t_end = get_period_times(5)
    assert t_start == time(13, 0)
    assert t_end == time(13, 50)
