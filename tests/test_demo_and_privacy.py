"""
Tests for Privacy & DPDP Erasure, Operational Metrics Dashboard, and 6 Demo Scenarios.
"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import (
    User, UserRole, TimetableEntry, MeetingRequest, ExtractionLog, RequestState
)


def test_privacy_page_and_data_erasure(client: TestClient, session: Session):
    """Verify privacy policy displays and self-service erasure purges user data."""
    student = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).first()
    client.cookies.set("campus_user_id", str(student.id))

    # GET /privacy
    res = client.get("/privacy")
    assert res.status_code == 200
    assert "Privacy, Retention & Data Minimization" in res.text
    assert "DPDP Act (2023) Aligned" in res.text

    # Seed a dummy timetable entry and meeting request for this student
    tt = TimetableEntry(owner_id=student.id, day_order_or_weekday="Day 1", period=1, course_code="CS101", room="TP1")
    session.add(tt)
    mr = MeetingRequest(
        student_id=student.id, faculty_id=1, description="Doubt",
        preferred_window_start="2026-10-20T10:00:00", preferred_window_end="2026-10-20T10:30:00",
        slot_start="2026-10-20T10:00:00", slot_end="2026-10-20T10:15:00",
        created_at="2026-10-18T10:00:00", updated_at="2026-10-18T10:00:00"
    )
    session.add(mr)
    session.commit()

    # POST /privacy/delete-my-data
    res_del = client.post("/privacy/delete-my-data", follow_redirects=True)
    assert res_del.status_code == 200
    assert "Data Erasure Completed" in res_del.text

    # Verify student entries are wiped
    user_tt = session.exec(select(TimetableEntry).where(TimetableEntry.owner_id == student.id)).all()
    assert len(user_tt) == 0

    user_mr = session.exec(select(MeetingRequest).where(MeetingRequest.student_id == student.id)).all()
    assert len(user_mr) == 0


def test_admin_metrics_view(client: TestClient, session: Session):
    """Verify GET /admin/metrics computes and displays operational telemetry."""
    admin = session.exec(select(User).where(User.role == UserRole.ADMIN.value)).first()
    client.cookies.set("campus_user_id", str(admin.id))

    res = client.get("/admin/metrics")
    assert res.status_code == 200
    assert "Operational Metrics & Performance" in res.text
    assert "Acceptance Rate" in res.text
    assert "Median Confirmation Time" in res.text
    assert "Faculty Consultation Workload" in res.text
    assert "Cohort Group Sessions" in res.text


def test_demo_page_and_six_scenarios(client: TestClient, session: Session):
    """Verify demo scenarios hub and execution of all 6 guided demo scenarios."""
    admin = session.exec(select(User).where(User.role == UserRole.ADMIN.value)).first()
    client.cookies.set("campus_user_id", str(admin.id))

    # GET /demo
    res = client.get("/demo")
    assert res.status_code == 200
    assert "CampusConnect Demo Center" in res.text
    assert "Scenario 1" in res.text
    assert "Scenario 6" in res.text

    # Scenario 1: Meera Thursday doubt hour
    res1 = client.post("/demo/run/1")
    assert res1.status_code == 200
    assert "Scenario 1" in res1.text
    assert "Thursday Doubt Hour" in res1.text

    # Scenario 2: Capacity & auto-confirm
    res2 = client.post("/demo/run/2")
    assert res2.status_code == 200
    assert "Capacity Limit" in res2.text
    assert "Auto-Confirm" in res2.text

    # Scenario 3: Group session & clash filtering
    res3 = client.post("/demo/run/3")
    assert res3.status_code == 200
    assert "Cohort Group Session" in res3.text

    # Scenario 4: Layer 1 on-demand
    res4 = client.post("/demo/run/4")
    assert res4.status_code == 200
    assert "Layer 1 On-Demand" in res4.text

    # Scenario 5: Verified vs unverified schedule changes
    res5 = client.post("/demo/run/5")
    assert res5.status_code == 200
    assert "Verified vs Unverified Schedule Changes" in res5.text

    # Scenario 6: Academic calendar holiday cascade
    res6 = client.post("/demo/run/6")
    assert res6.status_code == 200
    assert "Academic Calendar Holiday Cascade" in res6.text

    # Reset demo database
    res_reset = client.post("/demo/reset")
    assert res_reset.status_code == 200
    assert "Database Re-seeded Successfully" in res_reset.text
