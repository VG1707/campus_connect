"""
Base schemas and abstract interface for timetable extraction.
Extracts only day, period, course, room, and section.
Ignores student name, registration number, or sensitive personal data.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel, Field


class ExtractedEntry(BaseModel):
    """A single cell extracted from a student or faculty timetable."""
    day: str = Field(description="Day identifier, e.g., 'Day 1' or 'Monday'")
    period: int = Field(description="Period number, 1 through 12", ge=1, le=12)
    course_code: str = Field(description="Course code, e.g., 'CS201'")
    course_name: Optional[str] = Field(default="", description="Course title")
    room: str = Field(default="LH-101", description="Assigned classroom or lab")
    section: Optional[str] = Field(default=None, description="Section code, e.g., 'CSE-A'")
    is_lab: bool = Field(default=False, description="Flag indicating laboratory block")


class ExtractedOfficeHour(BaseModel):
    """An explicitly published faculty consultation window extracted from workbook."""
    day: str = Field(description="Day identifier, e.g., 'Day 2' or 'Tuesday'")
    period: Optional[str] = Field(default=None, description="Period identifier, e.g., '6' or 'P6'")
    start_time: str = Field(description="Start time in HH:MM format, e.g., '12:30'")
    end_time: str = Field(description="End time in HH:MM format, e.g., '13:20'")
    location: str = Field(default="Staff room", description="Consultation location")
    capacity: int = Field(default=2, ge=1, le=10, description="Max simultaneous students")


class ExtractedTimetable(BaseModel):
    """Full extracted weekly timetable including teaching periods and published office hours."""
    entries: List[ExtractedEntry] = Field(default_factory=list)
    office_hours: List[ExtractedOfficeHour] = Field(default_factory=list)
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    source_type: str = Field(default="mock", description="'mock', 'excel_xlsx', 'image_pixel_ocr', etc.")
    sheets_found: List[str] = Field(default_factory=list)


class BaseTimetableExtractor(ABC):
    """Abstract base class for timetable screenshot extractors."""

    @abstractmethod
    def extract_timetable(self, image_input: str | bytes) -> ExtractedTimetable:
        """
        Extract structured timetable data from a screenshot.
        Input can be a filepath (str) or raw image bytes.
        """
        pass
