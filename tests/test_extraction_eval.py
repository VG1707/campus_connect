"""
Tests for Timetable Extraction Accuracy Evaluation and Image Deletion Workflow.
"""

import os
from pathlib import Path
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import User, UserRole, ExtractionLog, TimetableEntry
from app.extractor.evaluator import run_extraction_evaluation
from app.extractor.mock_extractor import MockTimetableExtractor


def test_offline_extraction_benchmark():
    """Verify that the benchmark runs across fixtures and achieves high accuracy."""
    report = run_extraction_evaluation()
    assert report["fixtures_count"] == 4
    assert report["total_expected_cells"] > 0
    assert report["overall_accuracy"] >= 95.0
    for r in report["per_fixture_results"]:
        assert r["cell_accuracy"] >= 90.0


def test_admin_extraction_eval_route(client: TestClient, session: Session):
    """Verify GET /admin/extraction-eval returns 200 and renders benchmark metrics."""
    # Ensure admin user
    admin = session.exec(select(User).where(User.role == UserRole.ADMIN.value)).first()
    client.cookies.set("campus_user_id", str(admin.id))

    response = client.get("/admin/extraction-eval")
    assert response.status_code == 200
    assert "Timetable Extraction Evaluation" in response.text
    assert "Overall Cell Accuracy" in response.text
    assert "Per-Image Accuracy Breakdown" in response.text


def test_extraction_review_confirm_and_image_deletion(client: TestClient, session: Session, tmp_path):
    """
    Verify that confirming the extraction saves timetable entries,
    records ExtractionLog, and deletes the uploaded temporary image.
    """
    student = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).first()
    client.cookies.set("campus_user_id", str(student.id))

    # Create dummy temp image
    temp_img = tmp_path / "test_tt.png"
    temp_img.write_bytes(b"dummy image content")
    assert os.path.exists(temp_img)

    # Confirm extraction
    form_data = {
        "day_0": "Day 2",
        "period_0": "1",
        "course_code_0": "CS201",
        "course_name_0": "Data Structures",
        "room_0": "TP401",
        "day_1": "Day 2",
        "period_1": "2",
        "course_code_1": "CS202",
        "course_name_1": "Digital Logic",
        "room_1": "TP402",
        "temp_image_path": str(temp_img),
        "initial_count": "2",
        "extracted_at": "100.0",
    }

    res = client.post("/student/timetable/review/confirm", data=form_data, follow_redirects=True)
    assert res.status_code == 200

    # Image must be immediately deleted from disk!
    assert not os.path.exists(temp_img)

    # Verify TimetableEntry created
    entries = session.exec(select(TimetableEntry).where(
        TimetableEntry.owner_id == student.id,
        TimetableEntry.day_order_or_weekday == "Day 2"
    )).all()
    assert len(entries) == 2

    # Verify ExtractionLog recorded
    logs = session.exec(select(ExtractionLog).where(ExtractionLog.student_id == student.id)).all()
    assert len(logs) >= 1
