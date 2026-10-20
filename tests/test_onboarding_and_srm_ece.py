"""
Tests for Post-Login Onboarding Timetable Gateway and SRM ECE Dataset.
Verifies the /welcome/setup gateway, role-specific buttons, and SRMIST ECE curriculum data.
"""

import json
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import User, Course, OfficeHour, TimetableEntry, UserRole
from app.database import get_session
from app.auth_mock import COOKIE_NAME


def test_auth_switch_with_onboarding_redirects_to_setup(session: Session):
    """GET /auth/switch?user_id=X&onboarding=true redirects to /welcome/setup."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        arjun = session.exec(select(User).where(User.email == "arjun.rao@campusconnect.edu")).one()
        resp = client.get(f"/auth/switch?user_id={arjun.id}&onboarding=true", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/welcome/setup"
        assert resp.cookies[COOKIE_NAME] == str(arjun.id)
    app.dependency_overrides.clear()


def test_onboarding_setup_student_view(session: Session):
    """GET /welcome/setup for student renders timetable upload and skip options."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        arjun = session.exec(select(User).where(User.email == "arjun.rao@campusconnect.edu")).one()
        client.cookies.set(COOKIE_NAME, str(arjun.id))
        resp = client.get("/welcome/setup")
        assert resp.status_code == 200
        assert "Welcome, Arjun Rao" in resp.text
        assert "Upload Your Timetable &amp; Map Slots" in resp.text or "Upload Your Timetable & Map Slots" in resp.text
        assert "/student/timetable/upload" in resp.text
        assert "Continue to Student Dashboard" in resp.text
        assert "/student/dashboard" in resp.text
    app.dependency_overrides.clear()


def test_onboarding_setup_faculty_view(session: Session):
    """GET /welcome/setup for faculty renders faculty-specific upload and skip options."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        priya = session.exec(select(User).where(User.email == "priya.sundaram@campusconnect.edu")).one()
        client.cookies.set(COOKIE_NAME, str(priya.id))
        resp = client.get("/welcome/setup")
        assert resp.status_code == 200
        assert "Welcome, Dr. Priya Sundaram" in resp.text
        assert "/faculty/timetable/upload" in resp.text
        assert "Continue to Faculty Inbox" in resp.text
        assert "/faculty/inbox" in resp.text
    app.dependency_overrides.clear()


def test_srm_ece_dataset_presence(session: Session):
    """Verify SRM ECE course codes, topics, and Tech Park venues are seeded."""
    # Check core SRM courses
    courses = session.exec(select(Course).where(Course.department == "Electronics & Communication Engineering")).all()
    codes = [c.code for c in courses]
    assert "EC201" in codes
    assert "EC203" in codes
    assert "18ECC203J" in codes
    assert "18ECC302J" in codes
    assert "18ECC303J" in codes

    # Check ECE topics
    ec203 = session.exec(select(Course).where(Course.code == "EC203")).one()
    tags = json.loads(ec203.topic_tags_json)
    assert any("Z-Transform" in tag for tag in tags)

    # Check Dr. Priya's office hours in Tech Park TP-704
    priya = session.exec(select(User).where(User.email == "priya.sundaram@campusconnect.edu")).one()
    oh_priya = session.exec(select(OfficeHour).where(OfficeHour.faculty_id == priya.id)).first()
    assert oh_priya is not None
    assert "TP-704" in oh_priya.location
    assert 5 in oh_priya.allowed_durations
    assert 10 in oh_priya.allowed_durations

    # Check Dr. Venkatesh's office hours in Tech Park TP-802
    venkatesh = session.exec(select(User).where(User.email == "venkatesh.k@campusconnect.edu")).one()
    oh_venk = session.exec(select(OfficeHour).where(OfficeHour.faculty_id == venkatesh.id)).first()
    assert oh_venk is not None
    assert "TP-802" in oh_venk.location
    assert 5 in oh_venk.allowed_durations

    # Check Arjun Rao's timetable in TP-701
    arjun = session.exec(select(User).where(User.email == "arjun.rao@campusconnect.edu")).one()
    arjun_entries = session.exec(select(TimetableEntry).where(TimetableEntry.owner_id == arjun.id)).all()
    assert len(arjun_entries) >= 5
    rooms = [e.room for e in arjun_entries]
    assert "TP-701" in rooms


def test_brand_logo_and_loop_rendered(session: Session):
    """Verify brand wordmark has CampusC and connection loop on public pages."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "CampusC" in resp.text
        assert "nnect" in resp.text
        assert "Better Office Hours" in resp.text
        # Verify live demo preview elements inside burgundy card
        assert "Explore Full Demo" in resp.text
        assert "Student Workspace" in resp.text
        assert "Dr. Rajesh Raman" in resp.text
    app.dependency_overrides.clear()
