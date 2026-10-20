"""
Group Session Intelligence for CampusConnect.
Evaluates upcoming office-hour occurrences and aggregates open student doubt requests.
Crucial Rule: Count is evaluated PER-SLOT availability, NOT merely per-topic.
Students busy in class during that slot are strictly excluded.
"""

from datetime import datetime, date, time, timedelta
from typing import List, Dict, Tuple, Optional
import json
from sqlmodel import Session, select

from app.models import (
    User, OfficeHour, MeetingRequest, GroupSession, GroupSessionState,
    RequestState, RequestSource, TimetableEntry
)
from app.calendar_engine import (
    get_day_label_for_date, is_working_day, parse_date
)
from app.matching import (
    get_student_class_intervals, intervals_overlap
)
from app.database import record_audit, send_notification


DEFAULT_GROUP_THRESHOLD = 3


def find_students_free_in_slot(
    session: Session,
    student_ids: List[int],
    slot_start: datetime,
    slot_end: datetime,
    day_label: str
) -> List[int]:
    """
    Given a list of student IDs, returns only those who have NO timetable
    classes or confirmed meetings conflicting with the target slot.
    """
    free_students = []

    for sid in student_ids:
        # Check student timetable classes
        class_intervals = get_student_class_intervals(
            session, sid, slot_start.date(), day_label
        )
        has_class_clash = any(
            intervals_overlap(slot_start, slot_end, c_start, c_end)
            for c_start, c_end, _ in class_intervals
        )
        if has_class_clash:
            continue  # Busy in class!

        # Check existing confirmed meetings that would conflict with this slot
        stmt_meetings = select(MeetingRequest).where(
            MeetingRequest.student_id == sid,
            MeetingRequest.state == RequestState.CONFIRMED.value
        )
        meetings = session.exec(stmt_meetings).all()
        has_meeting_clash = any(
            intervals_overlap(
                slot_start, slot_end,
                datetime.fromisoformat(m.slot_start),
                datetime.fromisoformat(m.slot_end)
            )
            for m in meetings
        )
        if has_meeting_clash:
            continue  # Busy in confirmed meeting!

        free_students.append(sid)

    return free_students


