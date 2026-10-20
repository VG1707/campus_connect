"""
Unit tests for Group Session detection and workflow.
Verifies the essential rule:
Group session counts are evaluated PER-SLOT availability, NOT per-topic alone.
Students busy in class during the slot are strictly excluded.
"""

from datetime import datetime, date
from sqlmodel import Session, select

from app.models import (
    User, Course, OfficeHour, MeetingRequest, GroupSession,
    GroupSessionState, RequestState, Notification
)
from app.group_sessions import (
    evaluate_group_sessions, host_group_session, decline_group_session
)


def get_user_by_email(session: Session, email: str) -> User:
    """Helper to fetch user by email."""
    stmt = select(User).where(User.email == email)
    return session.exec(stmt).one()


def test_group_session_slot_overlap_not_topic_alone(session: Session):
    """
    Scenario:
    - 4 students (Meera, Rohan, Karthik, Arjun) all requested doubts on 'Binary Trees'.
    - Meera, Rohan, Karthik are free during Day 3 (2026-10-21) 14:00-15:00.
    - Arjun has EC204 Lab during Day 3 13:00-14:45, conflicting with the slot!
    - Threshold = 3.
    - Outcome:
      - Group session is suggested with EXACTLY 3 members.
      - Arjun is correctly EXCLUDED.
    """
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    rohan = get_user_by_email(session, "rohan.verma@campusconnect.edu")
    karthik = get_user_by_email(session, "karthik.s@campusconnect.edu")
    arjun = get_user_by_email(session, "arjun.rao@campusconnect.edu")

    # Evaluate group sessions for target week starting 2026-10-21
    suggested = evaluate_group_sessions(
        session=session,
        days_ahead=3,
        threshold=3,
        start_date=date(2026, 10, 21)
    )

    # Must find a suggested group session for Dr. Raman
    assert len(suggested) >= 1
    gs = next(s for s in suggested if s.faculty_id == raman.id and s.topic_tag == "Binary Trees")

    assert gs.state == GroupSessionState.SUGGESTED.value
    member_ids = gs.member_student_ids

    # Meera, Rohan, Karthik must be in members
    assert meera.id in member_ids
    assert rohan.id in member_ids
    assert karthik.id in member_ids

    # Arjun MUST BE EXCLUDED due to class conflict!
    assert arjun.id not in member_ids
    assert len(member_ids) == 3


def test_faculty_hosts_group_session_workflow(session: Session):
    """
    When faculty clicks 'Host Group Session':
    - State changes to 'hosted'.
    - Matching students' individual requests become 'confirmed'.
    - Excluded student's request remains 'pending'.
    - Notifications are sent to member students with aggregate info.
    """
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")
    arjun = get_user_by_email(session, "arjun.rao@campusconnect.edu")

    suggested = evaluate_group_sessions(
        session=session,
        days_ahead=3,
        threshold=3,
        start_date=date(2026, 10, 21)
    )
    gs = next(s for s in suggested if s.faculty_id == raman.id and s.topic_tag == "Binary Trees")

    # Faculty hosts
    hosted_gs = host_group_session(session, gs.id)
    assert hosted_gs.state == GroupSessionState.HOSTED.value

    # Meera's request must now be confirmed
    stmt_meera_req = select(MeetingRequest).where(
        MeetingRequest.student_id == meera.id,
        MeetingRequest.topic_tag == "Binary Trees"
    )
    meera_req = session.exec(stmt_meera_req).one()
    assert meera_req.state == RequestState.CONFIRMED.value
    assert meera_req.group_session_id == hosted_gs.id

    # Notification sent to Meera
    stmt_notif = select(Notification).where(Notification.user_id == meera.id)
    notifs = session.exec(stmt_notif).all()
    assert any("Group Consultation" in n.text and "Binary Trees" in n.text for n in notifs)

    # Arjun's request (who had a conflict) must REMAIN PENDING
    stmt_arjun_req = select(MeetingRequest).where(
        MeetingRequest.student_id == arjun.id,
        MeetingRequest.topic_tag == "Binary Trees"
    )
    arjun_req = session.exec(stmt_arjun_req).one()
    assert arjun_req.state == RequestState.PENDING.value
    assert arjun_req.group_session_id is None


def test_faculty_declines_group_session_workflow(session: Session):
    """When faculty declines group session, individual requests remain pending."""
    raman = get_user_by_email(session, "rajesh.raman@campusconnect.edu")
    meera = get_user_by_email(session, "meera.nair@campusconnect.edu")

    suggested = evaluate_group_sessions(
        session=session,
        days_ahead=3,
        threshold=3,
        start_date=date(2026, 10, 21)
    )
    gs = next(s for s in suggested if s.faculty_id == raman.id and s.topic_tag == "Binary Trees")

    declined_gs = decline_group_session(session, gs.id)
    assert declined_gs.state == GroupSessionState.DECLINED.value

    stmt_req = select(MeetingRequest).where(
        MeetingRequest.student_id == meera.id,
        MeetingRequest.topic_tag == "Binary Trees"
    )
    req = session.exec(stmt_req).one()
    assert req.state == RequestState.PENDING.value
