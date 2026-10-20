"""
Unit tests for the offline Mock Timetable Extractor.
Verifies ground-truth JSON fixture loading and fallback extraction.
"""

from app.extractor.mock_extractor import MockTimetableExtractor
from app.extractor.base import ExtractedTimetable


def test_mock_extractor_loads_cse_fixture():
    """Verify mock extractor accurately loads CSE 3rd semester fixture."""
    extractor = MockTimetableExtractor()
    result = extractor.extract_timetable("sample_timetable_cse_sem3.png")

    assert isinstance(result, ExtractedTimetable)
    assert len(result.entries) > 0
    assert result.confidence_score >= 0.95
    # Must contain CS201 and CS202 Lab
    course_codes = [e.course_code for e in result.entries]
    assert "CS201" in course_codes
    assert "CS202" in course_codes

    # Check lab flag
    lab_entries = [e for e in result.entries if e.is_lab]
    assert len(lab_entries) >= 2


def test_mock_extractor_loads_ece_fixture():
    """Verify mock extractor accurately loads ECE fixture."""
    extractor = MockTimetableExtractor()
    result = extractor.extract_timetable("sample_timetable_ece_sem3.png")

    assert isinstance(result, ExtractedTimetable)
    course_codes = [e.course_code for e in result.entries]
    assert "EC201" in course_codes
    assert "EC203" in course_codes
