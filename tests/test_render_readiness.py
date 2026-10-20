"""
Tests for Render Cloud Deployment Readiness:
- Health check endpoints (/health, /healthz)
- Configurable database path via environment variables
- Automatic initialization and demo account seeding on empty databases
- Upload directory cleanup on server startup
- Render blueprint specification (render.yaml)
"""

import os
from pathlib import Path
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select
from sqlmodel.pool import StaticPool

from app.main import app, cleanup_temp_uploads
from app.database import get_database_url, init_db, PROJECT_ROOT
from app.models import User, OfficeHour, Course


def test_health_check_endpoints():
    """Verify /health and /healthz return 200 OK with expected JSON structure."""
    with TestClient(app) as client:
        for path in ["/health", "/healthz"]:
            resp = client.get(path)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert data["service"] == "CampusConnect"
            assert "version" in data


def test_configurable_database_url_env_vars(monkeypatch, tmp_path):
    """Verify DATABASE_URL and DATABASE_PATH environment variables configure the database URL."""
    # 1. Default local path
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    default_url = get_database_url()
    assert "campus_connect.db" in default_url
    assert default_url.startswith("sqlite:///")

    # 2. DATABASE_PATH relative
    monkeypatch.setenv("DATABASE_PATH", "custom_test.db")
    path_url = get_database_url()
    assert "custom_test.db" in path_url

    # 3. DATABASE_PATH absolute
    abs_custom = tmp_path / "prod.db"
    monkeypatch.setenv("DATABASE_PATH", str(abs_custom))
    abs_url = get_database_url()
    assert abs_url == f"sqlite:///{abs_custom.as_posix()}"

    # 4. DATABASE_URL direct override takes precedence
    custom_url = "sqlite:////tmp/render_test.db"
    monkeypatch.setenv("DATABASE_URL", custom_url)
    assert get_database_url() == custom_url


def test_auto_initialization_and_seeding_on_empty_db():
    """Verify that init_db() automatically seeds default accounts on an empty database."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    # Initialize with auto_seed=True
    init_db(target_engine=test_engine, auto_seed=True)

    with Session(test_engine) as session:
        users = session.exec(select(User)).all()
        assert len(users) >= 20  # Admin + 12 Faculty + 8 Students

        courses = session.exec(select(Course)).all()
        assert len(courses) >= 15

        office_hours = session.exec(select(OfficeHour)).all()
        assert len(office_hours) >= 5

        # Confirm seeded student Meera Nair exists
        meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).first()
        assert meera is not None


def test_cleanup_temp_uploads(tmp_path, monkeypatch):
    """Verify that cleanup_temp_uploads deletes orphaned files in temp_uploads/."""
    temp_dir = PROJECT_ROOT / "temp_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy stale upload file
    stale_file = temp_dir / "stale_test_screenshot.png"
    stale_file.write_text("dummy image bytes")
    assert stale_file.exists()

    # Run startup cleanup
    cleanup_temp_uploads()
    assert not stale_file.exists()


def test_render_yaml_configuration():
    """Verify that render.yaml blueprint exists and specifies valid free-tier parameters."""
    yaml_path = PROJECT_ROOT / "render.yaml"
    assert yaml_path.exists(), "render.yaml must exist at the project root"

    content = yaml_path.read_text(encoding="utf-8")
    assert "type: web" in content
    assert "uvicorn app.main:app --host 0.0.0.0 --port $PORT" in content
    assert "pip install -r requirements.txt" in content
    assert "healthCheckPath: /health" in content
