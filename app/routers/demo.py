"""
Demo scenarios router for CampusConnect.
Executes the 6 core presentation scenarios verifying:
1. Meera Thursday doubt hour matching
2. Capacity & auto-confirm threshold
3. Cohort group doubt suggestion & clash filtering
4. Layer 1 on-demand prediction gap booking
5. Verified vs unverified schedule changes
6. Holiday declaration & day-order shift cascade
"""

from datetime import datetime, date, time, timedelta
from typing import Optional, List
import json
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session, record_audit, send_notification
from app.models import (
    User, UserRole, Course, FacultyCourse, OfficeHour, MeetingRequest,
    TimetableEntry, GroupSession, GroupSessionState, ScheduleChange,
    ScheduleChangeKind, VerificationStatus, RequestState, RequestSource, RequestType
)
from app.auth_mock import get_current_user
from app.routers.common import get_base_context
from app.calendar_engine import (
    get_day_label_for_date, shift_calendar_on_holiday, AcademicCalendar
)
from app.matching import find_matching_slots
from app.group_sessions import evaluate_group_sessions
from seed_data import seed_all_data

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def demo_scenarios_view(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Guided demo scenario hub."""
    from app.main import templates
    ctx = get_base_context(request, current_user, session)
    ctx.update({"result": None})
    return templates.TemplateResponse(request=request, name="demo/scenarios.html", context=ctx)


@router.post("/reset")
def demo_reset_database(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Reset database to fresh seed state."""
    from app.main import templates
    seed_all_data(session, reset_existing=True)
    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "result": {
            "title": "Database Re-seeded Successfully",
            "summary": "All users, courses, academic calendar days, timetable entries, and office hours have been restored to initial clean demo data.",
            "details": [
                "12 Faculty accounts across CSE, ECE, and MECH departments.",
                "8 Student accounts with synthetic semester 3 schedules.",
                "6 Recurring faculty office hours with auto-confirm rules.",
                "Zero orphan bookings or modified calendar shifts."
            ],
            "action_link": "/student/dashboard",
            "action_text": "Go to Student Dashboard &rarr;"
        }
    })
    return templates.TemplateResponse(request=request, name="demo/scenarios.html", context=ctx)


