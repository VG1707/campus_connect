# CampusConnect 🎓
**Deterministic Faculty Consultation & Availability Intersection Web Application**  
*Built for Design Thinking demonstration at an Indian Engineering College.*

---

## 📌 Purpose & Core Concept

When a college student wants to meet a professor to clarify a doubt or discuss a project, they typically face significant friction:
1. The student must memorize or manually cross-check their 8-period daily schedule against the faculty's consultation hours.
2. If the college uses a rotating **Day-Order system** (e.g., Day 1 to Day 6), holidays shift the cycle, causing confusion.
3. Students end up knocking on faculty doors during class hours or waiting outside cabins for hours.

**CampusConnect** solves this by deterministically intersecting the student's timetable with the faculty member's published office hours to surface conflict-free consultation slots, ranked by convenience and campus idle gaps.

> **Non-Goals**: CampusConnect is *not* a doubt-solving chatbot, quiz engine, or full university ERP. It is a focused, reliable availability matching tool.

---

## 🏛️ The Three-Layer Availability Model

CampusConnect enforces a strict separation between predictions, commitments, and confirmed bookings:

| Layer | Name | Description | Booking Behavior |
|---|---|---|---|
| **Layer 1** | **Timetable Prediction** (*"Likely Free"*) | Gaps in faculty teaching schedule where no class is assigned. | **Never bookable directly as confirmed.** Always marked: *"Likely free based on timetable - not a commitment"*. Submits as a pending on-demand request. |
| **Layer 2** | **Faculty Commitment** (*Recurring Office Hours*) | Explicit consultation hours set once by faculty (e.g., Day 3 14:00–15:00) with capacity, location, and allowed durations (5–50 min). | Primary source of meeting slots. Faculty can pause specific dates or cancel an occurrence with notifications. |
| **Layer 3** | **Booking Confirmation** | State machine governing requests. | Inside office hour: `auto_confirm=True` + remaining capacity $\to$ **instantly confirmed**. If `auto_confirm=False` or full $\to$ **pending**. Outside office hours $\to$ **always pending**. Confirmed bookings are never altered silently. |

---

## 🧩 Academic Calendar & Day-Order Rotation Engine

- **Config Switch**: Supports both **Day-Order Mode** (Day 1 through Day 6 cycle) and **Plain Weekday Mode** (Mon–Sat).
- **Holiday & Sunday Awareness**: Sundays, declared holidays, and exam days never generate consultation slots.
- **Dynamic Holiday Shifting**: When an unexpected holiday is declared (e.g., storm or local holiday), the day order pauses and cascades forward to the next working day without phase mismatch.

---

## 🔍 Deterministic Slot Matching Pipeline

The algorithm in `app/matching.py` executes in pure, testable Python:
1. Validates meeting duration (5–50 minutes in 5-minute increments).
2. Verifies student accountability: checks rate limits (max 3 pending requests) and no-show blocking (blocked at 2 no-shows).
3. Detects student timetable presence:
   - **With Timetable**: Filters out slots overlapping student classes, lab blocks, and existing meetings.
   - **Without Timetable (Manual Mode)**: Displays all valid faculty office hours with a prominent notification banner.
4. Filters out faculty teaching periods and active confirmed bookings.
5. Applies **Verified Schedule Changes** (faculty class swaps/cancellations); flags **Unverified Changes** with a warning badge without altering availability.
6. Ranks remaining slots using transparent scoring:
   - **Proximity**: Closeness to student's preferred window center (0–50 pts).
   - **Campus Idle Gap**: Bonus if the slot is immediately adjacent to a student's class (0–30 pts).
   - **Capacity Margin**: Bonus if multiple seats remain in the slot (0–20 pts).
   - Generates human-readable reasoning strings for every recommendation.

---

## 👥 Cohort Group Session Intelligence

When $\ge 3$ students have open pending requests on the same topic tag:
- The system evaluates whether those students are **actually free** during an upcoming office hour slot per their individual timetables.
- Students with timetable conflicts (e.g., busy in a laboratory) are **strictly excluded**.
- If $\ge 3$ free students match within office hour capacity, a `GroupSession` is suggested in the Faculty Inbox.
- If hosted, individual requests convert to confirmed seats, and students receive notifications (never revealing peer names or private descriptions).

