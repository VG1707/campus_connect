"""
Seed Data Script for CampusConnect Prototype.
SYNTHETIC SAMPLE DATA FOR DEMONSTRATION PURPOSES ONLY.
Does not scrape or access any real college portal or student database.
Departments: 3 (CSE, ECE, MECH)
Faculty: 12
Students: 8
Courses: ~20
Office Hours: 6 faculty (half), remainder on-demand only.
Calendar: 14-day sample with Day Orders (Day 1 - Day 6), holiday shift, and exam day.
"""

from datetime import date, datetime, timedelta
import json
from sqlmodel import Session, select

from app.database import engine, init_db, record_audit
from app.models import (
    User, UserRole, Course, FacultyCourse, AcademicCalendar,
    TimetableEntry, OfficeHour, MeetingRequest, ScheduleChange,
    RequestState, RequestSource, VerificationStatus, ScheduleChangeKind
)
from app.calendar_engine import set_calendar_mode, MODE_DAY_ORDER


def seed_all_data(session: Session, reset_existing: bool = True) -> dict:
    """Populate database with complete synthetic college environment."""
    if reset_existing:
        # Clear tables cleanly in reverse dependency order
        for model in [
            MeetingRequest, TimetableEntry, OfficeHour, FacultyCourse,
            ScheduleChange, AcademicCalendar, Course, User
        ]:
            session.exec(select(model)).all()
            for obj in session.exec(select(model)).all():
                session.delete(obj)
        session.commit()

    # 1. System Config
    set_calendar_mode(session, MODE_DAY_ORDER)

    # 2. Users: Admin, Faculty, Students
    admin = User(
        name="Dean Academic Office (Admin)",
        role=UserRole.ADMIN.value,
        department="Academic Administration",
        email="admin@campusconnect.edu"
    )
    session.add(admin)

    # 12 Faculty (4 CSE, 4 ECE, 4 MECH)
    faculty_members = [
        # CSE
        User(name="Dr. Rajesh Raman", role=UserRole.FACULTY.value, department="Computer Science & Engineering", email="rajesh.raman@campusconnect.edu"),
        User(name="Dr. Anita Sharma", role=UserRole.FACULTY.value, department="Computer Science & Engineering", email="anita.sharma@campusconnect.edu"),
        User(name="Prof. Vikram Iyer", role=UserRole.FACULTY.value, department="Computer Science & Engineering", email="vikram.iyer@campusconnect.edu"),
        User(name="Dr. K. Meenakshi", role=UserRole.FACULTY.value, department="Computer Science & Engineering", email="meenakshi.k@campusconnect.edu"),
        # ECE
        User(name="Dr. Priya Sundaram", role=UserRole.FACULTY.value, department="Electronics & Communication Engineering", email="priya.sundaram@campusconnect.edu"),
        User(name="Dr. K. Venkatesh", role=UserRole.FACULTY.value, department="Electronics & Communication Engineering", email="venkatesh.k@campusconnect.edu"),
        User(name="Prof. Suresh Natarajan", role=UserRole.FACULTY.value, department="Electronics & Communication Engineering", email="suresh.n@campusconnect.edu"),
        User(name="Dr. Deepa Thomas", role=UserRole.FACULTY.value, department="Electronics & Communication Engineering", email="deepa.thomas@campusconnect.edu"),
        # MECH
        User(name="Dr. R. Balaji", role=UserRole.FACULTY.value, department="Mechanical Engineering", email="balaji.r@campusconnect.edu"),
        User(name="Dr. S. Mukherjee", role=UserRole.FACULTY.value, department="Mechanical Engineering", email="mukherjee.s@campusconnect.edu"),
        User(name="Prof. Arvind Kumar", role=UserRole.FACULTY.value, department="Mechanical Engineering", email="arvind.k@campusconnect.edu"),
        User(name="Dr. Neha Joshi", role=UserRole.FACULTY.value, department="Mechanical Engineering", email="neha.joshi@campusconnect.edu"),
    ]
    for f in faculty_members:
        session.add(f)

    # 8 Students
    students = [
        User(name="Meera Nair (Student A)", role=UserRole.STUDENT.value, department="Computer Science & Engineering", email="meera.nair@campusconnect.edu", section="CSE-A", semester=3),
        User(name="Rohan Verma (Student B)", role=UserRole.STUDENT.value, department="Computer Science & Engineering", email="rohan.verma@campusconnect.edu", section="CSE-A", semester=3),
        User(name="Karthik S. (Student C)", role=UserRole.STUDENT.value, department="Computer Science & Engineering", email="karthik.s@campusconnect.edu", section="CSE-A", semester=3),
        User(name="Divya Patel (Student D)", role=UserRole.STUDENT.value, department="Computer Science & Engineering", email="divya.patel@campusconnect.edu", section="CSE-A", semester=3),
        User(name="Arjun Rao (Student E)", role=UserRole.STUDENT.value, department="Electronics & Communication Engineering", email="arjun.rao@campusconnect.edu", section="ECE-A", semester=3),
        User(name="Sneha Reddy (Student F)", role=UserRole.STUDENT.value, department="Electronics & Communication Engineering", email="sneha.reddy@campusconnect.edu", section="ECE-A", semester=3),
        User(name="Aditya Joshi (Student G)", role=UserRole.STUDENT.value, department="Mechanical Engineering", email="aditya.joshi@campusconnect.edu", section="ME-A", semester=5),
        User(name="Ananya Sen (Student H)", role=UserRole.STUDENT.value, department="Mechanical Engineering", email="ananya.sen@campusconnect.edu", section="ME-A", semester=5),
    ]
    for s in students:
        session.add(s)

    session.commit()

    # Refresh instances to populate IDs
    f_raman = faculty_members[0]
    f_sharma = faculty_members[1]
    f_iyer = faculty_members[2]
    f_meenakshi = faculty_members[3]
    f_priya = faculty_members[4]
    f_venkatesh = faculty_members[5]
    f_balaji = faculty_members[8]

    s_meera = students[0]
    s_rohan = students[1]
    s_karthik = students[2]
    s_divya = students[3]
    s_arjun = students[4]

    # 3. Courses with Topic Tags
    courses_data = [
        # CSE
        ("CS201", "Data Structures and Algorithms", "Computer Science & Engineering", ["Binary Trees", "Graphs", "Sorting", "Hashing", "Dynamic Programming"]),
        ("CS202", "Data Structures Lab", "Computer Science & Engineering", ["Tree Traversals", "Graph Implementations", "Pointer Debugging"]),
        ("CS203", "Digital Logic Design", "Computer Science & Engineering", ["K-Maps", "Multiplexers", "Flip-Flops", "Counters"]),
        ("CS204", "Digital Systems Lab", "Computer Science & Engineering", ["Breadboard Wiring", "Logic Gates", "Verilog Simulation"]),
        ("CS205", "Computer Organization & Architecture", "Computer Science & Engineering", ["Pipeline Hazards", "Cache Memory", "Instruction Sets"]),
        ("CS301", "Operating Systems", "Computer Science & Engineering", ["Process Synchronization", "Deadlocks", "Virtual Memory", "Semaphores"]),
        ("CS302", "OS & Networks Lab", "Computer Science & Engineering", ["Fork & Exec", "Socket Programming", "IPC"]),
        ("CS303", "Database Management Systems", "Computer Science & Engineering", ["SQL Queries", "B+ Trees", "Normalization", "Transactions"]),
        ("CS307", "Artificial Intelligence", "Computer Science & Engineering", ["A* Search", "Minimax", "Constraint Satisfaction"]),
        ("MA201", "Discrete Mathematics", "Science & Humanities", ["Graph Theory", "Recurrence Relations", "Combinatorics"]),
        ("GE201", "Professional Ethics", "Science & Humanities", ["Engineering Ethics", "Intellectual Property"]),
        # ECE (SRMIST Kattankulathur Curriculum)
        ("EC201", "Electron Devices and Circuits (18ECC201J)", "Electronics & Communication Engineering", ["BJT DC Biasing & Q-Point", "MOSFET Small-Signal Model", "CE/CS Amplifier Frequency Response", "Negative Feedback Topologies", "Push-Pull Power Output Stages"]),
        ("EC202", "Electron Devices & Circuits Lab (18ECC201J)", "Electronics & Communication Engineering", ["CRO & Function Generator", "BJT CE Output Characteristics", "MOSFET Transfer Curve", "RC Phase Shift Oscillator"]),
        ("EC203", "Signals and Systems (18ECC202J)", "Electronics & Communication Engineering", ["LTI System Properties", "Convolution Integral & Sum", "Continuous-Time Fourier Transform", "Z-Transform ROC Analysis", "Laplace Transform in Circuit Analysis"]),
        ("EC204", "Signals Simulation Lab (18ECC202J)", "Electronics & Communication Engineering", ["MATLAB Signal Generation", "FFT Spectrum Analysis", "Pole-Zero Cancellation", "Digital Filter Realization"]),
        ("EC205", "Electromagnetic Fields & Waves (18ECC204J)", "Electronics & Communication Engineering", ["Maxwell Equations in Phasor Form", "Poynting Vector & Power Flow", "Smith Chart Impedance Matching", "Transmission Line Reflections", "Rectangular Waveguide Modes"]),
        ("18ECC203J", "Digital Signal Processing", "Electronics & Communication Engineering", ["Radix-2 FFT Butterflies", "FIR Filter Design via Windows", "IIR Butterworth & Chebyshev Filters", "Bilinear Transformation", "Finite Word Length Effects"]),
        ("18ECC302J", "VLSI Design", "Electronics & Communication Engineering", ["CMOS Inverter Sizing & DC VTC", "Logical Effort & Delay Estimation", "Euler Path Stick Diagrams", "Static & Dynamic CMOS Logic", "Cadence Virtuoso Layout Rules"]),
        ("18ECC303J", "Microprocessors and Microcontrollers", "Electronics & Communication Engineering", ["8086 Architecture & Registers", "8051 Timer/Counter Interrupts", "8255 PPI Interfacing", "Assembly Language Subroutines", "ADC0808 Interfacing"]),
        # MECH
        ("ME301", "Thermodynamics II", "Mechanical Engineering", ["Rankine Cycle", "Refrigeration", "Entropy Generation"]),
        ("ME302", "Thermal Engineering Lab", "Mechanical Engineering", ["Boiler Trials", "IC Engine Performance"]),
        ("ME303", "Fluid Mechanics", "Mechanical Engineering", ["Bernoulli Equation", "Boundary Layer", "Pipe Friction"]),
        ("ME304", "Fluid Dynamics Lab", "Mechanical Engineering", ["Venturimeter Test", "Centrifugal Pump Test"]),
        ("ME305", "Kinematics of Machinery", "Mechanical Engineering", ["Gear Trains", "Cam Profiles", "Four-Bar Linkage"]),
    ]

    course_objs = {}
    for code, name, dept, tags in courses_data:
        c = Course(code=code, name=name, department=dept, topic_tags_json=json.dumps(tags))
        session.add(c)
        course_objs[code] = c

    session.commit()

    # 4. FacultyCourse links
    faculty_courses = [
        (f_raman.id, course_objs["CS201"].id, "CSE-A"),
        (f_raman.id, course_objs["CS202"].id, "CSE-A"),
        (f_sharma.id, course_objs["CS301"].id, "CSE-B"),
        (f_sharma.id, course_objs["CS302"].id, "CSE-B"),
        (f_iyer.id, course_objs["CS307"].id, "CSE-B"),
        (f_meenakshi.id, course_objs["CS303"].id, "CSE-A"),
        (f_priya.id, course_objs["EC201"].id, "ECE-A"),
        (f_priya.id, course_objs["EC202"].id, "ECE-A"),
        (f_priya.id, course_objs["18ECC302J"].id, "ECE-A"),
        (f_venkatesh.id, course_objs["EC203"].id, "ECE-A"),
        (f_venkatesh.id, course_objs["EC204"].id, "ECE-A"),
        (f_venkatesh.id, course_objs["18ECC203J"].id, "ECE-A"),
        (faculty_members[6].id, course_objs["EC205"].id, "ECE-A"),
        (faculty_members[7].id, course_objs["18ECC303J"].id, "ECE-A"),
        (f_balaji.id, course_objs["ME301"].id, "ME-A"),
    ]
    for fid, cid, sec in faculty_courses:
        session.add(FacultyCourse(faculty_id=fid, course_id=cid, section=sec))

    # 5. Academic Calendar (14 days starting from standard reference date)
    # Reference anchor: 2026-10-19 (Monday)
    calendar_schedule = [
        ("2026-10-19", 1, False, False, None),
        ("2026-10-20", 2, False, False, None),
        ("2026-10-21", 3, False, False, None),
        ("2026-10-22", None, True, False, "College Cultural Fest (Holiday)"),
        ("2026-10-23", 4, False, False, None),
        ("2026-10-24", 5, False, False, None),
        ("2026-10-25", None, True, False, "Sunday"),
        ("2026-10-26", 6, False, False, None),
        ("2026-10-27", 1, False, False, None),
        ("2026-10-28", None, False, True, "Mid-term Assessment 1 (Exam Day)"),
        ("2026-10-29", 2, False, False, None),
        ("2026-10-30", 3, False, False, None),
        ("2026-10-31", 4, False, False, None),
        ("2026-11-01", None, True, False, "Sunday"),
    ]
    for dt_str, do, is_h, is_e, note in calendar_schedule:
        session.add(AcademicCalendar(
            date=dt_str, day_order=do, is_holiday=is_h, is_exam=is_e, note=note
        ))

    # 6. Faculty Office Hours (Layer 2 Commitments for faculty)
    office_hours_data = [
        # Dr. Rajesh Raman: Day 3 consultation hour (14:00 - 15:00), capacity 3, auto_confirm=True
        OfficeHour(
            faculty_id=f_raman.id,
            recurrence_pattern="day_order:Day 3",
            start_time="14:00",
            end_time="15:00",
            location="Cabin 304, Academic Block A",
            capacity=3,
            allowed_durations_json=json.dumps([5, 10, 15, 30]),
            auto_confirm=True,
            paused_dates_json=json.dumps([])
        ),
        # Dr. Anita Sharma: Day 2 consultation hour (11:00 - 12:00), capacity 2, auto_confirm=True
        OfficeHour(
            faculty_id=f_sharma.id,
            recurrence_pattern="day_order:Day 2",
            start_time="11:00",
            end_time="12:00",
            location="Cabin 312, Academic Block A",
            capacity=2,
            allowed_durations_json=json.dumps([5, 10, 15, 30]),
            auto_confirm=True,
            paused_dates_json=json.dumps([])
        ),
        # Prof. Vikram Iyer: Day 4 consultation hour (15:00 - 16:00), capacity 1, auto_confirm=False (requires approval)
        OfficeHour(
            faculty_id=f_iyer.id,
            recurrence_pattern="day_order:Day 4",
            start_time="15:00",
            end_time="16:00",
            location="Cabin 308, Academic Block A",
            capacity=1,
            allowed_durations_json=json.dumps([5, 10, 20, 40]),
            auto_confirm=False,
            paused_dates_json=json.dumps([])
        ),
        # Dr. Priya Sundaram: Day 1 consultation hour (14:00 - 15:00), capacity 2, auto_confirm=True
        OfficeHour(
            faculty_id=f_priya.id,
            recurrence_pattern="day_order:Day 1",
            start_time="14:00",
            end_time="15:00",
            location="Cabin TP-704, Tech Park 7th Floor (SRM)",
            capacity=2,
            allowed_durations_json=json.dumps([5, 10, 15, 30]),
            auto_confirm=True,
            paused_dates_json=json.dumps([])
        ),
        # Dr. K. Venkatesh: Day 5 consultation hour (10:30 - 11:30), capacity 3, auto_confirm=True
        OfficeHour(
            faculty_id=f_venkatesh.id,
            recurrence_pattern="day_order:Day 5",
            start_time="10:30",
            end_time="11:30",
            location="Cabin TP-802, Tech Park 8th Floor (SRM)",
            capacity=3,
            allowed_durations_json=json.dumps([5, 10, 15, 30, 45]),
            auto_confirm=True,
            paused_dates_json=json.dumps([])
        ),
        # Prof. Suresh Natarajan: Day 2 consultation hour (15:00 - 16:00), capacity 2, auto_confirm=True
        OfficeHour(
            faculty_id=faculty_members[6].id,
            recurrence_pattern="day_order:Day 2",
            start_time="15:00",
            end_time="16:00",
            location="Cabin TP-712, Tech Park 7th Floor (SRM)",
            capacity=2,
            allowed_durations_json=json.dumps([5, 10, 15, 30]),
            auto_confirm=True,
            paused_dates_json=json.dumps([])
        ),
        # Dr. Deepa Thomas: Day 4 consultation hour (11:00 - 12:00), capacity 2, auto_confirm=False
        OfficeHour(
            faculty_id=faculty_members[7].id,
            recurrence_pattern="day_order:Day 4",
            start_time="11:00",
            end_time="12:00",
            location="Cabin TP-810, Tech Park 8th Floor (SRM)",
            capacity=2,
            allowed_durations_json=json.dumps([5, 10, 15, 30]),
            auto_confirm=False,
            paused_dates_json=json.dumps([])
        ),
        # Dr. R. Balaji: Day 2 consultation hour (14:00 - 15:00), capacity 2, auto_confirm=False
        OfficeHour(
            faculty_id=f_balaji.id,
            recurrence_pattern="day_order:Day 2",
            start_time="14:00",
            end_time="15:00",
            location="ME Cabin 104, Workshop Block",
            capacity=2,
            allowed_durations_json=json.dumps([5, 10, 15, 30]),
            auto_confirm=False,
            paused_dates_json=json.dumps([])
        ),
    ]
    for oh in office_hours_data:
        session.add(oh)

    # 7. Student Timetables: Seed Meera (Student A), Rohan (Student B), Karthik (Student C)
    # Divya (Student D) is intentionally kept timetable-free to demonstrate "Manual Mode (Timetable Inactive)".
    student_timetable_entries = [
        # Meera (Student A) - CSE 3rd Sem
        # Day 1: P1, P2, P3, P5
        (s_meera.id, "Day 1", 1, course_objs["CS201"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 1", 2, course_objs["MA201"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 1", 3, course_objs["CS203"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 1", 5, course_objs["CS205"].id, "LH-201", "CSE-A", False),
        # Day 2: P2, P3, P5-P6 (Lab)
        (s_meera.id, "Day 2", 2, course_objs["CS201"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 2", 3, course_objs["MA201"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 2", 5, course_objs["CS202"].id, "CS-Lab-2", "CSE-A", True),
        (s_meera.id, "Day 2", 6, course_objs["CS202"].id, "CS-Lab-2", "CSE-A", True),
        # Day 3: P1, P2, P4 (ends at 12:15, student free at 14:00!)
        (s_meera.id, "Day 3", 1, course_objs["CS203"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 3", 2, course_objs["CS205"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 3", 4, course_objs["CS201"].id, "LH-201", "CSE-A", False),
        # Day 4: P1, P3, P6
        (s_meera.id, "Day 4", 1, course_objs["MA201"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 4", 3, course_objs["CS205"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 4", 6, course_objs["CS201"].id, "LH-201", "CSE-A", False),
        # Day 5: P2, P4, P7
        (s_meera.id, "Day 5", 2, course_objs["CS203"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 5", 4, course_objs["CS205"].id, "LH-201", "CSE-A", False),
        (s_meera.id, "Day 5", 7, course_objs["GE201"].id, "LH-201", "CSE-A", False),

        # Rohan (Student B) - same section CSE-A (Free Day 3 at 14:00)
        (s_rohan.id, "Day 3", 1, course_objs["CS203"].id, "LH-201", "CSE-A", False),
        (s_rohan.id, "Day 3", 2, course_objs["CS205"].id, "LH-201", "CSE-A", False),
        (s_rohan.id, "Day 3", 4, course_objs["CS201"].id, "LH-201", "CSE-A", False),

        # Karthik (Student C) - same section CSE-A (Free Day 3 at 14:00)
        (s_karthik.id, "Day 3", 1, course_objs["CS203"].id, "LH-201", "CSE-A", False),
        (s_karthik.id, "Day 3", 2, course_objs["CS205"].id, "LH-201", "CSE-A", False),
        (s_karthik.id, "Day 3", 4, course_objs["CS201"].id, "LH-201", "CSE-A", False),

        # Arjun (Student E) - ECE-A (SRM Tech Park Campus)
        # Day 1: P1 (EC201), P3 (EC203), P5 (MA201)
        (s_arjun.id, "Day 1", 1, course_objs["EC201"].id, "TP-701", "ECE-A", False),
        (s_arjun.id, "Day 1", 3, course_objs["EC203"].id, "TP-701", "ECE-A", False),
        (s_arjun.id, "Day 1", 5, course_objs["MA201"].id, "TP-701", "ECE-A", False),
        # Day 2: P2 (EC205), P4 (EC201), P6-P7 (EC202 Lab in Basic Electronics Lab)
        (s_arjun.id, "Day 2", 2, course_objs["EC205"].id, "TP-701", "ECE-A", False),
        (s_arjun.id, "Day 2", 4, course_objs["EC201"].id, "TP-701", "ECE-A", False),
        (s_arjun.id, "Day 2", 6, course_objs["EC202"].id, "TP-204 Lab", "ECE-A", True),
        (s_arjun.id, "Day 2", 7, course_objs["EC202"].id, "TP-204 Lab", "ECE-A", True),
        # Day 3: P2 (EC203), P5-P6 (EC204 Signals Lab: 13:00 - 14:45! Preserves Lab Conflict)
        (s_arjun.id, "Day 3", 2, course_objs["EC203"].id, "TP-701", "ECE-A", False),
        (s_arjun.id, "Day 3", 5, course_objs["EC204"].id, "TP-204 Lab", "ECE-A", True),
        (s_arjun.id, "Day 3", 6, course_objs["EC204"].id, "TP-204 Lab", "ECE-A", True),
        # Day 4: P1 (EC201), P3 (EC205), P5 (DSP)
        (s_arjun.id, "Day 4", 1, course_objs["EC201"].id, "TP-701", "ECE-A", False),
        (s_arjun.id, "Day 4", 3, course_objs["EC205"].id, "TP-701", "ECE-A", False),
        (s_arjun.id, "Day 4", 5, course_objs["18ECC203J"].id, "TP-701", "ECE-A", False),
        # Day 5: P2 (EC203), P4 (EC205)
        (s_arjun.id, "Day 5", 2, course_objs["EC203"].id, "TP-701", "ECE-A", False),
        (s_arjun.id, "Day 5", 4, course_objs["EC205"].id, "TP-701", "ECE-A", False),
    ]

    for owner_id, day, period, cid, rm, sec, is_lab in student_timetable_entries:
        session.add(TimetableEntry(
            owner_id=owner_id,
            day_order_or_weekday=day,
            period=period,
            course_id=cid,
            room=rm,
            section=sec,
            is_lab_block=is_lab
        ))

    # 8. Faculty Teaching Timetables: Dr. Rajesh Raman, Dr. Priya Sundaram, Dr. K. Venkatesh
    faculty_teaching_entries = [
        # Dr. Raman (CSE)
        (f_raman.id, "Day 1", 1, course_objs["CS201"].id, "LH-201", "CSE-A"),
        (f_raman.id, "Day 2", 2, course_objs["CS201"].id, "LH-201", "CSE-A"),
        (f_raman.id, "Day 3", 4, course_objs["CS201"].id, "LH-201", "CSE-A"),
        (f_raman.id, "Day 4", 6, course_objs["CS201"].id, "LH-201", "CSE-A"),
        # Dr. Priya Sundaram (ECE)
        (f_priya.id, "Day 1", 1, course_objs["EC201"].id, "TP-701", "ECE-A"),
        (f_priya.id, "Day 2", 4, course_objs["EC201"].id, "TP-701", "ECE-A"),
        (f_priya.id, "Day 4", 1, course_objs["EC201"].id, "TP-701", "ECE-A"),
        # Dr. K. Venkatesh (ECE)
        (f_venkatesh.id, "Day 1", 3, course_objs["EC203"].id, "TP-701", "ECE-A"),
        (f_venkatesh.id, "Day 3", 2, course_objs["EC203"].id, "TP-701", "ECE-A"),
        (f_venkatesh.id, "Day 5", 2, course_objs["EC203"].id, "TP-701", "ECE-A"),
    ]
    for fid, day, period, cid, rm, sec in faculty_teaching_entries:
        session.add(TimetableEntry(
            owner_id=fid,
            day_order_or_weekday=day,
            period=period,
            course_id=cid,
            room=rm,
            section=sec,
            is_lab_block=False
        ))

    # 9. Seed meeting requests to support Scenario 3 (Group Session detection)
    # Target date: 2026-10-21 (Day 3). Office hour: 14:00 - 15:00. Topic: "Binary Trees"
    # Meera, Rohan, Karthik have open doubt requests on "Binary Trees" with Dr. Raman.
    # Arjun also has a doubt on "Binary Trees", but he has EC204 Lab at 13:00-14:45 (conflicts with 14:00-14:30)!
    req_time = "2026-10-21T14:00:00"
    end_time = "2026-10-21T14:30:00"
    group_test_requests = [
        MeetingRequest(
            student_id=s_meera.id,
            faculty_id=f_raman.id,
            course_id=course_objs["CS201"].id,
            topic_tag="Binary Trees",
            type="doubt",
            description="Need help understanding AVL tree rotations",
            preferred_window_start="2026-10-21T14:00:00",
            preferred_window_end="2026-10-21T15:00:00",
            slot_start=req_time,
            slot_end=end_time,
            duration=30,
            state=RequestState.PENDING.value,
            source=RequestSource.OFFICE_HOUR.value,
            location="Cabin 304, Academic Block A",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        ),
        MeetingRequest(
            student_id=s_rohan.id,
            faculty_id=f_raman.id,
            course_id=course_objs["CS201"].id,
            topic_tag="Binary Trees",
            type="doubt",
            description="Clarification on tree height balance factor",
            preferred_window_start="2026-10-21T14:00:00",
            preferred_window_end="2026-10-21T15:00:00",
            slot_start=req_time,
            slot_end=end_time,
            duration=30,
            state=RequestState.PENDING.value,
            source=RequestSource.OFFICE_HOUR.value,
            location="Cabin 304, Academic Block A",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        ),
        MeetingRequest(
            student_id=s_karthik.id,
            faculty_id=f_raman.id,
            course_id=course_objs["CS201"].id,
            topic_tag="Binary Trees",
            type="doubt",
            description="Doubt in in-order successor calculation",
            preferred_window_start="2026-10-21T14:00:00",
            preferred_window_end="2026-10-21T15:00:00",
            slot_start=req_time,
            slot_end=end_time,
            duration=30,
            state=RequestState.PENDING.value,
            source=RequestSource.OFFICE_HOUR.value,
            location="Cabin 304, Academic Block A",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        ),
        # 4th student (Arjun) who cannot attend due to Lab conflict
        MeetingRequest(
            student_id=s_arjun.id,
            faculty_id=f_raman.id,
            course_id=course_objs["CS201"].id,
            topic_tag="Binary Trees",
            type="doubt",
            description="Need help with BST deletion logic",
            preferred_window_start="2026-10-21T14:00:00",
            preferred_window_end="2026-10-21T15:00:00",
            slot_start=req_time,
            slot_end=end_time,
            duration=30,
            state=RequestState.PENDING.value,
            source=RequestSource.OFFICE_HOUR.value,
            location="Cabin 304, Academic Block A",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        ),
    ]
    for r in group_test_requests:
        session.add(r)

    session.commit()

    record_audit(
        session=session,
        action="DATABASE_SEEDED",
        entity_type="System",
        details="Seeded 3 departments, 12 faculty, 8 students, 21 courses, 14-day calendar."
    )

    return {
        "status": "success",
        "faculty_count": len(faculty_members),
        "student_count": len(students),
        "courses_count": len(courses_data),
        "office_hours_count": len(office_hours_data),
    }


if __name__ == "__main__":
    init_db()
    with Session(engine) as s:
        res = seed_all_data(s, reset_existing=True)
        print("Database seeded successfully:", res)