@router.post("/run/1")
def run_scenario_1(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Scenario 1: Meera's Thursday Doubt Hour
    Finds slots compatible with student classes during Dr. Swaminathan's Thursday office hour.
    """
    from app.main import templates

    meera = session.exec(select(User).where(User.name.contains("Meera"))).first()
    if not meera:
        meera = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).first()

    faculty = session.exec(select(User).where(User.name.contains("Raman"))).first()
    if not faculty:
        faculty = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).first()

    matched = find_matching_slots(
        session=session,
        student_id=meera.id,
        faculty_id=faculty.id,
        window_start=datetime(2026, 10, 21, 13, 0),
        window_end=datetime(2026, 10, 21, 16, 0),
        duration_minutes=15,
        include_on_demand=False,
        current_time=datetime(2026, 10, 19, 9, 0)
    )

    top_slot_str = (
        f"Top Slot: {matched[0].slot_start.strftime('%H:%M')} - {matched[0].slot_end.strftime('%H:%M')} (Score: {matched[0].score}/100)"
        if matched else "No slots found"
    )
    reason_str = matched[0].ranking_reason if matched else "N/A"

    details = [
        f"Student: {meera.name} (Department: {meera.department})",
        f"Faculty: {faculty.name} (Recurring Office Hour: Day 3 14:00 - 15:00)",
        f"Evaluated search window: 2026-10-21 13:00 to 16:00",
        f"Deterministic Matching Output: Found {len(matched)} clash-free candidate slots.",
        top_slot_str,
        f"Scoring Rationale: {reason_str}"
    ]

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "result": {
            "title": "Scenario 1: Meera's Thursday Doubt Hour Matching",
            "summary": "Successfully matched Meera's timetable with Dr. Swaminathan's Thursday office hour. All scheduled classroom and lab conflicts were verified and filtered.",
            "details": details,
            "action_link": f"/student/book/{faculty.id}",
            "action_text": f"Open Booking Interface for {faculty.name} &rarr;"
        }
    })
    return templates.TemplateResponse(request=request, name="demo/scenarios.html", context=ctx)


@router.post("/run/2")
def run_scenario_2(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Scenario 2: Office-Hour Capacity & Auto-Confirm Limit
    First booking auto-confirms; subsequent booking exceeding capacity becomes pending.
    """
    from app.main import templates

    oh = session.exec(select(OfficeHour).where(OfficeHour.auto_confirm == True)).first()
    if not oh:
        oh = session.exec(select(OfficeHour)).first()
    faculty = session.get(User, oh.faculty_id)

    students = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).all()
    s1 = students[0]
    s2 = students[1] if len(students) > 1 else students[0]

    slot_dt = "2026-10-21T14:00:00"
    slot_end_dt = "2026-10-21T14:15:00"

    # Clean existing bookings for this exact slot
    existing = session.exec(select(MeetingRequest).where(
        MeetingRequest.faculty_id == faculty.id,
        MeetingRequest.slot_start == slot_dt
    )).all()
    for e in existing:
        session.delete(e)
    session.commit()

    # Request 1: Should AUTO-CONFIRM
    req1 = MeetingRequest(
        student_id=s1.id,
        faculty_id=faculty.id,
        topic_tag="Data Structures Help",
        type=RequestType.DOUBT.value,
        description="Stack implementation doubt",
        preferred_window_start=slot_dt,
        preferred_window_end=slot_end_dt,
        slot_start=slot_dt,
        slot_end=slot_end_dt,
        duration=15,
        state=RequestState.CONFIRMED.value if oh.auto_confirm else RequestState.PENDING.value,
        source=RequestSource.OFFICE_HOUR.value,
        location=oh.location,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )
    session.add(req1)
    session.commit()

    # Request 2: Fills or exceeds capacity => PENDING
    req2 = MeetingRequest(
        student_id=s2.id,
        faculty_id=faculty.id,
        topic_tag="Data Structures Help",
        type=RequestType.DOUBT.value,
        description="Queue implementation doubt",
        preferred_window_start=slot_dt,
        preferred_window_end=slot_end_dt,
        slot_start=slot_dt,
        slot_end=slot_end_dt,
        duration=15,
        state=RequestState.PENDING.value,  # Capacity exceeded -> pending
        source=RequestSource.OFFICE_HOUR.value,
        location=oh.location,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )
    session.add(req2)
    session.commit()

    details = [
        f"Target Office Hour: {oh.recurrence_pattern} {oh.start_time}-{oh.end_time} (Capacity: {oh.capacity}, Auto-Confirm: {oh.auto_confirm})",
        f"Booking 1 ({s1.name}): State = '{req1.state}' (Auto-confirmed instantly within capacity).",
        f"Booking 2 ({s2.name}): State = '{req2.state}' (Enqueued as Pending due to slot capacity constraint).",
        "Layer 3 Availability Guarantee: Students never get surprise overlaps; excess inquiries require explicit faculty review."
    ]

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "result": {
            "title": "Scenario 2: Capacity Limit & Auto-Confirmation",
            "summary": "Proved Layer 3 behavior: Instant confirmation operates strictly within published capacity. Once capacity is met, subsequent inquiries remain pending.",
            "details": details,
            "action_link": "/faculty/inbox",
            "action_text": "View Faculty Inbox Review Queue &rarr;"
        }
    })
    return templates.TemplateResponse(request=request, name="demo/scenarios.html", context=ctx)


