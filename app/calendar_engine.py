"""
Configurable Academic Calendar and Day-Order Engine.
Supports two modes:
1. "day_order": Rotating Day 1 to Day 6 cycle (skips Sundays & holidays; shifts on holidays).
2. "weekday": Standard Monday-Saturday timetable.

Standard college timetable periods (8 periods of 50 mins each, with breaks).
"""

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
from sqlmodel import Session, select
from app.models import AcademicCalendar, SystemConfig


# Standard period timings for an Indian engineering college
# Period -> (start_time_str, end_time_str)
DEFAULT_PERIOD_TIMINGS: Dict[int, Tuple[str, str]] = {
    1: ("08:30", "09:20"),
    2: ("09:25", "10:15"),
    # Morning Break: 10:15 - 10:30
    3: ("10:30", "11:20"),
    4: ("11:25", "12:15"),
    # Lunch Break: 12:15 - 13:00
    5: ("13:00", "13:50"),
    6: ("13:55", "14:45"),
    # Afternoon Break: 14:45 - 15:00
    7: ("15:00", "15:50"),
    8: ("15:55", "16:45"),
    # Extended Indian college evening slots
    9: ("16:50", "17:35"),
    10: ("17:40", "18:25"),
    11: ("18:30", "19:15"),
    12: ("19:20", "20:05"),
}

CONFIG_CALENDAR_MODE_KEY = "academic_calendar_mode"
MODE_DAY_ORDER = "day_order"
MODE_WEEKDAY = "weekday"


def get_calendar_mode(session: Session) -> str:
    """Retrieve active calendar mode ('day_order' or 'weekday'). Defaults to 'day_order'."""
    stmt = select(SystemConfig).where(SystemConfig.key == CONFIG_CALENDAR_MODE_KEY)
    config = session.exec(stmt).first()
    return config.value if config else MODE_DAY_ORDER


def set_calendar_mode(session: Session, mode: str) -> None:
    """Update active calendar mode ('day_order' or 'weekday')."""
    if mode not in (MODE_DAY_ORDER, MODE_WEEKDAY):
        raise ValueError(f"Invalid mode '{mode}'. Choose '{MODE_DAY_ORDER}' or '{MODE_WEEKDAY}'.")
    stmt = select(SystemConfig).where(SystemConfig.key == CONFIG_CALENDAR_MODE_KEY)
    config = session.exec(stmt).first()
    if config:
        config.value = mode
        session.add(config)
    else:
        new_config = SystemConfig(key=CONFIG_CALENDAR_MODE_KEY, value=mode)
        session.add(new_config)
    session.commit()


def get_period_times(period: int) -> Tuple[time, time]:
    """Return start and end time objects for a given period number (1 to 12)."""
    if period not in DEFAULT_PERIOD_TIMINGS:
        raise ValueError(f"Period {period} is out of bounds (1 to 12).")
    start_str, end_str = DEFAULT_PERIOD_TIMINGS[period]
    h1, m1 = map(int, start_str.split(":"))
    h2, m2 = map(int, end_str.split(":"))
    return time(h1, m1), time(h2, m2)


def get_period_interval(dt_date: date, period: int) -> Tuple[datetime, datetime]:
    """Return start and end datetimes for a period on a specific calendar date."""
    t_start, t_end = get_period_times(period)
    dt_start = datetime.combine(dt_date, t_start)
    dt_end = datetime.combine(dt_date, t_end)
    return dt_start, dt_end


def parse_date(date_input: date | str) -> date:
    """Ensure date is a Python date object."""
    if isinstance(date_input, str):
        return date.fromisoformat(date_input)
    return date_input


def get_day_label_for_date(session: Session, target_date: date | str) -> Optional[str]:
    """
    Get the effective timetable day label for a calendar date.
    Returns:
      - None if Sunday or Holiday or Exam Day.
      - "Day 1" through "Day 6" if in 'day_order' mode.
      - "Monday" through "Saturday" if in 'weekday' mode.
    """
    d = parse_date(target_date)
    # Sunday is never an academic teaching day
    if d.weekday() == 6:
        return None

    date_str = d.isoformat()
    stmt = select(AcademicCalendar).where(AcademicCalendar.date == date_str)
    cal_entry = session.exec(stmt).first()

    if cal_entry:
        if cal_entry.is_holiday or cal_entry.is_exam:
            return None
        mode = get_calendar_mode(session)
        if mode == MODE_DAY_ORDER:
            return f"Day {cal_entry.day_order}" if cal_entry.day_order else None
        else:
            return d.strftime("%A")

    # Fallback if no calendar record exists
    mode = get_calendar_mode(session)
    if mode == MODE_WEEKDAY:
        return d.strftime("%A")
    return None


def is_working_day(session: Session, target_date: date | str) -> bool:
    """Check if the date is a regular working academic day (not holiday, not exam, not Sunday)."""
    return get_day_label_for_date(session, target_date) is not None


def generate_calendar_schedule(
    start_date: date,
    days_count: int,
    holidays: Optional[Dict[str, str]] = None,
    exam_days: Optional[Dict[str, str]] = None,
    start_day_order: int = 1,
    cycle_size: int = 6
) -> List[AcademicCalendar]:
    """
    Generate a list of AcademicCalendar records demonstrating rotating Day Orders.
    Holidays and Sundays do NOT consume a day order: the cycle pauses and resumes
    on the next working day.
    """
    holidays = holidays or {}
    exam_days = exam_days or {}
    entries: List[AcademicCalendar] = []

    current_day_order = start_day_order
    for offset in range(days_count):
        cur_date = start_date + timedelta(days=offset)
        cur_date_str = cur_date.isoformat()
        is_sunday = (cur_date.weekday() == 6)
        is_holiday = cur_date_str in holidays or is_sunday
        is_exam = cur_date_str in exam_days

        note = None
        if cur_date_str in holidays:
            note = holidays[cur_date_str]
        elif is_sunday:
            note = "Sunday"
        elif is_exam:
            note = exam_days[cur_date_str]

        if is_holiday or is_exam:
            day_order = None
        else:
            day_order = current_day_order
            # Advance Day Order (1 -> 2 -> ... -> 6 -> 1)
            current_day_order = (current_day_order % cycle_size) + 1

        entries.append(AcademicCalendar(
            date=cur_date_str,
            day_order=day_order,
            is_holiday=is_holiday,
            is_exam=is_exam,
            note=note
        ))

    return entries


def shift_calendar_on_holiday(
    session: Session,
    holiday_date_str: str,
    note: str = "Declared Holiday"
) -> None:
    """
    Simulate declaring an unexpected holiday:
    The targeted day becomes a holiday, and subsequent working days' Day Orders
    shift forward by one day to maintain the sequence.
    """
    stmt = select(AcademicCalendar).where(AcademicCalendar.date >= holiday_date_str).order_by(AcademicCalendar.date)
    future_entries = session.exec(stmt).all()
    if not future_entries:
        return

    # First entry becomes holiday
    target = future_entries[0]
    previous_order = target.day_order
    target.is_holiday = True
    target.day_order = None
    target.note = note
    session.add(target)

    # If the target day originally had a day order, cascade it to subsequent working days
    if previous_order is not None:
        curr_order = previous_order
        for entry in future_entries[1:]:
            d = date.fromisoformat(entry.date)
            # Skip Sundays and holidays
            if d.weekday() == 6 or entry.is_holiday or entry.is_exam:
                continue
            entry.day_order = curr_order
            session.add(entry)
            curr_order = (curr_order % 6) + 1

    session.commit()
