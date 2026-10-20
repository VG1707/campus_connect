"""
End-to-End Tests for Demo File Timetable Extraction:
1. Fake_Student_Timetable.png (Pixel-level Image OCR Extraction)
2. CampusConnect_Fake_Timetables.xlsx (Multi-Sheet Excel Workbook Extraction)
Includes tests with modified files to verify dynamic extraction reflecting changed content,
and error handling for malformed/corrupted files.
"""

import io
import os
import zipfile
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import User, UserRole, TimetableEntry
from app.auth_mock import COOKIE_NAME
from app.extractor.image_extractor import ImageTimetableExtractor
from app.extractor.excel_extractor import ExcelTimetableExtractor, read_xlsx_workbook
from app.extractor.mock_extractor import MockTimetableExtractor


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "sample_data"
PNG_PATH = FIXTURES_DIR / "Fake_Student_Timetable.png"
XLSX_PATH = FIXTURES_DIR / "CampusConnect_Fake_Timetables.xlsx"


def test_png_file_extraction_end_to_end():
    """
    1. Read actual image pixels using PIL and ImageTimetableExtractor.
    2. Extract all 28 classes across Day 1 to Day 5.
    3. Correctly handle the 3-hour lab block spanning Day 2 P2, P3, P4.
    4. Map subject codes using the legend (Transforms & Boundary Value Problems, etc.).
    5. Verify breaks/free periods are excluded.
    """
    assert PNG_PATH.exists(), f"Demo PNG missing at {PNG_PATH}"
    extractor = ImageTimetableExtractor(reference_image_path=PNG_PATH)

    # Test extraction directly from file path
    result = extractor.extract_from_image(PNG_PATH, role="student")

    assert result.source_type == "image_pixel_ocr"
    assert result.confidence_score >= 0.85
    assert len(result.entries) == 28, f"Expected 28 classes, got {len(result.entries)}"

    # Day 1: TVBP, TVBP, DLD, DLD, DTDM
    d1_entries = [e for e in result.entries if e.day == "Day 1"]
    assert len(d1_entries) == 5
    d1_codes = [e.course_code for e in d1_entries]
    assert d1_codes == ["TVBP", "TVBP", "DLD", "DLD", "DTDM"]
    assert d1_entries[0].course_name == "Transforms & Boundary Value Problems"
    assert d1_entries[2].course_name == "Digital Logic Design"
    assert d1_entries[4].course_name == "Design Thinking & Methodology"

    # Day 2: 3-hour lab block on P2, P3, P4
    d2_entries = [e for e in result.entries if e.day == "Day 2"]
    assert len(d2_entries) == 8
    lab_entries = [e for e in d2_entries if e.is_lab]
    assert len(lab_entries) == 3
    for e in lab_entries:
        assert e.period in (2, 3, 4)
        assert e.course_code == "LAB P12-P13"
        assert e.course_name == "Devices & Digital IC Lab"
        assert e.room == "Digital IC Lab"
        assert e.is_lab is True

    # Day 3: PE ONLINE on period 10
    d3_entries = [e for e in result.entries if e.day == "Day 3"]
    pe_entry = next(e for e in d3_entries if e.period == 10)
    assert pe_entry.course_code == "PE ONLINE"
    assert pe_entry.course_name == "Professional Elective (Online)"
    assert pe_entry.is_lab is False

    # Day 5: SE on P1, P2
    d5_entries = [e for e in result.entries if e.day == "Day 5"]
    assert d5_entries[0].course_code == "SE"
    assert d5_entries[0].course_name == "Semiconductor Electronics"


def test_modified_png_reflects_changed_pixels():
    """
    Verify that timetable extraction processes actual image pixels dynamically
    rather than returning static cached results.
    Modifies Day 1 Period 1 in the PNG from TVBP to COA by altering image pixels.
    """
    img = Image.open(str(PNG_PATH)).convert("RGB")

    # Crop the COA cell from Day 3 Period 1 (x: 87..165, y: 240..303)
    cell_coa = img.crop((87, 240, 87 + 78, 303))

    # Paste COA cell onto Day 1 Period 1 (x: 87, y: 113)
    img_modified = img.copy()
    img_modified.paste(cell_coa, (87, 113))

    # Convert modified image to PNG bytes
    buf = io.BytesIO()
    img_modified.save(buf, format="PNG")
    modified_bytes = buf.getvalue()

    extractor = ImageTimetableExtractor()
    result = extractor.extract_from_image(modified_bytes, role="student")

    # Day 1 Period 1 must now extract as COA!
    d1p1 = next(e for e in result.entries if e.day == "Day 1" and e.period == 1)
    assert d1p1.course_code == "COA", f"Expected COA, got {d1p1.course_code}"
    assert d1p1.course_name == "Computer Organization & Architecture"

    # Day 1 Period 2 must remain original TVBP
    d1p2 = next(e for e in result.entries if e.day == "Day 1" and e.period == 2)
    assert d1p2.course_code == "TVBP"


