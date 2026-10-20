"""
Integration tests for FastAPI routes, templates, role switcher, and consultation workflows.
Uses Starlette/FastAPI TestClient.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import User, MeetingRequest, OfficeHour, RequestState, RequestSource
from app.database import get_session
from app.auth_mock import COOKIE_NAME





def test_home_redirect_to_student_dashboard(client: TestClient, session: Session):
    """When logged in as student, GET / should redirect to /student/dashboard."""
    stmt = select(User).where(User.email == "meera.nair@campusconnect.edu")
    meera = session.exec(stmt).one()

    client.cookies.set(COOKIE_NAME, str(meera.id))
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/student/dashboard"


def test_home_redirect_to_faculty_inbox(client: TestClient, session: Session):
    """When logged in as faculty, GET / should redirect to /faculty/inbox."""
    stmt = select(User).where(User.email == "rajesh.raman@campusconnect.edu")
    raman = session.exec(stmt).one()

    client.cookies.set(COOKIE_NAME, str(raman.id))
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/faculty/inbox"


def test_auth_switch_user_cookie(client: TestClient, session: Session):
    """GET /auth/switch?user_id=X must set campus_user_id cookie."""
    stmt = select(User).where(User.email == "rohan.verma@campusconnect.edu")
    rohan = session.exec(stmt).one()

    response = client.get(f"/auth/switch?user_id={rohan.id}", follow_redirects=False)
    assert response.status_code == 303
    assert COOKIE_NAME in response.cookies
    assert response.cookies[COOKIE_NAME] == str(rohan.id)


def test_faculty_directory_page(client: TestClient):
    """GET /faculty-directory returns 200 and lists professors."""
    response = client.get("/faculty-directory")
    assert response.status_code == 200
    assert "Faculty Directory" in response.text
    assert "Dr. Rajesh Raman" in response.text


def test_faculty_directory_search_filter(client: TestClient):
    """GET /faculty-directory?search=Algorithms filters correctly."""
    response = client.get("/faculty-directory?search=Binary Trees")
    assert response.status_code == 200
    assert "Dr. Rajesh Raman" in response.text


def test_student_dashboard_page(client: TestClient, session: Session):
    """GET /student/dashboard returns 200 and displays Meera's dashboard."""
    stmt = select(User).where(User.email == "meera.nair@campusconnect.edu")
    meera = session.exec(stmt).one()
    client.cookies.set(COOKIE_NAME, str(meera.id))

    response = client.get("/student/dashboard")
    assert response.status_code == 200
    assert "Welcome, Meera Nair" in response.text
    assert "I Have a Doubt" in response.text


def test_student_slots_search_htmx_partial(client: TestClient, session: Session):
    """GET /student/slots-search returns the ranked slot cards HTML partial."""
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(meera.id))

    # Search for Day 3 (2026-10-21) where Raman has office hours
    params = {
        "faculty_id": raman.id,
        "duration": 15,
        "from_date": "2026-10-21",
        "to_date": "2026-10-21"
    }
    response = client.get("/student/slots-search", params=params)
    assert response.status_code == 200
    assert "Rank #" in response.text
    assert "Transparent Match Reason" in response.text


def test_student_booking_auto_confirm(client: TestClient, session: Session):
    """POST /student/book on office hour with auto_confirm=True should confirm immediately."""
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(meera.id))

    # Clear existing requests for Meera to test clean booking
    for m in session.exec(select(MeetingRequest).where(MeetingRequest.student_id == meera.id)).all():
        session.delete(m)
    session.commit()

    form_data = {
        "faculty_id": raman.id,
        "topic_tag": "Graph Traversals",
        "type": "doubt",
        "description": "BFS vs DFS difference",
        "slot_start": "2026-10-21T14:30:00",
        "slot_end": "2026-10-21T14:45:00",
        "duration": 15,
        "source": "office_hour",
        "location": "Cabin 304"
    }
    response = client.post("/student/book", data=form_data, follow_redirects=False)
    assert response.status_code == 303

    # Verify meeting is confirmed in DB
    stmt_req = select(MeetingRequest).where(
        MeetingRequest.student_id == meera.id,
        MeetingRequest.topic_tag == "Graph Traversals"
    )
    req = session.exec(stmt_req).one()
    assert req.state == RequestState.CONFIRMED.value


