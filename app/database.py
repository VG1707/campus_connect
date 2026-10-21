"""
Database engine configuration, table initialization, and session management.
Uses SQLite with SQLModel.
"""

import os
from pathlib import Path
from typing import Generator, Optional
from datetime import datetime
from sqlmodel import SQLModel, Session, create_engine, select
from app.models import AuditLog, Notification, User

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Configurable database path via DATABASE_URL or DATABASE_PATH
# Preserves local default "campus_connect.db" in project root
def get_database_url() -> str:
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url

    env_path = os.environ.get("DATABASE_PATH")
    if env_path:
        target_path = Path(env_path)
        if not target_path.is_absolute():
            target_path = PROJECT_ROOT / target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{target_path.as_posix()}"

    default_path = PROJECT_ROOT / "campus_connect.db"
    return f"sqlite:///{default_path.as_posix()}"

DEFAULT_DB_URL = get_database_url()

# Engine with connect_args for SQLite thread safety
engine = create_engine(
    DEFAULT_DB_URL,
    connect_args={"check_same_thread": False},
    echo=False
)


def init_db(target_engine=None, auto_seed: bool = True) -> None:
    """
    Create all database tables if they do not exist.
    If auto_seed is True and the database has no users, automatically populates
    synthetic demo accounts and timetables so the application is ready immediately on Render.
    """
    use_engine = target_engine or engine
    SQLModel.metadata.create_all(use_engine)

    # Safe column migration for SQLite if existing database lacks newly added columns
    try:
        from sqlalchemy import text
        with use_engine.connect() as conn:
            res = conn.execute(text("PRAGMA table_info(user)")).fetchall()
            col_names = [r[1] for r in res] if res else []
            if "languages_json" not in col_names and col_names:
                conn.execute(text("ALTER TABLE user ADD COLUMN languages_json TEXT DEFAULT '[\"English\"]'"))
                conn.commit()
    except Exception:
        pass

    if auto_seed:
        with Session(use_engine) as session:
            has_users = session.exec(select(User)).first() is not None
            if not has_users:
                from seed_data import seed_all_data
                seed_all_data(session, reset_existing=False)


def get_session(target_engine=None) -> Generator[Session, None, None]:
    """Provide a transactional database session."""
    use_engine = target_engine or engine
    with Session(use_engine) as session:
        yield session


def record_audit(
    session: Session,
    action: str,
    entity_type: str,
    details: str,
    actor_id: Optional[int] = None,
    entity_id: Optional[int] = None
) -> AuditLog:
    """Convenience helper to record an immutable audit entry."""
    entry = AuditLog(
        timestamp=datetime.now().isoformat(),
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def send_notification(
    session: Session,
    user_id: int,
    text: str
) -> Notification:
    """Convenience helper to write a user notification."""
    notif = Notification(
        user_id=user_id,
        text=text,
        read=False,
        created_at=datetime.now().isoformat()
    )
    session.add(notif)
    session.commit()
    session.refresh(notif)
    return notif
