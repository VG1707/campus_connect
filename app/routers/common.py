"""
Common routes: Home redirection, role switcher, faculty directory, and notifications.
"""

from typing import Optional, List
from fastapi import APIRouter, Request, Depends, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session, record_audit
from app.models import (
    User, UserRole, Course, FacultyCourse, OfficeHour, Notification,
    TimetableEntry, MeetingRequest, ExtractionLog
)
from app.auth_mock import get_current_user, get_mock_users_by_role, COOKIE_NAME

router = APIRouter()


def get_base_context(request: Request, current_user: User, session: Session) -> dict:
    """Standard context dictionary passed to all Jinja2 templates."""
    # Count unread notifications for current user
    stmt = select(Notification).where(
        Notification.user_id == current_user.id,
        Notification.read == False
    )
    unread_count = len(session.exec(stmt).all())

    return {
        "request": request,
        "current_user": current_user,
        "mock_users": get_mock_users_by_role(session),
        "unread_count": unread_count
    }


from app.auth_mock import get_current_user, get_authenticated_user_or_none, get_mock_users_by_role, COOKIE_NAME


def get_landing_preview_context(session: Session) -> dict:
    """
    Build real live demo state for the burgundy card preview:
    Queries seeded student Meera Nair, sample faculty, her real upcoming meetings,
    her real timetable entries, and actual matching consultation slots.
    """
    from datetime import datetime, timedelta
    from app.matching import find_matching_slots

    demo_student = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).first()
    demo_faculty = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).first()

    # Sample faculty with courses and office hour status
    faculty_rows = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).all()[:3]
    sample_faculty_list = []
    for f in faculty_rows:
        stmt_c = (
            select(Course)
            .join(FacultyCourse, FacultyCourse.course_id == Course.id)
            .where(FacultyCourse.faculty_id == f.id)
        )
        courses = session.exec(stmt_c).all()
        oh_count = len(session.exec(select(OfficeHour).where(OfficeHour.faculty_id == f.id)).all())
        sample_faculty_list.append({
            "id": f.id,
            "name": f.name,
            "department": f.department,
            "course": courses[0].name if courses else "Engineering",
            "has_office_hours": oh_count > 0,
        })

    # Upcoming meetings for demo student
    demo_meetings = []
    if demo_student:
        meetings = session.exec(
            select(MeetingRequest)
            .where(MeetingRequest.student_id == demo_student.id)
            .order_by(MeetingRequest.slot_start)
        ).all()
        for m in meetings[:2]:
            fac = session.get(User, m.faculty_id)
            demo_meetings.append({
                "id": m.id,
                "topic": m.topic_tag or m.description or "Doubt Session",
                "state": m.state,
                "slot_start": m.slot_start,
                "slot_end": m.slot_end,
                "location": m.location,
                "faculty_name": fac.name if fac else "Faculty",
                "duration": m.duration
            })

    # Timetable entries for demo student (Day 1 representative schedule)
    demo_timetable = []
    if demo_student:
        stmt_tt = (
            select(TimetableEntry, Course)
            .where(TimetableEntry.owner_id == demo_student.id, TimetableEntry.day_order_or_weekday == "Day 1")
            .join(Course, TimetableEntry.course_id == Course.id, isouter=True)
            .order_by(TimetableEntry.period)
        )
        for entry, course in session.exec(stmt_tt).all()[:4]:
            demo_timetable.append({
                "period": entry.period,
                "code": course.code if course else "GAP",
                "name": course.name if course else "Break / Free Gap",
                "room": entry.room,
                "is_lab": entry.is_lab_block
            })

    # Matched consultation slots calculated live via deterministic matching engine
    live_slots = []
    if demo_student and demo_faculty:
        try:
            now = datetime(2026, 10, 19, 9, 0, 0)
            slots = find_matching_slots(
                session=session,
                student_id=demo_student.id,
                faculty_id=demo_faculty.id,
                duration_minutes=15,
                window_start=now,
                window_end=now + timedelta(days=7),
                current_time=now
            )
            for s in slots[:3]:
                live_slots.append({
                    "start": s.slot_start.strftime("%a %b %d, %H:%M"),
                    "end": s.slot_end.strftime("%H:%M"),
                    "time_str": f"{s.slot_start.strftime('%H:%M')} - {s.slot_end.strftime('%H:%M')}",
                    "day_str": s.slot_start.strftime("%A"),
                    "can_auto_confirm": s.can_auto_confirm,
                    "remaining_capacity": s.remaining_capacity,
                    "capacity": s.capacity,
                    "location": s.location,
                    "source": s.source
                })
        except Exception:
            pass

    return {
        "demo_student": demo_student,
        "demo_faculty": demo_faculty,
        "sample_faculty": sample_faculty_list,
        "demo_meetings": demo_meetings,
        "demo_timetable": demo_timetable,
        "live_slots": live_slots
    }