---

## 🔬 Timetable Extraction & Human-in-the-Loop Review

CampusConnect supports offline extraction of uploaded timetables (PNG screenshots and multi-sheet Excel workbooks) processed directly on the backend without external cloud APIs:

### 1. PNG Screenshot Extraction (`Fake_Student_Timetable.png`)
- **Backend Processor**: `ImageTimetableExtractor` (`app/extractor/image_extractor.py`).
- **Pixel Grid Segmentation**: Uses PIL to validate image geometry (e.g., 1024x512) and maps the timetable into a 5-day rows by 12-period columns grid.
- **Cell Classification & Color Detection**:
  - Theory courses (Yellow / warm background).
  - Multi-period Lab blocks (Lavender / purple background).
  - Online courses (Light blue background).
  - Break periods (Gray background).
- **Normalized Jaccard Glyph Matching**: Extracts cell foreground pixels and matches against synthetic glyph representations (`TVBP`, `DLD`, `DTDM`, `LAB P12-P13`, `SSD`, `COA`, `EMTI`, `PE ONLINE`, `SE`, `BREAK`).
- **Legend Code Mapping**: Automatically maps abbreviations to official course titles via the embedded legend (e.g., `TVBP` $\to$ *Transforms & Boundary Value Problems*).
- **Multi-Period Lab Block Handling**: The 3-period lab block (`LAB P12-P13` on Day 2 Periods 2, 3, and 4) is correctly parsed and tagged with `is_lab=True`.

### 2. Multi-Sheet Excel Extraction (`CampusConnect_Fake_Timetables.xlsx`)
- **Backend Processor**: `ExcelTimetableExtractor` (`app/extractor/excel_extractor.py`).
- **Standard Library XML Parsing**: Uses pure Python `zipfile` and `xml.etree.ElementTree` without external Excel dependencies, parsing shared strings and `inlineStr` nodes.
- **Multi-Sheet Identification**:
  - `Student Timetable`: Maps the 5x12 grid and parses course entries and breaks.
  - `Faculty Timetable`: Identifies faculty teaching schedules across periods 1–12.
  - `Schedule Data`: Tabular format with `record_type`, `day_order`, `period`, `course_code`, `course_name`, `location`, `capacity`.
- **Teaching vs. Consultation Separation**:
  - `record_type == 'teaching'` $\to$ Mapped to `TimetableEntry` (blocks faculty availability).
  - `record_type == 'consultation'` / `Course == 'Consultation Hour'` $\to$ Mapped to `OfficeHour` / `ExtractedOfficeHour` (creates available Layer 2 consultation windows).

### 3. Human Review & Security Guarantees
- **Human Review Screen**: Uploaded timetables are presented in an editable review grid (`/student/timetable/upload` and `/faculty/timetable/upload`) where users can inspect, edit, or add rows before committing to the database.
- **Dynamic Content Reflection**: Extraction responds dynamically to file changes (e.g. modified pixels or modified cell values).
- **Meaningful Error Handling**: Corrupted images or malformed workbooks produce clear HTTP 400 error banners.
- **Immediate Media Deletion Guarantee**: The moment the user clicks Confirm or Cancel, temporary upload files are permanently unlinked from disk. Zero image or workbook bytes are retained on disk.
- **Accuracy Benchmark Dashboard**: Accessible at `/admin/extraction-eval` reporting cell-level accuracy, diff logs, and human correction metrics from `ExtractionLog`.

---

## 🔒 Privacy, DPDP (2023) Alignment & Accountability

- **Privacy Policy (`/privacy`)**: Discloses data minimization rules (zero Aadhaar, grades, or personal phone numbers collected).
- **Right to Erasure**: Interactive 1-click *"Delete My Data"* button allowing students to purge all their timetable entries, meeting requests, and logs.
- **Rate Limit**: Maximum 3 pending consultation requests per student at any time.
- **No-Show Accountability**: 2 recorded no-shows automatically locks student booking. Unlocking is reserved for academic administration via `/admin`.
- **Immutable Audit Trail**: Key actions (auto-confirmations, status transitions, cancellations, holiday shifts) are permanently recorded in `AuditLog`.