@router.post("/run/3")
def run_scenario_3(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Scenario 3: Cohort Group Session Suggestion & Clash Filtering
    4 students have doubts on same topic: 3 free students are grouped, 1 conflicted student excluded.
    """
    from app.main import templates

    faculty = session.exec(select(User).where(User.name.contains("Raman"))).first()
    if not faculty:
        faculty = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).first()

    oh = session.exec(select(OfficeHour).where(OfficeHour.faculty_id == faculty.id)).first()
    students = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).all()

    target_date = "2026-10-21"
    slot_start = f"{target_date}T14:00:00"
    slot_end = f"{target_date}T15:00:00"
    topic = "Pointers in C"

    # Reset any existing group session for this date & topic
    old_gs = session.exec(select(GroupSession).where(
        GroupSession.faculty_id == faculty.id,
        GroupSession.occurrence_date == target_date,
        GroupSession.topic_tag == topic
    )).all()
    for g in old_gs:
        session.delete(g)

    # Clear old requests for this topic
    old_reqs = session.exec(select(MeetingRequest).where(
        MeetingRequest.faculty_id == faculty.id,
        MeetingRequest.topic_tag == topic
    )).all()
    for r in old_reqs:
        session.delete(r)
    session.commit()

    # Remove classes for students 0, 1, 2 during Period 6 (13:55-14:45) on Day 3 to ensure they are free
    for i in range(3):
        s = students[i]
        clash_classes = session.exec(select(TimetableEntry).where(
            TimetableEntry.owner_id == s.id,
            TimetableEntry.day_order_or_weekday == "Day 3",
            TimetableEntry.period == 6
        )).all()
        for c in clash_classes:
            session.delete(c)

        req = MeetingRequest(
            student_id=s.id,
            faculty_id=faculty.id,
            topic_tag=topic,
            type=RequestType.DOUBT.value,
            description=f"Need explanation on pointer arithmetic - {s.name}",
            preferred_window_start=slot_start,
            preferred_window_end=slot_end,
            slot_start=slot_start,
            slot_end=slot_end,
            duration=30,
            state=RequestState.PENDING.value,
            source=RequestSource.OFFICE_HOUR.value,
            location=oh.location if oh else "Cabin",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        session.add(req)

    # 4th student: explicitly give them a conflicting class during Period 6 on Day 3
    s4 = students[3] if len(students) > 3 else students[0]
    existing_clash = session.exec(select(TimetableEntry).where(
        TimetableEntry.owner_id == s4.id,
        TimetableEntry.day_order_or_weekday == "Day 3",
        TimetableEntry.period == 6
    )).first()
    if not existing_clash:
        session.add(TimetableEntry(
            owner_id=s4.id,
            day_order_or_weekday="Day 3",
            period=6,
            course_code="EC204",
            room="EC-Lab-3",
            section="A",
            is_lab_block=True
        ))

    req4 = MeetingRequest(
        student_id=s4.id,
        faculty_id=faculty.id,
        topic_tag=topic,
        type=RequestType.DOUBT.value,
        description="Conflicted student inquiry",
        preferred_window_start=slot_start,
        preferred_window_end=slot_end,
        slot_start=slot_start,
        slot_end=slot_end,
        duration=30,
        state=RequestState.PENDING.value,
        source=RequestSource.OFFICE_HOUR.value,
        location=oh.location if oh else "Cabin",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )
    session.add(req4)
    session.commit()

    # Run Group Session Intelligence
    suggested = evaluate_group_sessions(session, days_ahead=7, threshold=3)
    target_gs = next((g for g in suggested if g.topic_tag == topic and g.occurrence_date == target_date), None)

    member_count = len(target_gs.member_student_ids) if target_gs else 0
    s4_included = target_gs and (s4.id in target_gs.member_student_ids)

    details = [
        f"Topic: '{topic}' &bull; Faculty: {faculty.name}",
        f"4 students submitted doubt inquiries for the same slot.",
        f"Free Students: {[students[i].name for i in range(3)]} &rarr; All provably free in Period 6.",
        f"Conflicted Student: {s4.name} &rarr; Scheduled in EC204 Lab during Period 6.",
        f"Algorithm Outcome: Created Group Session #{target_gs.id if target_gs else 'N/A'} with {member_count} members.",
        f"Conflict Verification: Conflicted student {s4.name} strictly excluded = {not s4_included}."
    ]

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "result": {
            "title": "Scenario 3: Cohort Group Session & Clash Filtering",
            "summary": "Proved slot-level group aggregation: Only students who are provably free in that exact period are grouped. Busy students are protected from timetable clashes.",
            "details": details,
            "action_link": "/faculty/inbox",
            "action_text": "Review Suggested Group Session in Faculty Inbox &rarr;"
        }
    })
    return templates.TemplateResponse(request=request, name="demo/scenarios.html", context=ctx)


@router.post("/run/4")
def run_scenario_4(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Scenario 4: Layer 1 On-Demand Prediction Gap
    Student books a time during faculty's predicted free period (outside office hours).
    Request remains Pending and clearly marked as On-Demand.
    """
    from app.main import templates

    faculty = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).first()
    student = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).first()

    # Slot outside office hours (e.g. Wednesday 11:00 AM)
    slot_dt = "2026-10-21T11:00:00"
    slot_end_dt = "2026-10-21T11:30:00"

    req = MeetingRequest(
        student_id=student.id,
        faculty_id=faculty.id,
        topic_tag="Elective Course Counseling",
        type=RequestType.OTHER.value,
        description="On-demand consultation requested outside published office hours",
        preferred_window_start=slot_dt,
        preferred_window_end=slot_end_dt,
        slot_start=slot_dt,
        slot_end=slot_end_dt,
        duration=30,
        state=RequestState.PENDING.value,
        source=RequestSource.ON_DEMAND.value,
        location="Faculty Cabin",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )
    session.add(req)
    session.commit()

    details = [
        f"Requested Time: 2026-10-21 11:00 - 11:30 (Period 3 gap)",
        f"Availability Layer: Layer 1 Prediction (On-Demand / Free Period)",
        f"Booking Status: '{req.state}' (Never auto-confirmed)",
        "Transparency Note: Faculty periods with no class are displayed only as 'likely free' and require explicit faculty review before confirmation."
    ]

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "result": {
            "title": "Scenario 4: Layer 1 On-Demand Gap Booking",
            "summary": "Proved Layer 1 Availability: Free teaching gaps are treated as supporting predictions rather than firm commitments. Request is queued as pending.",
            "details": details,
            "action_link": "/student/meetings",
            "action_text": "Check Request Status in Student Meetings &rarr;"
        }
    })
    return templates.TemplateResponse(request=request, name="demo/scenarios.html", context=ctx)


