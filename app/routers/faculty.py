"""
Faculty routes for CampusConnect.
Handles inbox actions (accept, reject, counter, complete, no-show),
office-hour editor, and weekly schedule view.
"""

from datetime import datetime, date, timedelta, time as dt_time
from typing import Optional, List
import json
import os
import time
import shutil
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form, HTTPException, UploadFile, File, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session, record_audit, send_notification
from app.models import (
    User, UserRole, Course, OfficeHour, MeetingRequest,
    TimetableEntry, RequestState, RequestSource, GroupSession, GroupSessionState,
    ScheduleChange, ScheduleChangeKind, VerificationStatus, ExtractionLog
)
from app.auth_mock import get_current_user
from app.routers.common import get_base_context
from app.calendar_engine import DEFAULT_PERIOD_TIMINGS
from app.group_sessions import (
    evaluate_group_sessions, host_group_session, decline_group_session
)

router = APIRouter(prefix="/faculty", tags=["faculty"])


@router.get("/inbox", response_class=HTMLResponse)
def faculty_inbox(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Faculty Inbox: Review pending consultation inquiries,
    upcoming confirmed meetings, manage student attendance (no-shows),
    and approve/decline suggested cohort group sessions.
    """
    from app.main import templates

    # Check for suggested group sessions across upcoming 7 days
    suggested_all = evaluate_group_sessions(session, days_ahead=7)
    suggested_for_faculty = [
        gs for gs in suggested_all
        if gs.faculty_id == current_user.id and gs.state == GroupSessionState.SUGGESTED.value
    ]

    suggested_display = []
    for gs in suggested_for_faculty:
        member_users = [session.get(User, sid) for sid in gs.member_student_ids]
        suggested_display.append({
            "gs": gs,
            "members": [u.name for u in member_users if u]
        })

    stmt_all = select(MeetingRequest).where(
        MeetingRequest.faculty_id == current_user.id
    ).order_by(MeetingRequest.slot_start)
    all_reqs = session.exec(stmt_all).all()

    pending_list = []
    confirmed_list = []
    history_list = []

    for r in all_reqs:
        student = session.get(User, r.student_id)
        course = session.get(Course, r.course_id) if r.course_id else None
        item = {
            "req": r,
            "student": student,
            "course": course
        }
        if r.state == RequestState.PENDING.value:
            pending_list.append(item)
        elif r.state == RequestState.CONFIRMED.value:
            confirmed_list.append(item)
        else:
            history_list.append(item)

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "suggested_display": suggested_display,
        "pending_list": pending_list,
        "confirmed_list": confirmed_list,
        "history_list": history_list
    })
    return templates.TemplateResponse(request=request, name="faculty/inbox.html", context=ctx)


@router.post("/group-sessions/{gs_id}/host")
def handle_host_group_session(
    gs_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Faculty converts suggested group session into confirmed session."""
    gs = session.get(GroupSession, gs_id)
    if not gs or gs.faculty_id != current_user.id:
        raise HTTPException(status_code=404, detail="Group session not found")
    host_group_session(session, gs_id)
    return RedirectResponse(url="/faculty/inbox", status_code=303)


@router.post("/group-sessions/{gs_id}/decline")
def handle_decline_group_session(
    gs_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Faculty declines suggested group session."""
    gs = session.get(GroupSession, gs_id)
    if not gs or gs.faculty_id != current_user.id:
        raise HTTPException(status_code=404, detail="Group session not found")
    decline_group_session(session, gs_id)
    return RedirectResponse(url="/faculty/inbox", status_code=303)



@router.post("/requests/{req_id}/accept")
def accept_request(
    req_id: int,
    location: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Faculty confirms a pending consultation inquiry."""
    req = session.get(MeetingRequest, req_id)
    if not req or req.faculty_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")

    req.state = RequestState.CONFIRMED.value
    if location:
        req.location = location
    req.updated_at = datetime.now().isoformat()
    session.add(req)

    record_audit(
        session=session,
        actor_id=current_user.id,
        action="FACULTY_ACCEPTED_REQUEST",
        entity_type="MeetingRequest",
        entity_id=req.id,
        details=f"Faculty {current_user.name} accepted meeting for {req.slot_start}."
    )
    send_notification(
        session=session,
        user_id=req.student_id,
        text=f"{current_user.name} ACCEPTED your consultation on {req.slot_start[:10]} at {req.slot_start[11:16]} ({req.location})."
    )
    session.commit()
    return RedirectResponse(url="/faculty/inbox", status_code=303)


@router.post("/requests/{req_id}/reject")
def reject_request(
    req_id: int,
    reason: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Faculty declines a consultation inquiry."""
    req = session.get(MeetingRequest, req_id)
    if not req or req.faculty_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")

    req.state = RequestState.REJECTED.value
    req.counter_note = reason
    req.updated_at = datetime.now().isoformat()
    session.add(req)

    record_audit(
        session=session,
        actor_id=current_user.id,
        action="FACULTY_REJECTED_REQUEST",
        entity_type="MeetingRequest",
        entity_id=req.id,
        details=f"Faculty declined request. Reason: {reason or 'Unavailable'}"
    )
    send_notification(
        session=session,
        user_id=req.student_id,
        text=f"Your consultation request with {current_user.name} for {req.slot_start[:10]} was declined: {reason or 'Faculty unavailable'}."
    )
    session.commit()
    return RedirectResponse(url="/faculty/inbox", status_code=303)


@router.post("/requests/{req_id}/counter")
def counter_propose_request(
    req_id: int,
    new_date: str = Form(...),
    new_time: str = Form(...),
    duration: int = Form(15),
    location: str = Form("Cabin"),
    note: str = Form(""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Faculty counter-proposes an alternate consultation time."""
    req = session.get(MeetingRequest, req_id)
    if not req or req.faculty_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")

    dt_start = datetime.fromisoformat(f"{new_date}T{new_time}:00")
    dt_end = dt_start + timedelta(minutes=duration)

    req.slot_start = dt_start.isoformat()
    req.slot_end = dt_end.isoformat()
    req.duration = duration
    req.location = location
    req.counter_note = note
    req.state = RequestState.COUNTERED.value
    req.updated_at = datetime.now().isoformat()
    session.add(req)

    record_audit(
        session=session,
        actor_id=current_user.id,
        action="FACULTY_COUNTER_PROPOSAL",
        entity_type="MeetingRequest",
        entity_id=req.id,
        details=f"Proposed new time: {req.slot_start}. Note: {note}"
    )
    send_notification(
        session=session,
        user_id=req.student_id,
        text=f"{current_user.name} proposed an alternate time: {new_date} at {new_time}. Please review and accept in your Meetings tab."
    )
    session.commit()
    return RedirectResponse(url="/faculty/inbox", status_code=303)


@router.post("/meetings/{req_id}/complete")
def mark_meeting_completed(
    req_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Faculty marks a confirmed consultation as completed."""
    req = session.get(MeetingRequest, req_id)
    if req and req.faculty_id == current_user.id:
        req.state = RequestState.COMPLETED.value
        req.updated_at = datetime.now().isoformat()
        session.add(req)
        session.commit()
    return RedirectResponse(url="/faculty/inbox", status_code=303)


@router.post("/meetings/{req_id}/no-show")
def mark_meeting_no_show(
    req_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Faculty marks a student as a no-show.
    Increments student's no_show_count; automatically locks bookings if count >= 2.
    """
    req = session.get(MeetingRequest, req_id)
    if req and req.faculty_id == current_user.id:
        req.state = RequestState.NO_SHOW.value
        req.updated_at = datetime.now().isoformat()
        session.add(req)

        student = session.get(User, req.student_id)
        if student:
            student.no_show_count += 1
            if student.no_show_count >= 2:
                student.is_blocked = True

            session.add(student)
            record_audit(
                session=session,
                actor_id=current_user.id,
                action="STUDENT_NO_SHOW_RECORDED",
                entity_type="User",
                entity_id=student.id,
                details=f"No-show recorded. Total count: {student.no_show_count}. Blocked: {student.is_blocked}"
            )
            send_notification(
                session=session,
                user_id=student.id,
                text=f"A no-show was recorded for your consultation on {req.slot_start[:10]}. Total no-shows: {student.no_show_count} (2 no-shows locks booking)."
            )

        session.commit()
    return RedirectResponse(url="/faculty/inbox", status_code=303)


@router.get("/office-hours", response_class=HTMLResponse)
def office_hours_editor(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Layer 2: Faculty Commitment Editor.
    Manage recurring consultation windows, capacity, allowed durations, and auto-confirm flags.
    """
    from app.main import templates

    stmt = select(OfficeHour).where(OfficeHour.faculty_id == current_user.id)
    office_hours = session.exec(stmt).all()

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "office_hours": office_hours
    })
    return templates.TemplateResponse(request=request, name="faculty/office_hours.html", context=ctx)


@router.post("/office-hours/add")
def add_office_hour(
    recurrence_type: str = Form("day_order"),
    day_val: str = Form("Day 3"),
    start_time: str = Form("14:00"),
    end_time: str = Form("15:00"),
    location: str = Form("Faculty Cabin"),
    capacity: int = Form(1),
    durations: List[int] = Form([15, 30]),
    auto_confirm: bool = Form(True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Create a new recurring consultation window."""
    rec_pattern = f"{recurrence_type}:{day_val}"
    oh = OfficeHour(
        faculty_id=current_user.id,
        recurrence_pattern=rec_pattern,
        start_time=start_time,
        end_time=end_time,
        location=location,
        capacity=capacity,
        allowed_durations_json=json.dumps(durations),
        auto_confirm=auto_confirm,
        paused_dates_json=json.dumps([])
    )
    session.add(oh)
    session.commit()

    record_audit(
        session=session,
        actor_id=current_user.id,
        action="OFFICE_HOUR_CREATED",
        entity_type="OfficeHour",
        entity_id=oh.id,
        details=f"Created recurring office hour: {rec_pattern} {start_time}-{end_time} at {location}."
    )
    return RedirectResponse(url="/faculty/office-hours", status_code=303)


@router.post("/office-hours/{oh_id}/pause-date")
def pause_office_hour_date(
    oh_id: int,
    pause_date: str = Form(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Pause office hour on a specific date (e.g., attending a conference)."""
    oh = session.get(OfficeHour, oh_id)
    if oh and oh.faculty_id == current_user.id:
        paused = oh.paused_dates
        if pause_date not in paused:
            paused.append(pause_date)
            oh.paused_dates_json = json.dumps(paused)
            session.add(oh)
            session.commit()
    return RedirectResponse(url="/faculty/office-hours", status_code=303)


@router.post("/office-hours/{oh_id}/delete")
def delete_office_hour(
    oh_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Delete an office hour commitment."""
    oh = session.get(OfficeHour, oh_id)
    if oh and oh.faculty_id == current_user.id:
        session.delete(oh)
        session.commit()
    return RedirectResponse(url="/faculty/office-hours", status_code=303)


@router.get("/schedule-change", response_class=HTMLResponse)
def faculty_schedule_change_view(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Form for faculty to report an official timetable or class change."""
    from app.main import templates
    courses = session.exec(select(Course)).all()
    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "courses": courses,
        "today_str": date.today().isoformat(),
        "period_timings": DEFAULT_PERIOD_TIMINGS,
        "success_msg": None
    })
    return templates.TemplateResponse(request=request, name="schedule_change/report_faculty.html", context=ctx)


@router.post("/schedule-change", response_class=HTMLResponse)
def faculty_submit_schedule_change(
    request: Request,
    kind: str = Form(...),
    change_date: str = Form(...),
    period: Optional[int] = Form(None),
    new_period: Optional[int] = Form(None),
    course_id: Optional[int] = Form(None),
    note: str = Form(""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Publish an auto-verified faculty schedule change."""
    from app.main import templates
    details = {
        "date": change_date,
        "period": period,
        "original_period": period,
        "new_period": new_period,
        "course_id": course_id,
        "note": note
    }
    sc = ScheduleChange(
        owner_id=current_user.id,
        kind=kind,
        details_json=json.dumps(details),
        verification=VerificationStatus.VERIFIED.value,
        reported_by="faculty",
        created_at=datetime.now().isoformat()
    )
    session.add(sc)
    record_audit(
        session=session,
        actor_id=current_user.id,
        action="SCHEDULE_CHANGE_REPORTED",
        entity_type="ScheduleChange",
        details=f"Faculty {current_user.name} reported verified {kind} on {change_date}."
    )
    session.commit()

    courses = session.exec(select(Course)).all()
    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "courses": courses,
        "today_str": date.today().isoformat(),
        "period_timings": DEFAULT_PERIOD_TIMINGS,
        "success_msg": f"Successfully published verified {kind.replace('_', ' ')} for {change_date}. Student slot availability updated."
    })
    return templates.TemplateResponse(request=request, name="schedule_change/report_faculty.html", context=ctx)


@router.get("/dashboard", response_class=RedirectResponse)
def faculty_dashboard_redirect():
    """Route /faculty/dashboard to the primary faculty inbox."""
    return RedirectResponse(url="/faculty/inbox", status_code=303)


# =========================================================================
# Faculty Timetable Management & Upload Routes
# =========================================================================

@router.get("/timetable", response_class=HTMLResponse)
def faculty_timetable_view(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Weekly 8-period teaching schedule matrix for faculty.
    Shows teaching classes (busy), published office hours (Layer 2),
    and non-teaching gaps (Layer 1 Likely Free on-demand consultation opportunities).
    """
    from app.main import templates

    stmt = select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)
    entries = session.exec(stmt).all()

    stmt_oh = select(OfficeHour).where(OfficeHour.faculty_id == current_user.id)
    office_hours = session.exec(stmt_oh).all()

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
                "course": c,
                "office_hour": None
            }

    # Overlay office hours on free cells
    for oh in office_hours:
        day_target = None
        if ":" in oh.recurrence_pattern:
            _, val = oh.recurrence_pattern.split(":", 1)
            day_target = val.strip()
        else:
            day_target = oh.recurrence_pattern.strip()

        if day_target in grid:
            for p_num, timing in DEFAULT_PERIOD_TIMINGS.items():
                if grid[day_target][p_num] is None:
                    p_start, p_end = timing
                    if not (oh.end_time <= p_start or oh.start_time >= p_end):
                        grid[day_target][p_num] = {
                            "entry": None,
                            "course": None,
                            "office_hour": oh
                        }

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "grid": grid,
        "days": days,
        "periods": DEFAULT_PERIOD_TIMINGS,
        "has_timetable": len(entries) > 0,
        "total_entries": len(entries)
    })
    return templates.TemplateResponse(request=request, name="faculty/timetable.html", context=ctx)


@router.get("/timetable/sample-csv")
def download_faculty_sample_csv():
    """Download ready-to-use CSV timetable template for faculty."""
    from app.extractor.excel_extractor import generate_sample_csv
    content = generate_sample_csv(role="faculty")
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=campusconnect_sample_faculty_timetable.csv"}
    )


@router.get("/timetable/sample-xlsx")
def download_faculty_sample_xlsx():
    """Download ready-to-use Excel (.xlsx) timetable template for faculty."""
    from app.extractor.excel_extractor import generate_sample_xlsx
    content = generate_sample_xlsx(role="faculty")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=campusconnect_sample_faculty_timetable.xlsx"}
    )


@router.get("/timetable/demo-png")
def download_faculty_demo_png():
    """Download the official Fake_Student_Timetable.png image."""
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
def download_faculty_demo_xlsx():
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


@router.get("/timetable/upload", response_class=HTMLResponse)
def upload_faculty_timetable_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Faculty timetable upload interface (Excel, CSV, image, pasted text)."""
    from app.main import templates
    ctx = get_base_context(request, current_user, session)
    return templates.TemplateResponse(request=request, name="faculty/upload_timetable.html", context=ctx)


@router.post("/timetable/upload", response_class=HTMLResponse)
async def process_faculty_timetable_upload(
    request: Request,
    screenshot: Optional[UploadFile] = File(None),
    pasted_text: Optional[str] = Form(None),
    sample_fixture: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Extract faculty teaching schedule from uploaded file (.xlsx, .csv, image),
    pasted text, or sample fixture. Directs to human-in-the-loop Review Grid.
    """
    from app.main import templates
    from app.extractor.mock_extractor import MockTimetableExtractor

    extractor = MockTimetableExtractor()
    temp_image_path = None

    try:
        if screenshot and screenshot.filename:
            temp_dir = Path(__file__).resolve().parent.parent.parent / "temp_uploads"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_image_path = temp_dir / f"temp_fac_{current_user.id}_{int(time.time())}_{screenshot.filename}"
            with open(temp_image_path, "wb") as buffer:
                shutil.copyfileobj(screenshot.file, buffer)

            extracted = extractor.extract_timetable(str(temp_image_path), role="faculty")
        elif pasted_text and pasted_text.strip():
            extracted = extractor.extract_timetable(pasted_text.strip(), role="faculty")
        elif sample_fixture and sample_fixture.strip():
            extracted = extractor.extract_timetable(sample_fixture.strip(), role="faculty")
        else:
            extracted = extractor.extract_timetable("sample_faculty_teaching", role="faculty")

        ctx = get_base_context(request, current_user, session)
        ctx.update({
            "entries": extracted.entries,
            "extracted_at": time.time(),
            "temp_image_path": str(temp_image_path) if temp_image_path else ""
        })
        return templates.TemplateResponse(request=request, name="faculty/review_timetable.html", context=ctx)

    except Exception as e:
        if temp_image_path and os.path.exists(temp_image_path):
            try:
                os.remove(temp_image_path)
            except Exception:
                pass
        ctx = get_base_context(request, current_user, session)
        ctx["error_msg"] = str(e)
        return templates.TemplateResponse(request=request, name="faculty/upload_timetable.html", context=ctx, status_code=400)


@router.post("/timetable/review/confirm")
async def confirm_faculty_reviewed_timetable(
    request: Request,
    initial_count: int = Form(0),
    extracted_at: float = Form(0.0),
    temp_image_path: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Save confirmed reviewed teaching timetable cells for this faculty member.
    Immediately deletes temporary uploaded file.
    """
    form_data = await request.form()

    # Clear existing teaching entries for this faculty
    stmt_del = select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)
    for old_e in session.exec(stmt_del).all():
        session.delete(old_e)

    # Collect existing courses map
    courses_map = {c.code.upper(): c.id for c in session.exec(select(Course)).all()}

    saved_count = 0
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
            room=room or "LH-201",
            section="CSE-A",
            is_lab_block=is_lab
        )
        session.add(entry)
        saved_count += 1

    # Privacy guarantee: Immediately delete temporary uploaded file
    if temp_image_path and os.path.exists(temp_image_path):
        try:
            os.remove(temp_image_path)
        except Exception:
            pass

    record_audit(
        session=session,
        actor_id=current_user.id,
        action="FACULTY_TIMETABLE_SAVED",
        entity_type="TimetableEntry",
        details=f"Faculty {current_user.name} saved {saved_count} teaching periods. Temporary file deleted."
    )
    send_notification(
        session=session,
        user_id=current_user.id,
        text=f"Your teaching schedule was updated ({saved_count} periods confirmed). Non-teaching periods are now surfaced as likely free consultation slots for students."
    )

    session.commit()
    return RedirectResponse(url="/faculty/timetable", status_code=303)


@router.post("/timetable/review/cancel")
def cancel_faculty_reviewed_timetable(
    temp_image_path: Optional[str] = Form(None)
):
    """Cancel faculty extraction review and permanently delete temporary file."""
    if temp_image_path and os.path.exists(temp_image_path):
        try:
            os.remove(temp_image_path)
        except Exception:
            pass
    return RedirectResponse(url="/faculty/timetable", status_code=303)


@router.post("/timetable/clear")
def clear_faculty_timetable(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Clear all teaching periods for this faculty member."""
    stmt = select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)
    for e in session.exec(stmt).all():
        session.delete(e)
    session.commit()
    return RedirectResponse(url="/faculty/timetable", status_code=303)