def test_student_booking_on_demand_pending(client: TestClient, session: Session):
    """POST /student/book on on_demand source should remain pending."""
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(meera.id))

    for m in session.exec(select(MeetingRequest).where(MeetingRequest.student_id == meera.id)).all():
        session.delete(m)
    session.commit()

    form_data = {
        "faculty_id": raman.id,
        "topic_tag": "Research Mentorship",
        "type": "other",
        "description": "Discussing summer research",
        "slot_start": "2026-10-20T10:30:00",
        "slot_end": "2026-10-20T10:45:00",
        "duration": 15,
        "source": "on_demand",
        "location": "Faculty Cabin"
    }
    response = client.post("/student/book", data=form_data, follow_redirects=False)
    assert response.status_code == 303

    stmt_req = select(MeetingRequest).where(
        MeetingRequest.student_id == meera.id,
        MeetingRequest.topic_tag == "Research Mentorship"
    )
    req = session.exec(stmt_req).one()
    assert req.state == RequestState.PENDING.value


def test_faculty_accept_inbox_request(client: TestClient, session: Session):
    """Faculty POST /faculty/requests/{id}/accept confirms pending request."""
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(raman.id))

    # Find a pending request for Raman
    stmt_req = select(MeetingRequest).where(
        MeetingRequest.faculty_id == raman.id,
        MeetingRequest.state == RequestState.PENDING.value
    )
    pending_req = session.exec(stmt_req).first()
    assert pending_req is not None

    response = client.post(
        f"/faculty/requests/{pending_req.id}/accept",
        data={"location": "Cabin 304, Academic Block A"},
        follow_redirects=False
    )
    assert response.status_code == 303

    session.refresh(pending_req)
    assert pending_req.state == RequestState.CONFIRMED.value


def test_faculty_counter_propose_and_student_accept(client: TestClient, session: Session):
    """Faculty counter-proposes alternate time, student accepts."""
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()

    # 1. Faculty counters
    client.cookies.set(COOKIE_NAME, str(raman.id))
    stmt_req = select(MeetingRequest).where(
        MeetingRequest.faculty_id == raman.id,
        MeetingRequest.student_id == meera.id
    )
    req = session.exec(stmt_req).first()
    assert req is not None

    counter_data = {
        "new_date": "2026-10-23",
        "new_time": "15:30",
        "duration": 20,
        "location": "Cabin 304",
        "note": "Please come at 3:30 PM instead."
    }
    response = client.post(f"/faculty/requests/{req.id}/counter", data=counter_data, follow_redirects=False)
    assert response.status_code == 303

    session.refresh(req)
    assert req.state == RequestState.COUNTERED.value
    assert req.counter_note == "Please come at 3:30 PM instead."

    # 2. Student accepts counter-proposal
    client.cookies.set(COOKIE_NAME, str(meera.id))
    accept_resp = client.post(f"/student/meetings/{req.id}/accept-counter", follow_redirects=False)
    assert accept_resp.status_code == 303

    session.refresh(req)
    assert req.state == RequestState.CONFIRMED.value


def test_faculty_mark_no_show_blocks_student_at_two(client: TestClient, session: Session):
    """Faculty marks student as no-show; 2 no-shows sets is_blocked=True."""
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(raman.id))

    # Reset Meera's no-shows to 1
    meera.no_show_count = 1
    meera.is_blocked = False
    session.add(meera)
    session.commit()

    # Create a confirmed meeting for Meera
    meeting = MeetingRequest(
        student_id=meera.id,
        faculty_id=raman.id,
        description="Viva doubt",
        preferred_window_start="2026-10-21T14:00:00",
        preferred_window_end="2026-10-21T14:15:00",
        slot_start="2026-10-21T14:00:00",
        slot_end="2026-10-21T14:15:00",
        duration=15,
        state=RequestState.CONFIRMED.value,
        source=RequestSource.OFFICE_HOUR.value,
        created_at="2026-10-20T10:00:00",
        updated_at="2026-10-20T10:00:00"
    )
    session.add(meeting)
    session.commit()

    # Mark as no-show
    resp = client.post(f"/faculty/meetings/{meeting.id}/no-show", follow_redirects=False)
    assert resp.status_code == 303

    session.refresh(meera)
    assert meera.no_show_count == 2
    assert meera.is_blocked is True


def test_admin_mode_toggle_and_student_reset(client: TestClient, session: Session):
    """Admin toggles calendar mode and resets a blocked student."""
    admin = session.exec(select(User).where(User.role == "admin")).first()
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(admin.id))

    # Toggle mode
    resp = client.post("/admin/calendar-mode", data={"mode": "weekday"}, follow_redirects=False)
    assert resp.status_code == 303

    # Reset student
    meera.no_show_count = 2
    meera.is_blocked = True
    session.add(meera)
    session.commit()

    reset_resp = client.post(f"/admin/reset-student/{meera.id}", follow_redirects=False)
    assert reset_resp.status_code == 303

    session.refresh(meera)
    assert meera.no_show_count == 0
    assert meera.is_blocked is False


def test_notifications_page(client: TestClient, session: Session):
    """GET /notifications displays user notifications."""
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(meera.id))

    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert "Your Notifications" in resp.text