@router.get("/", response_class=HTMLResponse)
def index_landing(
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Landing page:
    If user already has an active session cookie, route them to their role dashboard.
    If not, display the Landing Page with live demo preview in the burgundy card.
    """
    from app.main import templates

    user = get_authenticated_user_or_none(request, session)
    if user:
        if user.role == UserRole.FACULTY.value:
            return RedirectResponse(url="/faculty/inbox", status_code=303)
        elif user.role == UserRole.ADMIN.value:
            return RedirectResponse(url="/admin", status_code=303)
        return RedirectResponse(url="/student/dashboard", status_code=303)

    # Render landing page with live demo preview state
    fallback_user = session.exec(select(User).order_by(User.id)).first()
    ctx = {
        "request": request,
        "current_user": fallback_user,
        "mock_users": get_mock_users_by_role(session),
        "unread_count": 0
    }
    ctx.update(get_landing_preview_context(session))
    return templates.TemplateResponse(request=request, name="auth/landing.html", context=ctx)


@router.get("/login", response_class=HTMLResponse)
def login_landing(
    request: Request,
    session: Session = Depends(get_session)
):
    """Dedicated login entry screen with live demo preview."""
    from app.main import templates

    fallback_user = session.exec(select(User).order_by(User.id)).first()
    ctx = {
        "request": request,
        "current_user": fallback_user,
        "mock_users": get_mock_users_by_role(session),
        "unread_count": 0
    }
    ctx.update(get_landing_preview_context(session))
    return templates.TemplateResponse(request=request, name="auth/landing.html", context=ctx)


@router.get("/login/student", response_class=HTMLResponse)
def login_student_selector(
    request: Request,
    session: Session = Depends(get_session)
):
    """List of all seeded student accounts for simulated login."""
    from app.main import templates

    stmt = select(User).where(User.role == UserRole.STUDENT.value).order_by(User.id)
    students = session.exec(stmt).all()
    ctx = {
        "request": request,
        "current_user": students[0] if students else None,
        "mock_users": get_mock_users_by_role(session),
        "unread_count": 0,
        "users": students,
        "role_title": "Student",
        "role_type": "student"
    }
    return templates.TemplateResponse(request=request, name="auth/login_role.html", context=ctx)


@router.get("/login/faculty", response_class=HTMLResponse)
def login_faculty_selector(
    request: Request,
    session: Session = Depends(get_session)
):
    """List of all seeded faculty accounts for simulated login."""
    from app.main import templates

    stmt = select(User).where(User.role == UserRole.FACULTY.value).order_by(User.id)
    faculty = session.exec(stmt).all()
    ctx = {
        "request": request,
        "current_user": faculty[0] if faculty else None,
        "mock_users": get_mock_users_by_role(session),
        "unread_count": 0,
        "users": faculty,
        "role_title": "Faculty",
        "role_type": "faculty"
    }
    return templates.TemplateResponse(request=request, name="auth/login_role.html", context=ctx)


@router.get("/login/admin")
def login_admin_quick(
    session: Session = Depends(get_session)
):
    """1-click login as Campus Administrator."""
    stmt = select(User).where(User.role == UserRole.ADMIN.value)
    admin = session.exec(stmt).first()
    if not admin:
        return RedirectResponse(url="/", status_code=303)

    resp = RedirectResponse(url="/admin", status_code=303)
    resp.set_cookie(key=COOKIE_NAME, value=str(admin.id), path="/", max_age=86400)
    return resp


@router.get("/logout")
def logout(
    request: Request
):
    """Simulated logout: clears the campus_user_id cookie and redirects to /login."""
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


@router.get("/auth/switch")
def switch_user(
    request: Request,
    user_id: int = Query(...),
    onboarding: Optional[bool] = Query(False),
    session: Session = Depends(get_session)
):
    """Switch active demo user and redirect to setup gateway or appropriate home."""
    user = session.get(User, user_id)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    if onboarding:
        target_url = "/welcome/setup"
    else:
        # Determine destination based on user role
        target_url = "/student/dashboard"
        if user.role == UserRole.FACULTY.value:
            target_url = "/faculty/inbox"
        elif user.role == UserRole.ADMIN.value:
            target_url = "/admin"

    response = RedirectResponse(url=target_url, status_code=303)
    response.set_cookie(key=COOKIE_NAME, value=str(user.id), path="/", max_age=86400)
    return response


@router.get("/welcome/setup", response_class=HTMLResponse)
def welcome_setup_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Post-login onboarding gateway prompting the user to either upload a timetable
    or proceed with the pre-loaded synthetic timetable.
    """
    from app.main import templates
    from app.auth_mock import get_mock_users_by_role
    return templates.TemplateResponse(
        request=request,
        name="auth/onboarding_timetable.html",
        context={
            "request": request,
            "current_user": current_user,
            "mock_users": get_mock_users_by_role(session),
        }
    )


@router.get("/faculty-directory", response_class=HTMLResponse)
def faculty_directory(
    request: Request,
    search: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    only_office_hours: Optional[bool] = Query(False),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Searchable, filterable Faculty Directory with Three-Layer Availability badges
    and Language-Based Matching prioritisation.
    """
    from app.main import templates

    # Query all faculty members
    stmt = select(User).where(User.role == UserRole.FACULTY.value)
    if department and department != "All":
        stmt = stmt.where(User.department == department)
    all_faculty = session.exec(stmt).all()

    # Collect all available languages across all faculty for filter dropdown
    all_faculty_unfiltered = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).all()
    all_languages = sorted(list({
        l.strip()
        for f in all_faculty_unfiltered
        for l in f.languages
        if l.strip()
    }))

    student_langs_lower = {l.strip().lower() for l in current_user.languages}

    faculty_list = []
    for f in all_faculty:
        # Check language filter
        if language and language != "All" and language.strip():
            fac_langs_lower = [l.strip().lower() for l in f.languages]
            if language.strip().lower() not in fac_langs_lower:
                continue

        # Get taught courses
        stmt_fc = select(FacultyCourse).where(FacultyCourse.faculty_id == f.id)
        fc_records = session.exec(stmt_fc).all()
        courses = [session.get(Course, fc.course_id) for fc in fc_records if session.get(Course, fc.course_id)]

        # Get published office hours (Layer 2)
        stmt_oh = select(OfficeHour).where(OfficeHour.faculty_id == f.id)
        office_hours = session.exec(stmt_oh).all()

        has_oh = len(office_hours) > 0
        if only_office_hours and not has_oh:
            continue

        # Check search string match (name, course code, course name, topic tags, languages)
        if search:
            q = search.lower().strip()
            name_match = q in f.name.lower() or q in f.email.lower()
            course_match = any(
                q in c.code.lower() or q in c.name.lower() or any(q in t.lower() for t in c.topic_tags)
                for c in courses
            )
            lang_match = any(q in l.lower() for l in f.languages)
            if not (name_match or course_match or lang_match):
                continue

        # Check shared languages with current user
        shared = [l.strip() for l in f.languages if l.strip().lower() in student_langs_lower]
        has_lang_match = len(shared) > 0

        # Build transparent recommendation reason
        reasons = []
        if has_lang_match:
            reasons.append(f"Shared language: {', '.join(shared)}")
        else:
            reasons.append(f"Speaks: {', '.join(f.languages)} (no language overlap)")

        if courses:
            reasons.append(f"{len(courses)} course(s) taught")
        if has_oh:
            reasons.append(f"{len(office_hours)} office hour window(s)")

        faculty_list.append({
            "faculty": f,
            "courses": courses,
            "office_hours": office_hours,
            "has_office_hours": has_oh,
            "shared_languages": shared,
            "has_language_match": has_lang_match,
            "recommendation_reason": "; ".join(reasons)
        })

    # Prioritise faculty who share at least one language with the student
    # Note: Does NOT hide non-matching faculty! They are listed after matching faculty.
    if current_user.role == UserRole.STUDENT.value:
        faculty_list.sort(
            key=lambda x: (
                1 if x["has_language_match"] else 0,
                1 if x["has_office_hours"] else 0,
                x["faculty"].name
            ),
            reverse=True
        )

    # Get unique department list
    dept_stmt = select(User.department).where(User.role == UserRole.FACULTY.value).distinct()
    departments = sorted(list(session.exec(dept_stmt).all()))

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "faculty_list": faculty_list,
        "departments": departments,
        "all_languages": all_languages,
        "current_search": search or "",
        "current_dept": department or "All",
        "current_language": language or "All",
        "only_office_hours": only_office_hours
    })
    return templates.TemplateResponse(request=request, name="directory.html", context=ctx)


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Display all notifications for the active user."""
    from app.main import templates

    stmt = select(Notification).where(
        Notification.user_id == current_user.id
    ).order_by(Notification.id.desc())
    notifications = session.exec(stmt).all()

    ctx = get_base_context(request, current_user, session)
    ctx.update({
        "notifications": notifications
    })
    return templates.TemplateResponse(request=request, name="notifications.html", context=ctx)


@router.post("/notifications/{notif_id}/read")
def mark_notification_read(
    notif_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Mark a single notification as read."""
    notif = session.get(Notification, notif_id)
    if notif and notif.user_id == current_user.id:
        notif.read = True
        session.add(notif)
        session.commit()
    return RedirectResponse(url="/notifications", status_code=303)


@router.post("/notifications/read-all")
def mark_all_read(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Mark all user notifications as read."""
    stmt = select(Notification).where(
        Notification.user_id == current_user.id,
        Notification.read == False
    )
    for n in session.exec(stmt).all():
        n.read = True
        session.add(n)
    session.commit()
    return RedirectResponse(url="/notifications", status_code=303)


@router.get("/privacy", response_class=HTMLResponse)
def privacy_policy_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Privacy and data protection policy aligned with India DPDP Act 2023."""
    from app.main import templates
    ctx = get_base_context(request, current_user, session)
    return templates.TemplateResponse(request=request, name="privacy.html", context=ctx)


@router.post("/privacy/delete-my-data")
def delete_user_personal_data(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Self-service data erasure for logged-in user.
    Purges timetable entries, meeting requests, and extraction logs.
    """
    # 1. Delete Timetable Entries
    tt_entries = session.exec(select(TimetableEntry).where(TimetableEntry.owner_id == current_user.id)).all()
    for entry in tt_entries:
        session.delete(entry)

    # 2. Delete Meeting Requests
    meetings = session.exec(select(MeetingRequest).where(MeetingRequest.student_id == current_user.id)).all()
    for m in meetings:
        session.delete(m)

    # 3. Delete Extraction Logs
    ext_logs = session.exec(select(ExtractionLog).where(ExtractionLog.student_id == current_user.id)).all()
    for l in ext_logs:
        session.delete(l)

    # 4. Delete Notifications
    notifs = session.exec(select(Notification).where(Notification.user_id == current_user.id)).all()
    for n in notifs:
        session.delete(n)

    record_audit(
        session=session,
        actor_id=current_user.id,
        action="USER_DATA_ERASURE",
        entity_type="User",
        entity_id=current_user.id,
        details=f"User {current_user.name} executed complete self-service data erasure."
    )
    session.commit()
    return RedirectResponse(url="/privacy?deleted=true", status_code=303)