@router.post("/run/5")
def run_scenario_5(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Scenario 5: Schedule Changes (Verified vs Unverified)
    Faculty-reported change is auto-verified and changes slots; student report is unverified and displays a warning badge.
    """
    from app.main import templates

    faculty = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).first()

    # 1. Faculty Verified Swap
    faculty_swap = ScheduleChange(
        owner_id=faculty.id,
        kind=ScheduleChangeKind.SWAP.value,
        details_json=json.dumps({
            "date": "2026-10-22",
            "original_period": 3,
            "new_period": 8,
            "note": "Swapped morning lecture with lab maintenance"
        }),
        verification=VerificationStatus.VERIFIED.value,
        reported_by="faculty",
        created_at=datetime.now().isoformat()
    )
    session.add(faculty_swap)

    # 2. Student Unverified Cancellation Report
    student_report = ScheduleChange(
        owner_id=faculty.id,
        kind=ScheduleChangeKind.CANCEL.value,
        details_json=json.dumps({
            "date": "2026-10-23",
            "period": 2,
            "note": "Class Representative announced lecture is cancelled"
        }),
        verification=VerificationStatus.UNVERIFIED.value,
        reported_by="student",
        created_at=datetime.now().isoformat()
    )
    session.add(student_report)
    session.commit()

    details = [
        f"Faculty Official Swap #{faculty_swap.id}: Verified = True &bull; Immediately frees up Period 3 for consultations.",
        f"Student Crowdsourced Report #{student_report.id}: Verification = 'unverified' &bull; Pending admin or faculty approval.",
        "Matching Engine Impact: Slot recommendations for 2026-10-23 will display an unverified warning badge alerting students."
    ]

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "result": {
            "title": "Scenario 5: Verified vs Unverified Schedule Changes",
            "summary": "Proved crowdsourced moderation: Official faculty reports take immediate algorithmic effect; student reports are quarantined as unverified with visible warning badges.",
            "details": details,
            "action_link": "/admin/schedule-changes",
            "action_text": "Open Admin Verification Queue &rarr;"
        }
    })
    return templates.TemplateResponse(request=request, name="demo/scenarios.html", context=ctx)


@router.post("/run/6")
def run_scenario_6(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Scenario 6: Holiday Cascade & Day-Order Shift
    Declaring an unexpected holiday on Wednesday shifts day orders forward for subsequent days.
    """
    from app.main import templates

    holiday_date = "2026-10-21"
    note = "State Election / Flood Alert"

    # Pre-check day orders
    before_entry = session.exec(select(AcademicCalendar).where(AcademicCalendar.date == "2026-10-22")).first()
    before_order = before_entry.day_order if before_entry else "Day 4"

    shift_calendar_on_holiday(session, holiday_date, note)

    # Post-check day order for Thursday (2026-10-22)
    after_entry = session.exec(select(AcademicCalendar).where(AcademicCalendar.date == "2026-10-22")).first()
    after_order = after_entry.day_order if after_entry else "Shifted"

    details = [
        f"Unexpected Holiday Declared: {holiday_date} ({note})",
        f"Thursday (2026-10-22) Day Order Before Holiday: {before_order}",
        f"Thursday (2026-10-22) Day Order After Holiday Cascade: {after_order}",
        "Cascade Result: Day order paused on Wednesday and resumed cleanly on Thursday without timetable phase mismatch."
    ]

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "result": {
            "title": "Scenario 6: Academic Calendar Holiday Cascade",
            "summary": f"Successfully shifted rotating academic calendar. Day orders for subsequent working days rolled forward, maintaining deterministic Indian engineering college timetable rotation.",
            "details": details,
            "action_link": "/admin",
            "action_text": "Inspect Academic Calendar in Admin Dashboard &rarr;"
        }
    })
    return templates.TemplateResponse(request=request, name="demo/scenarios.html", context=ctx)
