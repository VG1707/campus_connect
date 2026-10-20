"""
Student routes for CampusConnect.
Handles dashboard, booking workflow, matching slot search, meeting lifecycle, and timetable views.
"""

import os
import time
import shutil
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import APIRouter, Request, Depends, Query, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session, record_audit, send_notification
from app.models import (
    User, UserRole, Course, FacultyCourse, OfficeHour, MeetingRequest,
    TimetableEntry, ExtractionLog, RequestState, RequestSource, RequestType,
    ScheduleChange, ScheduleChangeKind, VerificationStatus
)
from app.auth_mock import get_current_user
from app.routers.common import get_base_context
from app.matching import find_matching_slots, MatchingError
from app.calendar_engine import DEFAULT_PERIOD_TIMINGS
from app.extractor.mock_extractor import MockTimetableExtractor

router = APIRouter(prefix="/student", tags=["student"])


@router.get("/dashboard", response_class=HTMLResponse)
def student_dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Design Thinking Student Dashboard:
    Two prominent action paths: 'I have a doubt' vs 'I need something else'.
    Displays timetable status badge and upcoming meetings count.
    """
    from app.main import templates

    # Check student timetable count
    stmt_tt = select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)
    tt_count = len(session.exec(stmt_tt).all())

    # Get student meetings stats
    stmt_meetings = select(MeetingRequest).where(MeetingRequest.student_id == current_user.id)
    all_meetings = session.exec(stmt_meetings).all()
    confirmed_count = sum(1 for m in all_meetings if m.state == RequestState.CONFIRMED.value)
    pending_count = sum(1 for m in all_meetings if m.state == RequestState.PENDING.value)
    countered_count = sum(1 for m in all_meetings if m.state == RequestState.COUNTERED.value)

    # Get recent meetings
    recent_meetings = sorted(
        [m for m in all_meetings if m.state in (RequestState.CONFIRMED.value, RequestState.PENDING.value, RequestState.COUNTERED.value)],
        key=lambda x: x.slot_start
    )[:3]

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "tt_count": tt_count,
        "has_timetable": tt_count > 0,
        "confirmed_count": confirmed_count,
        "pending_count": pending_count,
        "countered_count": countered_count,
        "recent_meetings": recent_meetings
    })
    return templates.TemplateResponse(request=request, name="student/dashboard.html", context=ctx)


@router.get("/book", response_class=HTMLResponse)
@router.get("/book/{faculty_id}", response_class=HTMLResponse)
def book_consultation_form(
    request: Request,
    faculty_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Booking configuration page.
    Selects faculty, course, preferred date window, and duration.
    HTMX triggers slot search dynamically.
    """
    from app.main import templates

    # All faculty
    stmt_f = select(User).where(User.role == UserRole.FACULTY.value).order_by(User.name)
    all_faculty = session.exec(stmt_f).all()

    selected_faculty = None
    faculty_courses = []
    if faculty_id:
        selected_faculty = session.get(User, faculty_id)
        if selected_faculty:
            stmt_fc = select(FacultyCourse).where(FacultyCourse.faculty_id == selected_faculty.id)
            fc_records = session.exec(stmt_fc).all()
            for fc in fc_records:
                c = session.get(Course, fc.course_id)
                if c:
                    faculty_courses.append(c)

    # Check student timetable presence
    stmt_tt = select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)
    has_timetable = len(session.exec(stmt_tt).all()) > 0

    # Default date window: 2026-10-19 to 2026-10-24
    default_from = "2026-10-19"
    default_to = "2026-10-24"

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "all_faculty": all_faculty,
        "selected_faculty": selected_faculty,
        "faculty_courses": faculty_courses,
        "has_timetable": has_timetable,
        "default_from": default_from,
        "default_to": default_to
    })
    return templates.TemplateResponse(request=request, name="student/book.html", context=ctx)


