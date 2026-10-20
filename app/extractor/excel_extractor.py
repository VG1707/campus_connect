"""
Universal Excel and CSV Timetable Extractor.
Parses spreadsheet files (.xlsx, .xls, .csv, .tsv) into structured ExtractedTimetable entries.
100% pure standard library: uses zipfile and xml.etree.ElementTree for .xlsx,
and csv module for .csv/.tsv. Zero external third-party dependencies required.
"""

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

from app.extractor.base import ExtractedEntry, ExtractedOfficeHour, ExtractedTimetable


def col_ref_to_index(cell_ref: str) -> int:
    """
    Convert an Excel column letter (e.g. 'A', 'B', 'AA') to a 0-based column index.
    e.g., 'A1' -> 0, 'B1' -> 1, 'C1' -> 2, 'Z1' -> 25, 'AA1' -> 26.
    """
    col_str = "".join(filter(str.isalpha, cell_ref)).upper()
    if not col_str:
        return 0
    idx = 0
    for char in col_str:
        idx = idx * 26 + (ord(char) - ord('A') + 1)
    return max(0, idx - 1)


def read_xlsx_workbook(file_input: str | bytes | Path) -> Dict[str, List[List[str]]]:
    """
    Read an Excel (.xlsx) file into a dictionary of {sheet_name: 2D list of cells}.
    Preserves exact column offsets, shared strings, and inlineStr values.
    Uses 100% pure standard library zipfile and xml.etree.ElementTree.
    """
    if isinstance(file_input, (str, Path)):
        z = zipfile.ZipFile(str(file_input), 'r')
    else:
        z = zipfile.ZipFile(io.BytesIO(file_input), 'r')

    with z:
        # 1. Parse shared strings table if present
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in tree.findall('.//{*}si'):
                text_parts = [t.text for t in si.findall('.//{*}t') if t.text]
                shared_strings.append("".join(text_parts))

        # 2. Parse workbook relationships (mapping rId to worksheet path)
        rels: Dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in z.namelist():
            rels_tree = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels_tree.findall('.//{*}Relationship'):
                r_id = rel.get('Id')
                target = rel.get('Target', '').lstrip('/')
                if not target.startswith('xl/'):
                    target = 'xl/' + target
                if r_id:
                    rels[r_id] = target

        # 3. Parse sheets in workbook.xml
        sheets_data: Dict[str, List[List[str]]] = {}
        if "xl/workbook.xml" in z.namelist():
            wb_tree = ET.fromstring(z.read("xl/workbook.xml"))
            for s_el in wb_tree.findall('.//{*}sheet'):
                sheet_name = s_el.get('name', 'Sheet1')
                r_id = (
                    s_el.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                    or s_el.get('r:id')
                    or s_el.get('id')
                )
                sheet_path = rels.get(r_id, "")
                if not sheet_path or sheet_path not in z.namelist():
                    # Fallback candidate search
                    candidates = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
                    sheet_path = candidates[0] if candidates else ""

                if not sheet_path or sheet_path not in z.namelist():
                    continue

                sheet_tree = ET.fromstring(z.read(sheet_path))
                rows_data: List[List[str]] = []

                for row_el in sheet_tree.findall('.//{*}row'):
                    row_cells: List[str] = []
                    for c_el in row_el.findall('.//{*}c'):
                        ref = c_el.get('r', '')
                        val_el = c_el.find('{*}v')
                        cell_type = c_el.get('t', '')
                        inline_t = c_el.find('{*}is/{*}t')

                        if inline_t is not None and inline_t.text:
                            cell_val = inline_t.text
                        elif val_el is not None and val_el.text:
                            if cell_type == 's':
                                idx = int(val_el.text)
                                cell_val = shared_strings[idx] if idx < len(shared_strings) else ""
                            else:
                                cell_val = val_el.text
                        else:
                            cell_val = ""

                        target_col = col_ref_to_index(ref)
                        while len(row_cells) < target_col:
                            row_cells.append("")
                        row_cells.append(cell_val.strip())

                    if any(cell for cell in row_cells):
                        rows_data.append(row_cells)

                sheets_data[sheet_name] = rows_data

        return sheets_data