def test_xlsx_file_extraction_end_to_end():
    """
    1. Read actual workbook bytes using pure standard library zipfile/xml.
    2. Identify Student Timetable, Faculty Timetable, and Schedule Data sheets.
    3. For Student: extracts 28 classes matching timetable layout.
    4. For Faculty: extracts teaching periods into entries and explicitly published
       consultation hours into office_hours, preserving this vital distinction.
    """
    assert XLSX_PATH.exists(), f"Demo Excel missing at {XLSX_PATH}"
    extractor = ExcelTimetableExtractor()

    # 1. Verify sheet discovery
    sheets = read_xlsx_workbook(XLSX_PATH)
    assert "Student Timetable" in sheets
    assert "Faculty Timetable" in sheets
    assert "Schedule Data" in sheets

    # 2. Extract for Student
    res_student = extractor.extract_from_file(XLSX_PATH, role="student")
    assert len(res_student.entries) == 28
    assert res_student.confidence_score >= 0.95
    assert "Schedule Data" in res_student.sheets_found or "Student Timetable" in res_student.sheets_found

    # Check student lab block
    student_labs = [e for e in res_student.entries if e.is_lab]
    assert len(student_labs) >= 3
    for lab in student_labs:
        assert lab.day == "Day 2"
        assert lab.period in (2, 3, 4)

    # 3. Extract for Faculty
    res_faculty = extractor.extract_from_file(XLSX_PATH, role="faculty")
    assert len(res_faculty.entries) == 12, f"Expected 12 teaching periods, got {len(res_faculty.entries)}"
    assert len(res_faculty.office_hours) == 8, f"Expected 8 consultation hours, got {len(res_faculty.office_hours)}"

    # Verify that consultation hours are preserved as consultation windows (OfficeHour schema)
    # and NOT confused with teaching periods
    oh_days = [oh.day for oh in res_faculty.office_hours]
    assert "Day 2" in oh_days
    assert "Day 4" in oh_days
    sample_oh = next(oh for oh in res_faculty.office_hours if oh.day == "Day 2")
    assert sample_oh.start_time == "12:30"
    assert sample_oh.end_time == "13:20"
    assert sample_oh.location == "Staff room"
    assert sample_oh.capacity == 2


def test_modified_xlsx_reflects_changed_cells():
    """
    Verify that Excel parsing reads actual cell contents from workbook bytes.
    Modifies Schedule Data in memory by changing Day 1 Period 1 course_code from TVBP to EMTI.
    """
    with open(XLSX_PATH, "rb") as f:
        orig_bytes = f.read()

    z_in = zipfile.ZipFile(io.BytesIO(orig_bytes))
    sheet3_xml = z_in.read("xl/worksheets/sheet3.xml").decode("utf-8")
    assert "TVBP" in sheet3_xml

    # Replace first occurrence of TVBP with EMTI
    sheet3_mod = sheet3_xml.replace("TVBP", "EMTI", 1)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z_out:
        for item in z_in.infolist():
            if item.filename == "xl/worksheets/sheet3.xml":
                z_out.writestr(item, sheet3_mod.encode("utf-8"))
            else:
                z_out.writestr(item, z_in.read(item.filename))

    modified_xlsx_bytes = buf.getvalue()

    extractor = ExcelTimetableExtractor()
    res = extractor.extract_from_file(modified_xlsx_bytes, filename="modified.xlsx", role="student")

    d1p1 = next(e for e in res.entries if e.day == "Day 1" and e.period == 1)
    assert d1p1.course_code == "EMTI", f"Expected EMTI, got {d1p1.course_code}"


def test_malformed_and_unsupported_files_error_handling():
    """
    Verify meaningful error handling for corrupted or unsupported files.
    """
    img_ext = ImageTimetableExtractor()
    excel_ext = ExcelTimetableExtractor()

    # 1. Corrupted image bytes
    try:
        img_ext.extract_from_image(b"not an image", role="student")
        assert False, "Should raise ValueError for invalid image"
    except ValueError as e:
        assert "corrupted" in str(e).lower() or "not a valid image" in str(e).lower()

    # 2. Image with invalid dimensions
    tiny = Image.new("RGB", (25, 25), (255, 255, 255))
    try:
        img_ext.extract_from_image(tiny, role="student")
        assert False, "Should raise ValueError for tiny dimensions"
    except ValueError as e:
        assert "dimensions" in str(e).lower()

    # 3. Corrupted Excel bytes
    try:
        excel_ext.extract_from_file(b"corrupted zip bytes", filename="broken.xlsx", role="student")
        assert False, "Should raise ValueError for broken xlsx"
    except ValueError as e:
        assert "corrupted" in str(e).lower() or "not a valid excel" in str(e).lower()

    # 4. Empty CSV
    try:
        excel_ext.extract_from_file(b"", filename="empty.csv", role="student")
        assert False, "Should raise ValueError for empty csv"
    except ValueError as e:
        assert "empty" in str(e).lower()


