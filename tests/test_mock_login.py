"""
Unit and integration tests for the Mock Login flow.
Verifies landing page, student and faculty selector screens, and cookie management.
"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import User, UserRole
from app.database import get_session
from app.auth_mock import COOKIE_NAME


def test_landing_page_without_cookie(session: Session):
    """GET / with no cookie renders the Welcome Landing page."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        # Ensure no cookie is passed
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        assert "Welcome to CampusConnect" in resp.text
        assert "Student Portal" in resp.text
        assert "Faculty Portal" in resp.text
    app.dependency_overrides.clear()


def test_landing_page_with_cookie_redirects(session: Session):
    """GET / with cookie redirects to appropriate dashboard."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
        client.cookies.set(COOKIE_NAME, str(meera.id))
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/student/dashboard"
    app.dependency_overrides.clear()


def test_student_login_selector(session: Session):
    """GET /login/student renders student account choices."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        resp = client.get("/login/student")
        assert resp.status_code == 200
        assert "Select a Student Demo Persona" in resp.text
        assert "Meera Nair" in resp.text
        assert "Divya Patel" in resp.text
    app.dependency_overrides.clear()


def test_faculty_login_selector(session: Session):
    """GET /login/faculty renders faculty account choices."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        resp = client.get("/login/faculty")
        assert resp.status_code == 200
        assert "Select a Faculty Demo Persona" in resp.text
        assert "Dr. Rajesh Raman" in resp.text
        assert "Prof. Vikram Iyer" in resp.text
    app.dependency_overrides.clear()


def test_quick_admin_login(session: Session):
    """GET /login/admin sets admin cookie and redirects to /admin."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        resp = client.get("/login/admin", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin"
        assert COOKIE_NAME in resp.cookies
    app.dependency_overrides.clear()


def test_logout_clears_cookie(session: Session):
    """GET /logout clears cookie and redirects to /login."""
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, "1")
        resp = client.get("/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"
    app.dependency_overrides.clear()
