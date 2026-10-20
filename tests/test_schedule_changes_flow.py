"""
Tests for Schedule Change Reporting, Verification, and Slot Warning Flow.
"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import (
    User, UserRole, ScheduleChange, ScheduleChangeKind, VerificationStatus
)


def test_faculty_reports_verified_change(client: TestClient, session: Session):
    """Faculty submissions are auto-verified upon reporting."""
    faculty = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).first()
    client.cookies.set("campus_user_id", str(faculty.id))

    # GET reporting page
    res_get = client.get("/faculty/schedule-change")
    assert res_get.status_code == 200
    assert "Report Timetable or Class Change" in res_get.text

    # POST reporting
    post_data = {
        "kind": "cancel",
        "change_date": "2026-10-22",
        "period": 4,
        "note": "Conference attendance"
    }
    res_post = client.post("/faculty/schedule-change", data=post_data)
    assert res_post.status_code == 200
    assert "Successfully published" in res_post.text

    # Verify database state
    sc = session.exec(select(ScheduleChange).where(ScheduleChange.owner_id == faculty.id)).first()
    assert sc is not None
    assert sc.verification == VerificationStatus.VERIFIED.value
    assert sc.reported_by == "faculty"


def test_student_reports_unverified_change_and_admin_verifies(client: TestClient, session: Session):
    """Student reports are unverified; Admin verifies them."""
    faculty = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).first()
    student = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).first()
    admin = session.exec(select(User).where(User.role == UserRole.ADMIN.value)).first()

    # 1. Student reports swap
    client.cookies.set("campus_user_id", str(student.id))
    post_data = {
        "faculty_id": faculty.id,
        "kind": "swap",
        "change_date": "2026-10-23",
        "period": 2,
        "new_period": 6,
        "note": "Class rep message"
    }
    res_stud = client.post("/student/schedule-change", data=post_data)
    assert res_stud.status_code == 200
    assert "marked as Unverified" in res_stud.text

    sc = session.exec(select(ScheduleChange).where(
        ScheduleChange.owner_id == faculty.id,
        ScheduleChange.reported_by == "student"
    )).first()
    assert sc is not None
    assert sc.verification == VerificationStatus.UNVERIFIED.value

    # 2. Admin views verification panel
    client.cookies.set("campus_user_id", str(admin.id))
    res_admin = client.get("/admin/schedule-changes")
    assert res_admin.status_code == 200
    assert "Schedule Change Verification" in res_admin.text
    assert "Unverified" in res_admin.text

    # 3. Admin verifies change
    res_verify = client.post(f"/admin/schedule-changes/{sc.id}/verify", follow_redirects=True)
    assert res_verify.status_code == 200

    session.refresh(sc)
    assert sc.verification == VerificationStatus.VERIFIED.value