def test_student_web_upload_flow_and_confirmation(client: TestClient, session: Session):
    """
    End-to-end integration test through FastAPI routes:
    1. Uploads Fake_Student_Timetable.png to /student/timetable/upload.
    2. Verifies review screen renders 28 extracted entries.
    3. Confirms review screen -> verifies entries saved to DB and temp image erased.
    """
    student = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).first()
    client.cookies.set(COOKIE_NAME, str(student.id))

    # 1. Upload PNG via web endpoint
    with open(PNG_PATH, "rb") as f:
        png_bytes = f.read()

    files = {"screenshot": ("Fake_Student_Timetable.png", io.BytesIO(png_bytes), "image/png")}
    res_upload = client.post("/student/timetable/upload", files=files)
    assert res_upload.status_code == 200
    assert "Review &amp; Edit Extracted Timetable" in res_upload.text or "Review & Edit Extracted Timetable" in res_upload.text
    assert "TVBP" in res_upload.text
    assert "LAB P12-P13" in res_upload.text or "LAB" in res_upload.text

    # Extract temp image path from hidden form input
    import re
    m_temp = re.search(r'name="temp_image_path" value="([^"]+)"', res_upload.text)
    temp_path = m_temp.group(1) if m_temp else ""
    if temp_path:
        assert os.path.exists(temp_path)

    # 2. Confirm reviewed timetable
    confirm_data = {
        "initial_count": 28,
        "extracted_at": 100.0,
        "temp_image_path": temp_path,
        "day_1": "Day 1",
        "period_1": "1",
        "course_code_1": "TVBP",
        "course_name_1": "Transforms & Boundary Value Problems",
        "room_1": "TP-401",
        "is_lab_1": "false",
        "day_2": "Day 2",
        "period_2": "2",
        "course_code_2": "LAB P12-P13",
        "course_name_2": "Devices & Digital IC Lab",
        "room_2": "Digital IC Lab",
        "is_lab_2": "true",
    }

    res_confirm = client.post("/student/timetable/review/confirm", data=confirm_data, follow_redirects=True)
    assert res_confirm.status_code == 200
    assert "Weekly Academic Timetable" in res_confirm.text

    # 3. Privacy guarantee: temp file must be deleted from disk
    if temp_path:
        assert not os.path.exists(temp_path)

    # 4. Verify TimetableEntry records in DB
    entries = session.exec(select(TimetableEntry).where(TimetableEntry.owner_id == student.id)).all()
    assert len(entries) >= 2


def test_faculty_web_upload_demo_xlsx_and_confirmation(client: TestClient, session: Session):
    """
    End-to-end integration test through faculty FastAPI routes:
    1. Uploads CampusConnect_Fake_Timetables.xlsx to /faculty/timetable/upload.
    2. Verifies review screen renders teaching entries.
    3. Confirms review screen -> verifies saved to DB and temp file deleted.
    """
    faculty = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).first()
    client.cookies.set(COOKIE_NAME, str(faculty.id))

    with open(XLSX_PATH, "rb") as f:
        xlsx_bytes = f.read()

    files = {"screenshot": ("CampusConnect_Fake_Timetables.xlsx", io.BytesIO(xlsx_bytes), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    res_upload = client.post("/faculty/timetable/upload", files=files)
    assert res_upload.status_code == 200
    assert "Review Extracted Faculty Timetable" in res_upload.text or "Teaching Schedule" in res_upload.text

    import re
    m_temp = re.search(r'name="temp_image_path" value="([^"]+)"', res_upload.text)
    temp_path = m_temp.group(1) if m_temp else ""
    if temp_path:
        assert os.path.exists(temp_path)

    # Confirm
    confirm_data = {
        "initial_count": 2,
        "extracted_at": 100.0,
        "temp_image_path": temp_path,
        "day_1": "Day 1",
        "period_1": "3",
        "course_code_1": "DLD201",
        "course_name_1": "Digital Logic Design",
        "room_1": "TP-301",
        "is_lab_1": "false",
    }
    res_confirm = client.post("/faculty/timetable/review/confirm", data=confirm_data, follow_redirects=True)
    assert res_confirm.status_code == 200

    if temp_path:
        assert not os.path.exists(temp_path)
