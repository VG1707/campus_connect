"""
Pytest fixtures for CampusConnect test suite.
Provides isolated in-memory SQLite database sessions.
"""

import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from app.models import (
    User, UserRole, Course, FacultyCourse, AcademicCalendar,
    TimetableEntry, OfficeHour, MeetingRequest, ScheduleChange
)
from app.calendar_engine import set_calendar_mode, MODE_DAY_ORDER
from seed_data import seed_all_data


@pytest.fixture(name="test_engine")
def fixture_test_engine():
    """Create an isolated in-memory SQLite engine for the test session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="session")
def fixture_session(test_engine):
    """Provide a transactional session with complete seeded data."""
    with Session(test_engine) as session:
        seed_all_data(session, reset_existing=True)
        yield session


@pytest.fixture(name="clean_session")
def fixture_clean_session(test_engine):
    """Provide an empty database session with tables created."""
    with Session(test_engine) as session:
        # Clear all data
        for table in reversed(SQLModel.metadata.sorted_tables):
            session.exec(table.delete())
        session.commit()
        set_calendar_mode(session, MODE_DAY_ORDER)
        yield session


@pytest.fixture(name="client")
def fixture_client(session: Session):
    """Provide a TestClient with database session override."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_session

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