@router.get("/slots-search", response_class=HTMLResponse)
def search_matching_slots(
    request: Request,
    faculty_id: int = Query(...),
    duration: int = Query(15),
    from_date: str = Query("2026-10-19"),
    to_date: str = Query("2026-10-24"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    HTMX-driven deterministic slot search endpoint.
    Returns the partial HTML containing ranked slot cards with transparent scoring.
    """
    from app.main import templates

    try:
        dt_start = datetime.fromisoformat(f"{from_date}T08:00:00")
        dt_end = datetime.fromisoformat(f"{to_date}T18:00:00")
        simulated_now = datetime.fromisoformat("2026-10-19T07:00:00")

        matched_slots = find_matching_slots(
            session=session,
            student_id=current_user.id,
            faculty_id=faculty_id,
            duration_minutes=duration,
            window_start=dt_start,
            window_end=dt_end,
            include_on_demand=True,
            current_time=simulated_now
        )
        error_message = None
    except MatchingError as e:
        matched_slots = []
        error_message = str(e)
    except Exception as e:
        matched_slots = []
        error_message = f"Calculation error: {str(e)}"

    ctx = {
        "request": request,
        "matched_slots": matched_slots,
        "error_message": error_message,
        "duration": duration,
        "current_user": current_user
    }
    return templates.TemplateResponse(request=request, name="student/slots_partial.html", context=ctx)


@router.post("/book")
def submit_booking(
    request: Request,
    faculty_id: int = Form(...),
    course_id: Optional[int] = Form(None),
    topic_tag: Optional[str] = Form(None),
    type: str = Form("doubt"),
    description: str = Form(""),
    slot_start: str = Form(...),
    slot_end: str = Form(...),
    duration: int = Form(...),
    source: str = Form("office_hour"),
    location: str = Form(""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Confirm consultation booking request.
    Applies Layer 3 rules:
      - Inside office hour + auto_confirm=True + capacity left => CONFIRMED immediately!
      - Outside office hour (on_demand) OR auto_confirm=False => PENDING!
    """
    faculty = session.get(User, faculty_id)
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    # Accountability check: student locked from booking if >= 2 no-shows
    if current_user.is_blocked:
        raise HTTPException(
            status_code=403,
            detail="Booking account locked due to excessive no-shows (2 or more). Please contact academic administration to reset your account."
        )

    # Rate limit check: max 3 pending requests
    stmt_pending = select(MeetingRequest).where(
        MeetingRequest.student_id == current_user.id,
        MeetingRequest.state == RequestState.PENDING.value
    )
    if len(session.exec(stmt_pending).all()) >= 3:
        raise HTTPException(status_code=400, detail="Rate limit reached: Max 3 pending requests allowed.")

    # Determine state based on Layer 3 availability rules
    state = RequestState.PENDING.value
    auto_confirmed = False

    if source == RequestSource.OFFICE_HOUR.value:
        # Check office hour auto_confirm setting
        stmt_oh = select(OfficeHour).where(OfficeHour.faculty_id == faculty_id)
        oh = session.exec(stmt_oh).first()
        if oh and oh.auto_confirm:
            # Check capacity in that slot
            stmt_exist = select(MeetingRequest).where(
                MeetingRequest.faculty_id == faculty_id,
                MeetingRequest.state == RequestState.CONFIRMED.value,
                MeetingRequest.slot_start == slot_start
            )
            booked_count = len(session.exec(stmt_exist).all())
            if booked_count < oh.capacity:
                state = RequestState.CONFIRMED.value
                auto_confirmed = True

    meeting_req = MeetingRequest(
        student_id=current_user.id,
        faculty_id=faculty_id,
        course_id=course_id,
        topic_tag=topic_tag or "General",
        type=type,
        description=description,
        preferred_window_start=slot_start,
        preferred_window_end=slot_end,
        slot_start=slot_start,
        slot_end=slot_end,
        duration=duration,
        state=state,
        source=source,
        location=location or (oh.location if source == RequestSource.OFFICE_HOUR.value and oh else "Faculty Cabin"),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )
    session.add(meeting_req)
    session.commit()
    session.refresh(meeting_req)

    # Record Audit & Notifications
    if auto_confirmed:
        record_audit(
            session=session,
            actor_id=current_user.id,
            action="BOOKING_AUTO_CONFIRMED",
            entity_type="MeetingRequest",
            entity_id=meeting_req.id,
            details=f"Auto-confirmed inside office hour with {faculty.name} on {slot_start}."
        )
        send_notification(
            session=session,
            user_id=current_user.id,
            text=f"Meeting with {faculty.name} on {slot_start[:10]} at {slot_start[11:16]} is INSTANTLY CONFIRMED!"
        )
        send_notification(
            session=session,
            user_id=faculty_id,
            text=f"New auto-confirmed consultation with {current_user.name} for {meeting_req.topic_tag} on {slot_start[:10]}."
        )
    else:
        record_audit(
            session=session,
            actor_id=current_user.id,
            action="REQUEST_SUBMITTED_PENDING",
            entity_type="MeetingRequest",
            entity_id=meeting_req.id,
            details=f"Submitted pending consultation inquiry to {faculty.name} for {slot_start}."
        )
        send_notification(
            session=session,
            user_id=current_user.id,
            text=f"Request sent to {faculty.name} for {slot_start[:10]} at {slot_start[11:16]}. Status: Pending review."
        )
        send_notification(
            session=session,
            user_id=faculty_id,
            text=f"New pending consultation request from {current_user.name} for {meeting_req.topic_tag}."
        )

    return RedirectResponse(url="/student/meetings?success=1", status_code=303)


@router.get("/meetings", response_class=HTMLResponse)
def student_meetings(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """List of all student meetings categorized by state."""
    from app.main import templates

    stmt = select(MeetingRequest).where(
        MeetingRequest.student_id == current_user.id
    ).order_by(MeetingRequest.slot_start.desc())
    all_reqs = session.exec(stmt).all()

    # Enrich with faculty name and course name
    enriched = []
    for r in all_reqs:
        faculty = session.get(User, r.faculty_id)
        course = session.get(Course, r.course_id) if r.course_id else None
        enriched.append({
            "req": r,
            "faculty": faculty,
            "course": course
        })

    confirmed = [e for e in enriched if e["req"].state == RequestState.CONFIRMED.value]
    pending = [e for e in enriched if e["req"].state == RequestState.PENDING.value]
    countered = [e for e in enriched if e["req"].state == RequestState.COUNTERED.value]
    history = [e for e in enriched if e["req"].state in (
        RequestState.COMPLETED.value, RequestState.REJECTED.value,
        RequestState.CANCELLED.value, RequestState.NO_SHOW.value
    )]

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "confirmed": confirmed,
        "pending": pending,
        "countered": countered,
        "history": history
    })
    return templates.TemplateResponse(request=request, name="student/meetings.html", context=ctx)


@router.post("/meetings/{req_id}/cancel")
def cancel_student_meeting(
    req_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Student cancels an existing confirmed or pending consultation."""
    req = session.get(MeetingRequest, req_id)
    if req and req.student_id == current_user.id:
        req.state = RequestState.CANCELLED.value
        req.updated_at = datetime.now().isoformat()
        session.add(req)

        record_audit(
            session=session,
            actor_id=current_user.id,
            action="STUDENT_CANCELLED_MEETING",
            entity_type="MeetingRequest",
            entity_id=req.id,
            details=f"Student {current_user.name} cancelled meeting scheduled for {req.slot_start}."
        )
        send_notification(
            session=session,
            user_id=req.faculty_id,
            text=f"Consultation with {current_user.name} on {req.slot_start[:10]} was cancelled by the student."
        )
        session.commit()

    return RedirectResponse(url="/student/meetings", status_code=303)


@router.post("/meetings/{req_id}/accept-counter")
def accept_counter_proposal(
    req_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Student accepts faculty's counter-proposed time slot."""
    req = session.get(MeetingRequest, req_id)
    if req and req.student_id == current_user.id and req.state == RequestState.COUNTERED.value:
        req.state = RequestState.CONFIRMED.value
        req.updated_at = datetime.now().isoformat()
        session.add(req)

        record_audit(
            session=session,
            actor_id=current_user.id,
            action="COUNTER_PROPOSAL_ACCEPTED",
            entity_type="MeetingRequest",
            entity_id=req.id,
            details=f"Student accepted counter-proposal for {req.slot_start}."
        )
        send_notification(
            session=session,
            user_id=req.faculty_id,
            text=f"{current_user.name} accepted your counter-proposed time for {req.slot_start[:10]} at {req.slot_start[11:16]}."
        )
        session.commit()

    return RedirectResponse(url="/student/meetings", status_code=303)


@router.post("/meetings/{req_id}/decline-counter")
def decline_counter_proposal(
    req_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Student declines faculty's counter-proposal."""
    req = session.get(MeetingRequest, req_id)
    if req and req.student_id == current_user.id and req.state == RequestState.COUNTERED.value:
        req.state = RequestState.CANCELLED.value
        req.updated_at = datetime.now().isoformat()
        session.add(req)

        send_notification(
            session=session,
            user_id=req.faculty_id,
            text=f"{current_user.name} declined the counter-proposed time for {req.slot_start[:10]}."
        )
        session.commit()

    return RedirectResponse(url="/student/meetings", status_code=303)


@router.get("/timetable", response_class=HTMLResponse)
def student_timetable_view(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Weekly 8-period timetable matrix for the student.
    Shows Smart Matching status and manual edit controls.
    """
    from app.main import templates

    stmt = select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)
    entries = session.exec(stmt).all()

    # Build matrix: Day Label -> Period (1..12) -> entry
    days = ["Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Day 6"]
    grid = {d: {p: None for p in DEFAULT_PERIOD_TIMINGS.keys()} for d in days}

    courses_cache = {}
    for entry in entries:
        if entry.course_id and entry.course_id not in courses_cache:
            courses_cache[entry.course_id] = session.get(Course, entry.course_id)
        c = courses_cache.get(entry.course_id)
        if entry.day_order_or_weekday in grid and entry.period in grid[entry.day_order_or_weekday]:
            grid[entry.day_order_or_weekday][entry.period] = {
                "entry": entry,
                "course": c
            }

    # All available courses for manual entry modal
    all_courses = session.exec(select(Course).order_by(Course.code)).all()

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "grid": grid,
        "days": days,
        "periods": DEFAULT_PERIOD_TIMINGS,
        "has_timetable": len(entries) > 0,
        "total_entries": len(entries),
        "all_courses": all_courses
    })
    return templates.TemplateResponse(request=request, name="student/timetable.html", context=ctx)


@router.post("/timetable/add")
def add_timetable_entry(
    day: str = Form(...),
    period: int = Form(...),
    course_id: int = Form(...),
    room: str = Form("LH-201"),
    is_lab: bool = Form(False),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Add a single manual class entry to student's timetable."""
    entry = TimetableEntry(
        owner_id=current_user.id,
        day_order_or_weekday=day,
        period=period,
        course_id=course_id,
        room=room,
        is_lab_block=is_lab
    )
    session.add(entry)
    session.commit()
    return RedirectResponse(url="/student/timetable", status_code=303)


@router.post("/timetable/clear")
def clear_timetable(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Clear student timetable to test 'Manual Mode (Timetable Inactive)'."""
    stmt = select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)
    for e in session.exec(stmt).all():
        session.delete(e)
    session.commit()
    return RedirectResponse(url="/student/timetable", status_code=303)


# =========================================================================
# Timetable Screenshot Extraction & Review Screen Routes (Phase 3)
# =========================================================================

@router.get("/timetable/upload", response_class=HTMLResponse)
def upload_timetable_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Screenshot upload interface with privacy and deletion guarantees."""
    from app.main import templates

    ctx = get_base_context(request, current_user, session)
    return templates.TemplateResponse(request=request, name="student/upload_timetable.html", context=ctx)


@router.get("/timetable/sample-csv")
def download_student_sample_csv():
    """Download ready-to-use CSV timetable template for testing with spreadsheet apps."""
    from fastapi.responses import Response
    from app.extractor.excel_extractor import generate_sample_csv
    content = generate_sample_csv(role="student")
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=campusconnect_sample_student_timetable.csv"}
    )


@router.get("/timetable/sample-xlsx")
def download_student_sample_xlsx():
    """Download ready-to-use Excel (.xlsx) timetable template for testing with Excel."""
    from fastapi.responses import Response
    from app.extractor.excel_extractor import generate_sample_xlsx
    content = generate_sample_xlsx(role="student")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=campusconnect_sample_student_timetable.xlsx"}
    )


@router.get("/timetable/demo-png")
def download_student_demo_png():
    """Download the official Fake_Student_Timetable.png image for OCR testing."""
    from fastapi.responses import FileResponse
    p = Path(__file__).resolve().parent.parent.parent / "sample_data" / "Fake_Student_Timetable.png"
    if not p.exists():
        p = Path(__file__).resolve().parent.parent.parent / "data" / "fixtures" / "Fake_Student_Timetable.png"
    return FileResponse(
        path=str(p),
        media_type="image/png",
        filename="Fake_Student_Timetable.png"
    )


@router.get("/timetable/demo-xlsx")
def download_student_demo_xlsx():
    """Download the official CampusConnect_Fake_Timetables.xlsx multi-sheet workbook."""
    from fastapi.responses import FileResponse
    p = Path(__file__).resolve().parent.parent.parent / "sample_data" / "CampusConnect_Fake_Timetables.xlsx"
    if not p.exists():
        p = Path(__file__).resolve().parent.parent.parent / "data" / "fixtures" / "CampusConnect_Fake_Timetables.xlsx"
    return FileResponse(
        path=str(p),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="CampusConnect_Fake_Timetables.xlsx"
    )


@router.post("/timetable/upload", response_class=HTMLResponse)
async def process_timetable_upload(
    request: Request,
    screenshot: Optional[UploadFile] = File(None),
    pasted_text: Optional[str] = Form(None),
    sample_fixture: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Extract timetable from uploaded file (.xlsx, .csv, image), pasted text, or demo sample fixture.
    Redirects to the human-in-the-loop Editable Review Grid.
    """
    from app.main import templates

    extractor = MockTimetableExtractor()
    temp_image_path = None

    try:
        if screenshot and screenshot.filename:
            # Save uploaded file temporarily for extraction
            temp_dir = Path(__file__).resolve().parent.parent.parent / "temp_uploads"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_image_path = temp_dir / f"temp_{current_user.id}_{int(time.time())}_{screenshot.filename}"
            with open(temp_image_path, "wb") as buffer:
                shutil.copyfileobj(screenshot.file, buffer)

            extracted = extractor.extract_timetable(str(temp_image_path), role="student")
        elif pasted_text and pasted_text.strip():
            extracted = extractor.extract_timetable(pasted_text.strip(), role="student")
        elif sample_fixture:
            extracted = extractor.extract_timetable(sample_fixture, role="student")
        else:
            # Default fallback extraction
            extracted = extractor.extract_timetable("Fake_Student_Timetable.png", role="student")

        ctx = get_base_context(request, current_user, session)
        ctx.update({
            "entries": extracted.entries,
            "extracted_at": time.time(),
            "temp_image_path": str(temp_image_path) if temp_image_path else ""
        })
        return templates.TemplateResponse(request=request, name="student/review_timetable.html", context=ctx)

    except Exception as e:
        if temp_image_path and os.path.exists(temp_image_path):
            try:
                os.remove(temp_image_path)
            except Exception:
                pass
        ctx = get_base_context(request, current_user, session)
        ctx["error_msg"] = str(e)
        return templates.TemplateResponse(request=request, name="student/upload_timetable.html", context=ctx, status_code=400)


@router.post("/timetable/review/confirm")
async def confirm_reviewed_timetable(
    request: Request,
    initial_count: int = Form(0),
    extracted_at: float = Form(0.0),
    temp_image_path: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Save confirmed reviewed timetable cells to the database.
    Immediately deletes temporary uploaded image file.
    Logs extraction metrics (cells edited, seconds to save) without personal data.
    """
    form_data = await request.form()

    # Clear existing timetable for this student
    stmt_del = select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)
    for old_e in session.exec(stmt_del).all():
        session.delete(old_e)

    # Collect courses cache
    courses_map = {c.code.upper(): c.id for c in session.exec(select(Course)).all()}

    saved_count = 0
    edited_count = 0

    # Parse rows: keys formatted as day_N, period_N, course_code_N, etc.
    # Group by row index N
    row_indices = set()
    for k in form_data.keys():
        if "_" in k and k.split("_")[-1].isdigit():
            row_indices.add(int(k.split("_")[-1]))

    for idx in sorted(list(row_indices)):
        day = form_data.get(f"day_{idx}")
        period_str = form_data.get(f"period_{idx}", "1")
        code = form_data.get(f"course_code_{idx}", "").strip().upper()
        name = form_data.get(f"course_name_{idx}", "").strip()
        room = form_data.get(f"room_{idx}", "LH-201").strip()
        is_lab = form_data.get(f"is_lab_{idx}") == "true"

        if not code or not day:
            continue

        period = int(period_str.replace("P", "")) if period_str else 1

        # Check course ID or create course if brand new
        course_id = courses_map.get(code)
        if not course_id:
            new_c = Course(
                code=code,
                name=name or code,
                department=current_user.department,
                topic_tags_json="[]"
            )
            session.add(new_c)
            session.commit()
            session.refresh(new_c)
            course_id = new_c.id
            courses_map[code] = course_id

        entry = TimetableEntry(
            owner_id=current_user.id,
            day_order_or_weekday=day,
            period=period,
            course_id=course_id,
            room=room or "LH-101",
            section=current_user.section or "CSE-A",
            is_lab_block=is_lab
        )
        session.add(entry)
        saved_count += 1

    # Calculate review metrics
    seconds_to_save = max(0.5, time.time() - extracted_at) if extracted_at > 0 else 5.0
    cells_edited = abs(saved_count - initial_count)

    log_entry = ExtractionLog(
        student_id=current_user.id,
        source_type="mock_extractor",
        cells_extracted=initial_count,
        cells_edited=cells_edited,
        seconds_to_save=round(seconds_to_save, 1),
        created_at=datetime.now().isoformat()
    )
    session.add(log_entry)

    # Privacy guarantee: Immediately delete uploaded screenshot from disk
    if temp_image_path and os.path.exists(temp_image_path):
        try:
            os.remove(temp_image_path)
        except Exception:
            pass

    record_audit(
        session=session,
        actor_id=current_user.id,
        action="TIMETABLE_EXTRACTED_SAVED",
        entity_type="TimetableEntry",
        details=f"Extracted and saved {saved_count} periods. Review time: {round(seconds_to_save, 1)}s. Image permanently deleted."
    )
    send_notification(
        session=session,
        user_id=current_user.id,
        text=f"Your timetable was updated ({saved_count} periods confirmed). Smart availability matching is now active."
    )

    session.commit()
    return RedirectResponse(url="/student/timetable", status_code=303)


@router.post("/timetable/review/cancel")
def cancel_reviewed_timetable(
    temp_image_path: Optional[str] = Form(None)
):
    """
    Cancel extraction review: immediately deletes uploaded screenshot from disk
    and returns to timetable without making changes.
    """
    if temp_image_path and os.path.exists(temp_image_path):
        try:
            os.remove(temp_image_path)
        except Exception:
            pass
    return RedirectResponse(url="/student/timetable", status_code=303)


@router.get("/schedule-change", response_class=HTMLResponse)
def student_schedule_change_view(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Form for student to report an unverified timetable change or classroom swap."""
    from app.main import templates
    faculty_list = session.exec(select(User).where(User.role == UserRole.FACULTY.value).order_by(User.name)).all()
    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "faculty_list": faculty_list,
        "today_str": date.today().isoformat(),
        "period_timings": DEFAULT_PERIOD_TIMINGS,
        "success_msg": None
    })
    return templates.TemplateResponse(request=request, name="schedule_change/report_student.html", context=ctx)


@router.post("/schedule-change", response_class=HTMLResponse)
def student_submit_schedule_change(
    request: Request,
    faculty_id: int = Form(...),
    kind: str = Form(...),
    change_date: str = Form(...),
    period: Optional[int] = Form(None),
    new_period: Optional[int] = Form(None),
    note: str = Form(""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Submit an unverified crowdsourced student schedule change."""
    import json
    from app.main import templates
    details = {
        "date": change_date,
        "period": period,
        "original_period": period,
        "new_period": new_period,
        "note": note
    }
    sc = ScheduleChange(
        owner_id=faculty_id,
        kind=kind,
        details_json=json.dumps(details),
        verification=VerificationStatus.UNVERIFIED.value,
        reported_by="student",
        created_at=datetime.now().isoformat()
    )
    session.add(sc)
    record_audit(
        session=session,
        actor_id=current_user.id,
        action="STUDENT_SCHEDULE_CHANGE_REPORTED",
        entity_type="ScheduleChange",
        details=f"Student {current_user.name} reported unverified {kind} on {change_date} for faculty #{faculty_id}."
    )
    session.commit()

    faculty_list = session.exec(select(User).where(User.role == UserRole.FACULTY.value).order_by(User.name)).all()
    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "faculty_list": faculty_list,
        "today_str": date.today().isoformat(),
        "period_timings": DEFAULT_PERIOD_TIMINGS,
        "success_msg": f"Report submitted! It is marked as Unverified and will warn students during slot matching until approved."
    })
    return templates.TemplateResponse(request=request, name="schedule_change/report_student.html", context=ctx)