---

## 📈 Operational Telemetry & Analytics (`/admin/metrics`)

Real-time administrative metrics dashboard measuring:
- Median time from request to confirmation (sub-minute for auto-confirmed slots).
- Consultation Acceptance Rate, Completion Rate, and No-Show Rate.
- Faculty workload distribution table (inquiries received, confirmed, and completed).
- Cohort Group Session seat fill rate percentage.
- Extraction review screen averages (cells extracted, human edits, verification speed).

---

## ⚡ Interactive Demo Center (`/demo`)

The prototype includes 6 pre-scripted presentation scenarios executing real database logic:
1. **Meera's Thursday Doubt Hour**: Cross-references Meera's class schedule with Dr. Raman's office hour, filters lab conflicts, and transparently scores the top slot.
2. **Office-Hour Capacity & Auto-Confirm**: First booking auto-confirms instantly; subsequent booking exceeding capacity transitions into pending queue.
3. **Cohort Group Session & Clash Filtering**: 4 students have doubts on "Pointers in C". 3 free students are grouped; 1 student busy in class is strictly excluded.
4. **Layer 1 On-Demand Gap Booking**: Student requests time in faculty's predicted free period. System queues it as pending with visible "not a commitment" label.
5. **Verified vs Unverified Schedule Changes**: Faculty swap takes immediate algorithmic effect; student crowdsourced report flags an unverified warning on slot cards.
6. **Academic Calendar Holiday Cascade**: Declaring a Wednesday storm holiday pauses the day order and cascades subsequent working days forward.
- **1-Click Database Reset**: Restores all synthetic seed data in 1 click.

---

## 🚀 Getting Started

### 1. Requirements
- Python 3.11+ (tested on Python 3.11 and 3.14)
- SQLite3 (included with Python)

### 2. Installation
```powershell
# From the project root directory:
cd C:\Users\admin\.gemini\antigravity\scratch\campus_connect

# Install lightweight dependencies:
pip install -r requirements.txt
```

### 3. Initialize & Seed Database
```powershell
python seed_data.py
```
*Populates 3 departments (CSE, ECE, MECH), 12 faculty, 8 students, 21 courses with topic tags, realistic timetables with labs, 6 faculty office hours, and 4 ground-truth fixture sets.*

### 4. Run the Full Test Suite
```powershell
# Run the demo file extraction test suite (PNG & XLSX end-to-end + pixel/cell modification):
python -m pytest tests/test_demo_file_extraction.py -v

# Run the complete test suite (68 tests):
python -m pytest tests -v
```
*Runs all 68 automated unit and integration tests across calendar rotation, slot matching, mock login, demo file extraction, extraction review, schedule changes, privacy erasure, and demo scenarios.*

### 5. Launch the Web Application
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open your browser and navigate to:  
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

*(Note: If port 8000 is occupied by another application, launch with `--port 8080` instead).*

---

## 🎭 Simulated Authentication & Role Personas

CampusConnect uses simulated authentication (no real passwords):
- **Landing Page (`/` or `/login`)**: Dedicated portal cards for **Student Login** and **Faculty Login**.
- **Role Selection Pages (`/login/student` and `/login/faculty`)**: 1-click login listing seeded demo personas.
- **Persistent Navbar Switcher**: Instantly switch personas from any page via the top-right dropdown.
- **Exit Link**: Click `Exit` in the navbar to clear the session cookie and return to the landing page.

