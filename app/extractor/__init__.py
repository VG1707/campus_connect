"""
Timetable extraction subsystem.
Supports swappable mock extractor and Gemini Vision extractor.
"""
from app.extractor.base import BaseTimetableExtractor, ExtractedEntry, ExtractedTimetable
from app.extractor.mock_extractor import MockTimetableExtractor

__all__ = [
    "BaseTimetableExtractor",
    "ExtractedEntry",
    "ExtractedTimetable",
    "MockTimetableExtractor",
]