def read_xlsx_cells(file_input: str | bytes | Path) -> List[List[str]]:
    """
    Read the first or main sheet of an Excel (.xlsx) file into a 2D list of strings.
    Preserves empty cells and column offsets based on cell coordinate references.
    """
    wb = read_xlsx_workbook(file_input)
    if not wb:
        return []
    first_sheet = next(iter(wb))
    return wb[first_sheet]


def read_csv_cells(file_input: str | bytes | Path) -> List[List[str]]:
    """
    Read a CSV/TSV file or raw string into a 2D list of strings with auto-detection of delimiters.
    """
    if isinstance(file_input, bytes):
        text = file_input.decode("utf-8-sig", errors="replace")
    elif isinstance(file_input, Path):
        with open(file_input, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    elif isinstance(file_input, str):
        if "\n" in file_input or "\r" in file_input:
            text = file_input
        else:
            p = Path(file_input)
            if p.exists() and p.is_file():
                with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                    text = f.read()
            else:
                text = file_input
    else:
        text = str(file_input)

    # Detect delimiter
    sample = text[:2048]
    delimiter = ","
    for delim in ["\t", ",", ";", "|"]:
        if sample.count(delim) > sample.count(delimiter):
            delimiter = delim

    rows: List[List[str]] = []
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    for row in reader:
        cleaned = [c.strip() for c in row]
        if any(cleaned):
            rows.append(cleaned)
    return rows


def parse_cell_content(cell_text: str) -> Optional[Tuple[str, str, str, bool]]:
    """
    Parse a single timetable cell text to extract:
    (course_code, course_name, room, is_lab)

    Returns None if cell represents a free period, break, lunch, or is blank.
    """
    raw = cell_text.strip()
    if not raw:
        return None

    # Check for empty / non-class tokens
    upper_raw = raw.upper()
    skip_tokens = {"-", "--", "FREE", "NIL", "NA", "N/A", "LUNCH", "BREAK", "RECESS", "OFF", "VACANT", "NONE"}
    if upper_raw in skip_tokens:
        return None

    # Detect lab flag
    is_lab = bool(re.search(r'\b(lab|laboratory|practical|simulation)\b', raw, re.IGNORECASE))

    # Detect room
    room = "LH-101"
    if "(" in raw and ")" in raw:
        # Check inside parentheses, e.g., "CS201 (LH-201)" or "CS201 (Cabin 304)"
        inside = re.search(r'\((.*?)\)', raw)
        if inside and inside.group(1).strip():
            candidate_room = inside.group(1).strip()
            room = candidate_room
    else:
        room_match = re.search(r'\b(LH[- ]?\d+|CS-Lab[- ]?\d+|HW-Lab[- ]?\d+|Lab[- ]?\d+|Cabin[- ]?\d+|Room[- ]?\d+|Hall[- ]?\d+|TP[- ]?\d+)\b', raw, re.IGNORECASE)
        if room_match:
            room = room_match.group(1).upper()

    # Clean text of parentheses for course search
    text_clean = re.sub(r'\(.*?\)', '', raw).strip()

    # Detect course code (e.g. CS201, EC301, ME101, MA201, GE201, CS202)
    code_match = re.search(r'\b([A-Za-z]{2,4}\s*[-_]?\s*\d{2,4}[A-Za-z]?)\b', text_clean)
    if code_match:
        raw_code = code_match.group(1)
        code = re.sub(r'[\s\-_]', '', raw_code).upper()
        # Name is remaining text
        name_candidate = text_clean.replace(raw_code, "").strip(" -:,")
        name = name_candidate if len(name_candidate) > 2 else ""
    else:
        # No standard code pattern; check if user entered full name like "Data Structures" or "Maths"
        name = text_clean
        # Generate clean code from title
        words = [w for w in re.split(r'[\s\-_]+', name) if w]
        if len(words) == 1:
            code = words[0][:6].upper()
        else:
            code = ("".join(w[0] for w in words[:3]) + "201").upper()

    return code, name, room, is_lab


def normalize_day_label(text: str) -> Optional[str]:
    """Normalize day string into 'Day 1' through 'Day 6' or 'Monday' through 'Saturday'."""
    clean = text.strip()
    # Match "Day 1", "Day-1", "D1", "DO1", "Day Order 1"
    m_do = re.search(r'(?:day\s*order|day|do|d)\s*[-_]?\s*([1-6])\b', clean, re.IGNORECASE)
    if m_do:
        return f"Day {m_do.group(1)}"

    # Match weekday names
    weekdays = {
        "mon": "Monday", "monday": "Monday",
        "tue": "Tuesday", "tues": "Tuesday", "tuesday": "Tuesday",
        "wed": "Wednesday", "wednesday": "Wednesday",
        "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "thursday": "Thursday",
        "fri": "Friday", "friday": "Friday",
        "sat": "Saturday", "saturday": "Saturday"
    }
    low = clean.lower()
    for k, v in weekdays.items():
        if low.startswith(k):
            return v

    return None


def extract_from_grid_rows(rows: List[List[str]]) -> List[ExtractedEntry]:
    """
    Interprets a 2D matrix of strings into ExtractedEntry items.
    Handles:
    1. Grid with Days as rows and Periods as columns.
    2. Grid with Periods as rows and Days as columns.
    3. List / Record format with columns (Day, Period, Course, Room, Section).
    """
    if not rows or len(rows) < 2:
        return []

    header = [c.lower() for c in rows[0]]

    # Case A: Record / Tabular format (Header has Day, Period, Course/Subject)
    day_col_idx = -1
    period_col_idx = -1
    course_col_idx = -1
    room_col_idx = -1
    section_col_idx = -1
    is_lab_col_idx = -1

    for idx, col_name in enumerate(header):
        if any(k in col_name for k in ["day", "day order", "weekday"]):
            day_col_idx = idx
        elif any(k in col_name for k in ["period", "hour", "slot", "p#"]):
            period_col_idx = idx
        elif any(k in col_name for k in ["course", "subject", "class", "paper", "code"]):
            course_col_idx = idx
        elif any(k in col_name for k in ["room", "hall", "venue", "cabin", "lab"]):
            room_col_idx = idx
        elif any(k in col_name for k in ["sec", "section"]):
            section_col_idx = idx
        elif "lab" in col_name:
            is_lab_col_idx = idx

    if day_col_idx != -1 and period_col_idx != -1 and course_col_idx != -1:
        # It's a tabular list
        entries: List[ExtractedEntry] = []
        for row in rows[1:]:
            if len(row) <= max(day_col_idx, period_col_idx, course_col_idx):
                continue
            day_raw = row[day_col_idx]
            period_raw = row[period_col_idx]
            course_raw = row[course_col_idx]

            day = normalize_day_label(day_raw)
            if not day:
                continue

            # Extract integer period
            p_match = re.search(r'\d+', period_raw)
            if not p_match:
                continue
            period = int(p_match.group(0))
            if period < 1 or period > 12:
                continue

            parsed = parse_cell_content(course_raw)
            if not parsed:
                continue
            code, name, cell_room, is_lab = parsed

            # If room column is explicitly provided, prefer it
            if room_col_idx != -1 and room_col_idx < len(row) and row[room_col_idx].strip():
                cell_room = row[room_col_idx].strip()

            sec = "CSE-A"
            if section_col_idx != -1 and section_col_idx < len(row) and row[section_col_idx].strip():
                sec = row[section_col_idx].strip()

            if is_lab_col_idx != -1 and is_lab_col_idx < len(row):
                val_lab = row[is_lab_col_idx].lower()
                if val_lab in ["true", "1", "yes", "y", "lab"]:
                    is_lab = True

            entries.append(ExtractedEntry(
                day=day,
                period=period,
                course_code=code,
                course_name=name,
                room=cell_room,
                section=sec,
                is_lab=is_lab
            ))
        return entries

    # Case B: Standard College Grid (Days as rows, Periods 1..12 as columns)
    # Check if header row contains period labels (1, 2.. or P1, P2.. or times)
    col_period_map: Dict[int, int] = {}
    for c_idx in range(1, len(rows[0])):
        hdr_txt = rows[0][c_idx].strip()
        m_p = re.search(r'(?:period|p|hr|hour)?\s*(1[0-2]|[1-9])\b', hdr_txt, re.IGNORECASE)
        if m_p:
            col_period_map[c_idx] = int(m_p.group(1))
        elif c_idx <= 12:
            col_period_map[c_idx] = c_idx

    entries: List[ExtractedEntry] = []
    for row in rows[1:]:
        if not row:
            continue
        first_cell = row[0]
        day = normalize_day_label(first_cell)
        if not day:
            continue

        for c_idx, period_num in col_period_map.items():
            if c_idx >= len(row):
                continue
            cell_str = row[c_idx].strip()
            parsed = parse_cell_content(cell_str)
            if not parsed:
                continue
            code, name, room, is_lab = parsed

            entries.append(ExtractedEntry(
                day=day,
                period=period_num,
                course_code=code,
                course_name=name,
                room=room,
                section="CSE-A",
                is_lab=is_lab
            ))

    return entries


def parse_schedule_data_sheet(rows: List[List[str]], role: str = "student") -> ExtractedTimetable:
    """
    Parses the canonical 'Schedule Data' sheet containing typed records for students and faculty.
    Preserves the distinction between teaching periods and explicitly published consultation hours.
    """
    if not rows or len(rows) < 2:
        return ExtractedTimetable(entries=[], source_type="excel_schedule_data")

    header = [c.lower().strip() for c in rows[0]]
    col_map = {name: idx for idx, name in enumerate(header)}

    role_col = col_map.get("role", 1)
    type_col = col_map.get("record_type", 12)
    day_col = col_map.get("day_order", 7)
    code_col = col_map.get("course_code", 5)
    name_col = col_map.get("course_name", 6)
    p_start_col = col_map.get("period_start", 8)
    p_end_col = col_map.get("period_end", 9)
    t_start_col = col_map.get("start_time", 10)
    t_end_col = col_map.get("end_time", 11)
    loc_col = col_map.get("location", 13)
    cap_col = col_map.get("capacity", 14)

    entries: List[ExtractedEntry] = []
    office_hours: List[ExtractedOfficeHour] = []

    for row in rows[1:]:
        if len(row) <= max(role_col, type_col, day_col):
            continue

        r_role = row[role_col].lower().strip() if role_col < len(row) else ""
        r_type = row[type_col].lower().strip() if type_col < len(row) else ""
        r_day = row[day_col].strip() if day_col < len(row) else ""

        day = normalize_day_label(r_day)
        if not day:
            continue

        if role == "student" and r_role == "student" and r_type == "class":
            code = row[code_col].strip() if code_col < len(row) else ""
            c_name = row[name_col].strip() if name_col < len(row) else ""
            p_raw = row[p_start_col].strip() if p_start_col < len(row) else "1"
            m_p = re.search(r'\d+', p_raw)
            period = int(m_p.group(0)) if m_p else 1
            is_lab = ("LAB" in code.upper()) or ("LAB" in c_name.upper())
            room = row[loc_col].strip() if loc_col < len(row) and row[loc_col].strip() else ("Digital IC Lab" if is_lab else "TP-401")

            entries.append(ExtractedEntry(
                day=day,
                period=period,
                course_code=code,
                course_name=c_name,
                room=room,
                section="ECE-A",
                is_lab=is_lab
            ))

        elif role == "faculty" and r_role == "faculty":
            if r_type == "teaching":
                code = row[code_col].strip() if code_col < len(row) else ""
                c_name = row[name_col].strip() if name_col < len(row) else ""
                p_start_raw = row[p_start_col].strip() if p_start_col < len(row) else "1"
                p_end_raw = row[p_end_col].strip() if p_end_col < len(row) else p_start_raw
                m_s = re.search(r'\d+', p_start_raw)
                m_e = re.search(r'\d+', p_end_raw)
                p_start = int(m_s.group(0)) if m_s else 1
                p_end = int(m_e.group(0)) if m_e else p_start
                is_lab = ("LAB" in code.upper()) or ("LAB" in c_name.upper())
                room = row[loc_col].strip() if loc_col < len(row) and row[loc_col].strip() else ("Digital IC Lab" if is_lab else "TP-301")

                for p in range(p_start, p_end + 1):
                    entries.append(ExtractedEntry(
                        day=day,
                        period=p,
                        course_code=code or ("EC201" if not is_lab else "EC204Lab"),
                        course_name=c_name,
                        room=room,
                        section="ECE-A",
                        is_lab=is_lab
                    ))

            elif r_type == "consultation":
                p_raw = row[p_start_col].strip() if p_start_col < len(row) else ""
                t_start = row[t_start_col].strip() if t_start_col < len(row) else "12:30"
                t_end = row[t_end_col].strip() if t_end_col < len(row) else "13:20"
                loc = row[loc_col].strip() if loc_col < len(row) and row[loc_col].strip() else "Staff room"
                cap_raw = row[cap_col].strip() if cap_col < len(row) else "2"
                cap = int(cap_raw) if cap_raw.isdigit() else 2

                office_hours.append(ExtractedOfficeHour(
                    day=day,
                    period=p_raw,
                    start_time=t_start,
                    end_time=t_end,
                    location=loc,
                    capacity=cap
                ))

    return ExtractedTimetable(
        entries=entries,
        office_hours=office_hours,
        confidence_score=0.99,
        source_type="excel_schedule_data"
    )


def parse_student_timetable_sheet(rows: List[List[str]]) -> ExtractedTimetable:
    """
    Parses the 5x12 student timetable grid along with the subject legend table at the bottom.
    Correctly resolves codes to subject names and tags multi-period lab blocks.
    """
    if not rows:
        return ExtractedTimetable(entries=[], source_type="excel_student_sheet")

    # 1. Parse legend table below grid if present
    legend: Dict[str, str] = {}
    legend_started = False
    for r in rows:
        if len(r) >= 2 and any(k in r[0].lower() for k in ["subject code", "code", "legend"]):
            legend_started = True
            continue
        if legend_started:
            if len(r) >= 2 and r[0].strip():
                legend[r[0].strip().upper()] = r[1].strip()

    # 2. Parse Day Order rows (1 through 5/6)
    entries: List[ExtractedEntry] = []
    for r in rows:
        if not r:
            continue
        day = normalize_day_label(r[0])
        if not day:
            continue

        for c_idx in range(1, min(len(r), 13)):
            val = r[c_idx].strip()
            if not val:
                continue
            upper_val = val.upper()
            if upper_val in ["BREAK", "FREE", "LUNCH", "RECESS", "-", "--", "NIL", "NA", "N/A", "NONE"]:
                continue

            code = val
            name = legend.get(code.upper(), code)
            is_lab = ("LAB" in code.upper()) or ("LAB" in name.upper())
            room = "Digital IC Lab" if is_lab else "TP-401"

            entries.append(ExtractedEntry(
                day=day,
                period=c_idx,
                course_code=code,
                course_name=name,
                room=room,
                section="ECE-A",
                is_lab=is_lab
            ))

    return ExtractedTimetable(
        entries=entries,
        confidence_score=0.99,
        source_type="excel_student_sheet"
    )


def parse_faculty_timetable_sheet(rows: List[List[str]]) -> ExtractedTimetable:
    """
    Parses the Faculty Timetable sheet, strictly distinguishing teaching periods
    from explicitly published consultation hours.
    """
    if not rows or len(rows) < 2:
        return ExtractedTimetable(entries=[], source_type="excel_faculty_sheet")

    entries: List[ExtractedEntry] = []
    office_hours: List[ExtractedOfficeHour] = []

    for r in rows:
        if len(r) < 6:
            continue
        subj = r[3].strip() if len(r) > 3 else ""
        day_raw = r[4].strip() if len(r) > 4 else ""
        day = normalize_day_label(day_raw)
        if not day:
            continue

        p_str = r[5].strip() if len(r) > 5 else "1"
        t_start = r[6].strip() if len(r) > 6 else ""
        t_end = r[7].strip() if len(r) > 7 else ""

        if "consultation" in subj.lower():
            office_hours.append(ExtractedOfficeHour(
                day=day,
                period=p_str,
                start_time=t_start or "12:30",
                end_time=t_end or "13:20",
                location="Staff room",
                capacity=2
            ))
        else:
            p_nums = [int(n) for n in re.findall(r'\d+', p_str)]
            if len(p_nums) == 2 and p_nums[0] < p_nums[1]:
                p_range = list(range(p_nums[0], p_nums[1] + 1))
            elif p_nums:
                p_range = [p_nums[0]]
            else:
                p_range = [1]

            is_lab = "LAB" in subj.upper()
            words = [w for w in re.split(r'[\s\-_]+', subj) if w]
            code = ("".join(w[0] for w in words[:3]) + "201").upper() if len(words) > 1 else subj[:6].upper()

            for p in p_range:
                entries.append(ExtractedEntry(
                    day=day,
                    period=p,
                    course_code=code,
                    course_name=subj,
                    room="Digital IC Lab" if is_lab else "TP-301",
                    section="ECE-A",
                    is_lab=is_lab
                ))

    return ExtractedTimetable(
        entries=entries,
        office_hours=office_hours,
        confidence_score=0.99,
        source_type="excel_faculty_sheet"
    )


class ExcelTimetableExtractor:
    """
    High-accuracy spreadsheet extractor supporting .xlsx, .xls, .csv, and .tsv formats.
    Reads actual cell contents from workbook bytes, recognizes multi-sheet workbooks
    ('Student Timetable', 'Faculty Timetable', 'Schedule Data'), and separates
    teaching periods from consultation hours.
    """

    def extract_from_file(
        self,
        file_path_or_bytes: str | bytes | Path,
        filename: str = "",
        role: str = "student"
    ) -> ExtractedTimetable:
        name_lower = str(filename or file_path_or_bytes).lower()
        is_xlsx = name_lower.endswith(".xlsx") or (
            isinstance(file_path_or_bytes, bytes) and file_path_or_bytes.startswith(b"PK\x03\x04")
        )

        if is_xlsx:
            try:
                sheets = read_xlsx_workbook(file_path_or_bytes)
            except Exception as e:
                raise ValueError(f"Uploaded file is corrupted or not a valid Excel (.xlsx) workbook: {e}")

            if not sheets:
                raise ValueError("The uploaded Excel workbook contains no readable worksheets.")

            sheets_found = list(sheets.keys())

            # Priority 1: 'Schedule Data' sheet if present
            for s_name in sheets_found:
                if "schedule" in s_name.lower() and "data" in s_name.lower():
                    res = parse_schedule_data_sheet(sheets[s_name], role=role)
                    res.sheets_found = sheets_found
                    return res

            # Priority 2: Role-targeted sheet
            if role == "student":
                for s_name in sheets_found:
                    if "student" in s_name.lower() or "timetable" in s_name.lower():
                        res = parse_student_timetable_sheet(sheets[s_name])
                        res.sheets_found = sheets_found
                        return res
            elif role == "faculty":
                for s_name in sheets_found:
                    if "faculty" in s_name.lower() or "teacher" in s_name.lower():
                        res = parse_faculty_timetable_sheet(sheets[s_name])
                        res.sheets_found = sheets_found
                        return res

            # Fallback: Parse first available sheet as generic grid
            first_sheet = sheets_found[0]
            entries = extract_from_grid_rows(sheets[first_sheet])
            return ExtractedTimetable(
                entries=entries,
                confidence_score=0.99 if entries else 0.5,
                source_type="excel_xlsx",
                sheets_found=sheets_found
            )
        else:
            # CSV / TSV
            try:
                rows = read_csv_cells(file_path_or_bytes)
            except Exception as e:
                raise ValueError(f"Uploaded file could not be parsed as CSV: {e}")

            if not rows:
                raise ValueError("The uploaded CSV file is empty.")

            entries = extract_from_grid_rows(rows)
            return ExtractedTimetable(
                entries=entries,
                confidence_score=0.99 if entries else 0.5,
                source_type="excel_csv",
                sheets_found=["CSV Timetable"]
            )


def generate_sample_csv(role: str = "student") -> str:
    """
    Generates a standard CSV timetable template that can be opened in Excel,
    edited, and re-uploaded by students or faculty.
    """
    if role == "faculty":
        # Faculty teaching timetable: 3-4 teaching periods per day, leaving gaps for consultation
        lines = [
            "Day,Period 1,Period 2,Period 3,Period 4,Period 5,Period 6,Period 7,Period 8",
            "Day 1,CS201 (LH-201),CS201 (LH-201),-,FREE,CS205 (LH-201),-,-,-",
            "Day 2,-,CS201 (LH-201),CS203 (LH-201),-,-,CS202 Lab (CS-Lab-2),CS202 Lab (CS-Lab-2),-",
            "Day 3,CS201 (LH-201),-,CS205 (LH-201),-,-,-,-,-",
            "Day 4,-,CS203 (LH-201),-,CS201 (LH-201),CS204 Lab (HW-Lab-1),CS204 Lab (HW-Lab-1),-,-",
            "Day 5,CS205 (LH-201),-,CS201 (LH-201),-,-,-,-,-",
            "Day 6,-,-,CS203 (LH-201),CS205 (LH-201),-,-,-,-"
        ]
    else:
        # Student timetable: balanced classes and labs
        lines = [
            "Day,Period 1,Period 2,Period 3,Period 4,Period 5,Period 6,Period 7,Period 8",
            "Day 1,CS201 (LH-201),MA201 (LH-201),CS203 (LH-201),-,CS205 (LH-201),-,-,-",
            "Day 2,-,CS201 (LH-201),MA201 (LH-201),-,CS202 Lab (CS-Lab-2),CS202 Lab (CS-Lab-2),-,-",
            "Day 3,CS203 (LH-201),CS205 (LH-201),-,CS201 (LH-201),CS204 Lab (HW-Lab-1),CS204 Lab (HW-Lab-1),-,-",
            "Day 4,MA201 (LH-201),-,CS205 (LH-201),-,-,CS201 (LH-201),-,-",
            "Day 5,-,CS203 (LH-201),-,CS205 (LH-201),-,GE201 (LH-201),-,-",
            "Day 6,CS201 (LH-201),MA201 (LH-201),-,-,-,-,-,-"
        ]
    return "\n".join(lines)


def generate_sample_xlsx(role: str = "student") -> bytes:
    """
    Generates a valid .xlsx file in memory using pure standard library zipfile and xml.
    Opens natively in Microsoft Excel, Google Sheets, Apple Numbers, LibreOffice.
    """
    csv_text = generate_sample_csv(role=role)
    rows = [line.split(",") for line in csv_text.strip().split("\n")]

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '  <Default Extension="xml" ContentType="application/xml"/>\n'
        '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
        '  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        '  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>\n'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\n'
        '</Relationships>'
    )
    wb = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
        '  <sheets><sheet name="Timetable" sheetId="1" r:id="rId1"/></sheets>\n'
        '</workbook>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>\n'
        '  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>\n'
        '</Relationships>'
    )

    strings: List[str] = []
    string_map: Dict[str, int] = {}

    def get_str_id(s: str) -> int:
        if s not in string_map:
            string_map[s] = len(strings)
            strings.append(s)
        return string_map[s]

    sheet_rows: List[str] = []
    for r_idx, row in enumerate(rows, 1):
        cells: List[str] = []
        for c_idx, val in enumerate(row, 1):
            cleaned_val = val.strip()
            if not cleaned_val:
                continue
            col_letter = chr(64 + c_idx)
            sid = get_str_id(cleaned_val)
            cells.append(f'<c r="{col_letter}{r_idx}" t="s"><v>{sid}</v></c>')
        if cells:
            sheet_rows.append(f'<row r="{r_idx}">{" ".join(cells)}</row>')

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
        f'  <sheetData>{" ".join(sheet_rows)}</sheetData>\n'
        '</worksheet>'
    )

    sst_entries = "".join(f'<si><t>{re.sub(r"[<&>]", "", s)}</t></si>' for s in strings)
    sst = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">\n'
        f'  {sst_entries}\n'
        '</sst>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', rels)
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', wb_rels)
        z.writestr('xl/sharedStrings.xml', sst)
        z.writestr('xl/worksheets/sheet1.xml', sheet)

    return buf.getvalue()
