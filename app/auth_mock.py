"""
Mock Authentication System for CampusConnect.
Provides seamless navbar role switching for demo presentations.
Stores the active user ID in an HTTP cookie ('campus_user_id').
Never collects passwords or scrapes real credentials.
"""

from typing import Optional, List, Dict
from fastapi import Request, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import User, UserRole

COOKIE_NAME = "campus_user_id"


def get_authenticated_user_or_none(
    request: Request,
    session: Session = Depends(get_session)
) -> Optional[User]:
    """Return user if campus_user_id cookie is present and valid, else None."""
    user_id_str = request.cookies.get(COOKIE_NAME)
    if user_id_str and user_id_str.isdigit():
        return session.get(User, int(user_id_str))
    return None


def get_current_user(
    request: Request,
    session: Session = Depends(get_session)
) -> User:
    """
    Retrieve the active user from the 'campus_user_id' cookie.
    If no cookie is set, defaults to the primary student (Meera Nair).
    """
    user = get_authenticated_user_or_none(request, session)

    # Default fallback: Meera Nair (Student A) or the first available student
    if not user:
        stmt = select(User).where(User.role == UserRole.STUDENT.value).order_by(User.id)
        user = session.exec(stmt).first()

    if not user:
        # Ultimate fallback if database is empty
        user = User(
            id=1,
            role=UserRole.STUDENT.value,
            name="Demo Student",
            department="Computer Science & Engineering",
            email="demo.student@campusconnect.edu"
        )

    return user


def get_mock_users_by_role(session: Session) -> Dict[str, List[User]]:
    """
    Retrieve all seeded users categorized by role for the navbar switcher dropdown.
    """
    stmt = select(User).order_by(User.id)
    all_users = session.exec(stmt).all()

    grouped: Dict[str, List[User]] = {
        "students": [],
        "faculty": [],
        "admin": []
    }

    for u in all_users:
        if u.role == UserRole.STUDENT.value:
            grouped["students"].append(u)
        elif u.role == UserRole.FACULTY.value:
            grouped["faculty"].append(u)
        elif u.role == UserRole.ADMIN.value:
            grouped["admin"].append(u)

    return grouped