### Key Seeded Personas:
- **Meera Nair (Student)**: CSE 3rd sem. Timetable loaded. Has doubts on *Binary Trees*. Tests Smart Matching and auto-confirmations.
- **Divya Patel (Student)**: CSE 3rd sem. Timetable intentionally absent. Tests **Manual Mode** with published office hours.
- **Dr. Rajesh Raman (Faculty)**: CSE Professor. Office hour on **Day 3 (14:00 - 15:00)** with capacity 3 and auto-confirm enabled.
- **Prof. Vikram Iyer (Faculty)**: CSE Professor. Office hour on **Day 4** with `auto_confirm=False` (manual review required).
- **Dr. K. Meenakshi (Faculty)**: On-demand only (Layer 1). Demonstrates timetable prediction matching without commitments.
- **Academic Administration (Dean's Office)**: Access to `/admin`, calendar mode toggle, holiday declarations, student unblocking, OCR evaluation, and metrics.

---

## 📂 Project Architecture

```
campus_connect/
├── app/
│   ├── main.py                 # FastAPI application, lifespan, template mounting & router registration
│   ├── auth_mock.py            # Simulated authentication & cookie role switcher
│   ├── database.py             # SQLite connection, init_db, audit & notification helpers
│   ├── models.py               # SQLModel entities (User, Course, TimetableEntry, OfficeHour, MeetingRequest, etc.)
│   ├── calendar_engine.py      # Configurable Day-Order / Weekday calendar logic & holiday cascades
│   ├── matching.py             # Deterministic slot matching, conflict filtering & scoring pipeline
│   ├── group_sessions.py       # Group session detection per-slot availability
│   ├── extractor/
│   │   ├── base.py             # Abstract base extractor & Pydantic schemas
│   │   ├── mock_extractor.py   # Offline fixture-backed deterministic extractor
│   │   └── evaluator.py        # Automated benchmark accuracy evaluator
│   ├── routers/
│   │   ├── common.py           # Landing page, mock login selector, directory, notifications, /privacy
│   │   ├── student.py          # Dashboard, booking form, HTMX slots partial, meetings, timetable OCR review, schedule change
│   │   ├── faculty.py          # Inbox, accept/counter/reject, group session host/decline, office hours, schedule change
│   │   ├── admin.py            # Calendar mode switch, holiday declaration, student unlock, /extraction-eval, /metrics, /schedule-changes
│   │   └── demo.py             # 6 guided demo presentation scenarios and database reset
│   └── templates/
│       ├── base.html           # Main layout with Tailwind CDN, HTMX CDN, navbar, footer, demo links
│       ├── directory.html      # Searchable faculty directory
│       ├── notifications.html  # User notification center
│       ├── privacy.html        # Privacy policy & 1-click self-service data erasure
│       ├── auth/               # Landing page & simulated role login selectors
│       ├── student/            # Dashboard, booking form, slots partial, meetings, timetable upload & review
│       ├── faculty/            # Inbox with group session banner & office-hour editor
│       ├── admin/              # Calendar control, OCR eval benchmark, metrics telemetry, schedule changes
│       ├── demo/               # Guided demo scenario runner
│       └── schedule_change/    # Faculty verified & student unverified reporting forms
├── data/
│   └── fixtures/               # Hand-written ground-truth JSON fixtures (CSE, ECE, MECH)
├── tests/
│   ├── conftest.py             # In-memory SQLite fixtures & TestClient configuration
│   ├── test_calendar.py        # Calendar rotation, Sunday skips, holiday shifts
│   ├── test_extractor_mock.py  # Mock extractor fixture validation
│   ├── test_group_sessions.py  # Per-slot group session overlap tests
│   ├── test_matching.py        # 12 scheduling conflict & capacity tests
│   ├── test_mock_login.py      # 6 simulated authentication & landing page tests
│   ├── test_extraction_eval.py # Extraction benchmark, review confirmation, and immediate image deletion tests
│   ├── test_schedule_changes_flow.py # Faculty verified vs student unverified reporting and admin verification tests
│   ├── test_demo_and_privacy.py # Privacy erasure, metrics calculation, and 6 demo scenarios tests
│   └── test_web_routes.py      # 14 FastAPI integration & UI workflow tests
├── evaluate_extraction.py      # Standalone CLI evaluation script
├── seed_data.py                # Synthetic dataset populator
├── requirements.txt            # Lightweight dependency specifications
└── README.md                   # System documentation & presentation guide
```
