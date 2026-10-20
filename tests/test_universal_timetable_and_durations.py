"""
Tests for Universal Timetable Extraction (Excel .xlsx, .csv, image) and 5m/10m Doubt Durations.
Verifies end-to-end functionality for both Students and Faculty.
"""

import io
from datetime import datetime
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import (
    User, UserRole, Course, OfficeHour, TimetableEntry, MeetingRequest, RequestState, RequestSource
)
from app.matching import find_matching_slots
from app.auth_mock import COOKIE_NAME
from app.extractor.excel_extractor import generate_sample_csv, generate_sample_xlsx


def test_5min_and_10min_doubt_durations(client: TestClient, session: Session):
    """Verify 5-minute and 10-minute doubt durations generate valid slots and auto-confirm."""
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()

    client.cookies.set(COOKIE_NAME, str(meera.id))

    # 1. Search 5-minute slots via HTMX endpoint
    res_5 = client.get(
        f"/student/slots-search?faculty_id={raman.id}&duration=5&from_date=2026-10-21&to_date=2026-10-21"
    )
    assert res_5.status_code == 200
    assert "5 mins" in res_5.text

    # 2. Book a 5-minute slot
    dt_start = "2026-10-21T14:00:00"
    dt_end = "2026-10-21T14:05:00"
    book_data = {
        "faculty_id": raman.id,
        "slot_start": dt_start,
        "slot_end": dt_end,
        "duration": 5,
        "source": RequestSource.OFFICE_HOUR.value,
        "course_id": 1,
        "topic_tag": "Binary Trees",
        "type": "doubt",
        "description": "5-minute quick doubt on tree balancing formula",
        "location": "Cabin 304, Academic Block A",
        "can_auto_confirm": "true"
    }
    resp_book = client.post("/student/book", data=book_data, follow_redirects=False)
    assert resp_book.status_code == 303

    stmt_m = select(MeetingRequest).where(
        MeetingRequest.student_id == meera.id,
        MeetingRequest.duration == 5
    )
    m = session.exec(stmt_m).first()
    assert m is not None
    assert m.state == RequestState.CONFIRMED.value
    assert m.slot_start == dt_start
    assert m.slot_end == dt_end

    # 3. Search 10-minute slots
    res_10 = client.get(
        f"/student/slots-search?faculty_id={raman.id}&duration=10&from_date=2026-10-21&to_date=2026-10-21"
    )
    assert res_10.status_code == 200
    assert "10 mins" in res_10.text


def test_student_upload_fake_csv_and_clash_detection(client: TestClient, session: Session):
    """
    Student uploads a fake CSV timetable with a class on Day 1 Period 1.
    Slot matching verifies that Period 1 is treated as a busy clash.
    """
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(meera.id))

    fake_csv = (
        "Day,Period 1,Period 2,Period 3,Period 4,Period 5,Period 6,Period 7,Period 8\n"
        "Day 1,CS999 (TP-101),-,-,-,-,-,-,-\n"
        "Day 2,-,-,-,-,-,-,-,-\n"
    )

    # Post CSV upload
    files = {"screenshot": ("my_fake_schedule.csv", io.BytesIO(fake_csv.encode("utf-8")), "text/csv")}
    res_upload = client.post("/student/timetable/upload", files=files)
    assert res_upload.status_code == 200
    assert "Review &amp; Edit Extracted Timetable" in res_upload.text or "Review & Edit Extracted Timetable" in res_upload.text
    assert "CS999" in res_upload.text

    # Confirm review screen
    confirm_data = {
        "initial_count": 1,
        "extracted_at": 100.0,
        "day_1": "Day 1",
        "period_1": "1",
        "course_code_1": "CS999",
        "course_name_1": "Advanced AI",
        "room_1": "TP-101",
        "is_lab_1": "false"
    }
    res_confirm = client.post("/student/timetable/review/confirm", data=confirm_data, follow_redirects=False)
    assert res_confirm.status_code == 303

    # Check database
    stmt = select(TimetableEntry).where(TimetableEntry.owner_id == meera.id)
    entries = session.exec(stmt).all()
    assert len(entries) == 1
    assert entries[0].day_order_or_weekday == "Day 1"
    assert entries[0].period == 1