def evaluate_group_sessions(
    session: Session,
    days_ahead: int = 7,
    threshold: int = DEFAULT_GROUP_THRESHOLD,
    start_date: Optional[date] = None
) -> List[GroupSession]:
    """
    Scan upcoming office hours and pending requests.
    Suggest a group session when >= threshold students share a topic AND
    are provably free during that specific office-hour occurrence.
    """
    base_date = start_date or date.today()
    suggested_sessions: List[GroupSession] = []

    # Retrieve all office hours
    stmt_oh = select(OfficeHour)
    office_hours = session.exec(stmt_oh).all()

    for offset in range(days_ahead):
        cur_date = base_date + timedelta(days=offset)
        if not is_working_day(session, cur_date):
            continue

        day_label = get_day_label_for_date(session, cur_date)
        if not day_label:
            continue

        for oh in office_hours:
            # Check recurrence pattern match
            matches = False
            rec = oh.recurrence_pattern
            if ":" in rec:
                kind, val = rec.split(":", 1)
                if kind == "day_order" and val.strip() == day_label:
                    matches = True
                elif kind == "weekday" and val.strip().lower() == cur_date.strftime("%A").lower():
                    matches = True
            elif rec.strip() == day_label or rec.strip().lower() == cur_date.strftime("%A").lower():
                matches = True

            if not matches:
                continue

            if cur_date.isoformat() in oh.paused_dates:
                continue

            # Parse slot interval
            oh_sh, oh_sm = map(int, oh.start_time.split(":"))
            oh_eh, oh_em = map(int, oh.end_time.split(":"))
            slot_start = datetime.combine(cur_date, time(oh_sh, oh_sm))
            slot_end = datetime.combine(cur_date, time(oh_eh, oh_em))

            # Query all pending requests for this faculty that have a topic tag
            stmt_req = select(MeetingRequest).where(
                MeetingRequest.faculty_id == oh.faculty_id,
                MeetingRequest.state == RequestState.PENDING.value,
                MeetingRequest.topic_tag != None
            )
            pending_requests = session.exec(stmt_req).all()

            # Group requests by topic
            topic_to_students: Dict[str, List[int]] = {}
            for req in pending_requests:
                if req.topic_tag:
                    topic_to_students.setdefault(req.topic_tag, []).append(req.student_id)

            # Evaluate each topic
            for topic, student_ids in topic_to_students.items():
                # De-duplicate student ids
                unique_students = list(set(student_ids))

                # Check who is ACTUALLY FREE in this specific slot
                free_students = find_students_free_in_slot(
                    session, unique_students, slot_start, slot_end, day_label
                )

                if len(free_students) >= threshold and len(free_students) <= oh.capacity:
                    # Check if a group session already exists for this slot and topic
                    stmt_existing = select(GroupSession).where(
                        GroupSession.office_hour_id == oh.id,
                        GroupSession.occurrence_date == cur_date.isoformat(),
                        GroupSession.topic_tag == topic
                    )
                    existing = session.exec(stmt_existing).first()

                    if not existing:
                        group_session = GroupSession(
                            faculty_id=oh.faculty_id,
                            office_hour_id=oh.id,
                            occurrence_date=cur_date.isoformat(),
                            slot_start=slot_start.isoformat(),
                            slot_end=slot_end.isoformat(),
                            topic_tag=topic,
                            capacity=oh.capacity,
                            state=GroupSessionState.SUGGESTED.value,
                            member_student_ids_json=json.dumps(free_students),
                            created_at=datetime.now().isoformat()
                        )
                        session.add(group_session)
                        session.commit()
                        session.refresh(group_session)
                        suggested_sessions.append(group_session)
                    else:
                        suggested_sessions.append(existing)

    return suggested_sessions


def host_group_session(session: Session, group_session_id: int) -> GroupSession:
    """
    Faculty approves the group session.
    Converts member students' individual pending requests into confirmed group seats.
    """
    gs = session.get(GroupSession, group_session_id)
    if not gs:
        raise ValueError("Group session not found.")

    gs.state = GroupSessionState.HOSTED.value
    session.add(gs)

    # Convert member pending requests to confirmed
    member_ids = gs.member_student_ids
    stmt_req = select(MeetingRequest).where(
        MeetingRequest.faculty_id == gs.faculty_id,
        MeetingRequest.topic_tag == gs.topic_tag,
        MeetingRequest.student_id.in_(member_ids),
        MeetingRequest.state == RequestState.PENDING.value
    )
    requests = session.exec(stmt_req).all()

    for req in requests:
        req.state = RequestState.CONFIRMED.value
        req.slot_start = gs.slot_start
        req.slot_end = gs.slot_end
        req.group_session_id = gs.id
        req.updated_at = datetime.now().isoformat()
        session.add(req)

        # Notify student (aggregate details only)
        send_notification(
            session=session,
            user_id=req.student_id,
            text=f"Your doubt on '{gs.topic_tag}' has been accepted as a Group Consultation on {gs.slot_start[:10]} at {gs.slot_start[11:16]}."
        )

    record_audit(
        session=session,
        actor_id=gs.faculty_id,
        action="GROUP_SESSION_HOSTED",
        entity_type="GroupSession",
        entity_id=gs.id,
        details=f"Hosted group session on '{gs.topic_tag}' with {len(member_ids)} students."
    )

    session.commit()
    session.refresh(gs)
    return gs


def decline_group_session(session: Session, group_session_id: int) -> GroupSession:
    """Faculty declines group session; requests remain individual pending."""
    gs = session.get(GroupSession, group_session_id)
    if not gs:
        raise ValueError("Group session not found.")

    gs.state = GroupSessionState.DECLINED.value
    session.add(gs)
    session.commit()
    session.refresh(gs)
    return gs