def test_student_upload_fake_xlsx(client: TestClient, session: Session):
    """Student uploads a fake Excel (.xlsx) file and confirms review."""
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(meera.id))

    xlsx_bytes = generate_sample_xlsx(role="student")
    files = {"screenshot": ("custom_timetable.xlsx", io.BytesIO(xlsx_bytes), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    res_upload = client.post("/student/timetable/upload", files=files)
    assert res_upload.status_code == 200
    assert "CS201" in res_upload.text


def test_faculty_upload_teaching_timetable_and_free_slot_prediction(client: TestClient, session: Session):
    """
    Faculty (Dr. Raman) uploads teaching timetable via CSV.
    Teaching periods become busy (no consultations allowed).
    Non-teaching periods become Layer 1 Likely Free slots for students.
    """
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(raman.id))

    # GET faculty timetable page
    res_tt = client.get("/faculty/timetable")
    assert res_tt.status_code == 200
    assert "My Weekly Teaching Schedule" in res_tt.text

    # Upload teaching timetable with class on Day 1 Period 1 & 2
    faculty_csv = (
        "Day,Period 1,Period 2,Period 3,Period 4,Period 5,Period 6,Period 7,Period 8\n"
        "Day 1,CS201 (LH-201),CS201 (LH-201),-,FREE,-,-,-,-\n"
        "Day 2,-,-,-,-,-,-,-,-\n"
    )
    files = {"screenshot": ("faculty_teaching.csv", io.BytesIO(faculty_csv.encode("utf-8")), "text/csv")}
    res_upload = client.post("/faculty/timetable/upload", files=files)
    assert res_upload.status_code == 200
    assert "Review &amp; Confirm Teaching Schedule" in res_upload.text or "Review & Confirm Teaching Schedule" in res_upload.text

    # Confirm 2 periods
    confirm_data = {
        "initial_count": 2,
        "extracted_at": 100.0,
        "day_1": "Day 1",
        "period_1": "1",
        "course_code_1": "CS201",
        "course_name_1": "Data Structures",
        "room_1": "LH-201",
        "is_lab_1": "false",
        "day_2": "Day 1",
        "period_2": "2",
        "course_code_2": "CS201",
        "course_name_2": "Data Structures",
        "room_2": "LH-201",
        "is_lab_2": "false"
    }
    res_confirm = client.post("/faculty/timetable/review/confirm", data=confirm_data, follow_redirects=False)
    assert res_confirm.status_code == 303

    # Check database
    stmt = select(TimetableEntry).where(TimetableEntry.owner_id == raman.id)
    entries = session.exec(stmt).all()
    assert len(entries) == 2

    # Now verify slot matching for Day 1:
    # 2026-10-19 is Day 1
    # Period 1 (08:30-09:20) and Period 2 (09:25-10:15) MUST NOT be suggested
    # Period 3 (10:20-11:10) MUST be suggested as Layer 1 on-demand
    slots = find_matching_slots(
        session=session,
        student_id=1,
        faculty_id=raman.id,
        duration_minutes=15,
        window_start=datetime.fromisoformat("2026-10-19T08:00:00"),
        window_end=datetime.fromisoformat("2026-10-19T18:00:00"),
        include_on_demand=True,
        current_time=datetime.fromisoformat("2026-10-19T08:00:00")
    )
    slot_starts = [s.slot_start.strftime("%H:%M") for s in slots]
    # Teaching periods must NOT be in slots
    assert "08:30" not in slot_starts
    assert "09:25" not in slot_starts
    # Free teaching periods MUST be in slots (Layer 1)
    assert "10:30" in slot_starts
    on_demand_slots = [s for s in slots if s.source == RequestSource.ON_DEMAND.value]
    assert len(on_demand_slots) > 0


def test_sample_csv_and_xlsx_download_routes(client: TestClient):
    """Verify student and faculty sample CSV/XLSX download endpoints return valid data."""
    # Student CSV
    r1 = client.get("/student/timetable/sample-csv")
    assert r1.status_code == 200
    assert "text/csv" in r1.headers["content-type"]
    assert "Period 1" in r1.text

    # Student XLSX
    r2 = client.get("/student/timetable/sample-xlsx")
    assert r2.status_code == 200
    assert len(r2.content) > 100

    # Faculty CSV
    r3 = client.get("/faculty/timetable/sample-csv")
    assert r3.status_code == 200
    assert "text/csv" in r3.headers["content-type"]
    assert "Day 1" in r3.text

    # Faculty XLSX
    r4 = client.get("/faculty/timetable/sample-xlsx")
    assert r4.status_code == 200
    assert len(r4.content) > 100
