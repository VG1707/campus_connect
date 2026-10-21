"""
Comprehensive automated test suite for the three new features in CampusConnect:
1. Language-Based Faculty Matching
2. Prerequisite Topic Suggestions
3. Common Doubt Topics Analytics

Verifies all edge cases, counting rules, status exclusions, deterministic matching,
and web endpoints.
"""

from datetime import datetime, date, timedelta
import json
import pytest
from sqlmodel import Session, select
from fastapi.testclient import TestClient

from app.models import (
    User, UserRole, Course, FacultyCourse, OfficeHour, MeetingRequest,
    RequestState, RequestSource, TopicPrerequisite
)
from app.matching import find_matching_slots, recommend_faculty_for_student
from app.routers.faculty import compute_common_doubt_topics
from app.auth_mock import COOKIE_NAME


# =========================================================================
# FEATURE 1: Language-Based Faculty Matching Tests
# =========================================================================

def test_user_language_defaults_and_helpers(session: Session):
    """Verify User language property getters, normalization, and defaults."""
    u = User(
        name="Test Language User",
        role=UserRole.STUDENT.value,
        department="Computer Science & Engineering",
        email="test.lang@campusconnect.edu"
    )
    # Default is English
    assert u.languages == ["English"]

    # Normalize list with whitespace and duplicates
    u.set_languages([" English ", "Tamil", "tamil", "Hindi "])
    assert u.languages == ["English", "Tamil", "Hindi"]

    # Empty list falls back to English
    u.set_languages([])
    assert u.languages == ["English"]


def test_language_matching_boost_and_non_exclusion(session: Session):
    """
    Test that language overlap adds transparent bonus points and reasoning,
    while faculty without shared languages are NEVER hidden.
    """
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    raman.set_languages(["English", "Tamil"])
    session.add(raman)

    # Student 1 shares Tamil and English with Dr. Raman
    student_tamil = User(
        name="Tamil Student",
        role=UserRole.STUDENT.value,
        department="Computer Science & Engineering",
        email="tamil.student@campusconnect.edu",
        languages_json=json.dumps(["Tamil", "English"])
    )
    session.add(student_tamil)

    # Student 2 only prefers Malayalam & Hindi (no language overlap with Dr. Raman)
    student_no_overlap = User(
        name="No Overlap Student",
        role=UserRole.STUDENT.value,
        department="Computer Science & Engineering",
        email="no.overlap@campusconnect.edu",
        languages_json=json.dumps(["Malayalam", "Hindi"])
    )
    session.add(student_no_overlap)
    session.commit()

    window_start = datetime(2026, 10, 19, 9, 0, 0)
    window_end = datetime(2026, 10, 24, 18, 0, 0)
    sim_time = datetime(2026, 10, 19, 8, 0, 0)

    # 1. Matching slots for Tamil Student
    slots_tamil = find_matching_slots(
        session=session,
        student_id=student_tamil.id,
        faculty_id=raman.id,
        duration_minutes=15,
        window_start=window_start,
        window_end=window_end,
        current_time=sim_time
    )
    assert len(slots_tamil) > 0
    top_tamil_slot = slots_tamil[0]
    assert "English" in top_tamil_slot.shared_languages or "Tamil" in top_tamil_slot.shared_languages
    assert "Language compatibility" in top_tamil_slot.ranking_reason

    # 2. Matching slots for No-Overlap Student
    slots_no_overlap = find_matching_slots(
        session=session,
        student_id=student_no_overlap.id,
        faculty_id=raman.id,
        duration_minutes=15,
        window_start=window_start,
        window_end=window_end,
        current_time=sim_time
    )
    # MUST NOT BE EMPTY -> Faculty is NOT hidden!
    assert len(slots_no_overlap) > 0
    top_no_overlap_slot = slots_no_overlap[0]
    assert len(top_no_overlap_slot.shared_languages) == 0
    assert "no language overlap" in top_no_overlap_slot.ranking_reason.lower()

    # Score comparison: with same calendar window, shared language slot scores higher by +15 pts
    assert top_tamil_slot.score > top_no_overlap_slot.score


def test_recommend_faculty_prioritizes_shared_languages(session: Session):
    """
    Test recommend_faculty_for_student prioritises faculty sharing languages
    with the student, while retaining all faculty in results with transparent explanations.
    """
    # Student prefers Bengali only to test clear separation between matching & non-matching faculty
    student_bengali = User(
        name="Bengali Preference Student",
        role=UserRole.STUDENT.value,
        department="Computer Science & Engineering",
        email="bengali.pref@campusconnect.edu",
        languages_json=json.dumps(["Bengali"])
    )
    session.add(student_bengali)
    session.commit()

    recs = recommend_faculty_for_student(session, student_id=student_bengali.id)
    assert len(recs) == 12  # All 12 faculty are retained, none hidden

    # Faculty with language overlap (Dr. S. Mukherjee speaks Bengali)
    matching_fac = [r for r in recs if r["has_language_match"]]
    non_matching_fac = [r for r in recs if not r["has_language_match"]]

    assert len(matching_fac) == 1
    assert matching_fac[0]["faculty"].name == "Dr. S. Mukherjee"
    assert "Shared language: Bengali" in matching_fac[0]["recommendation_reason"]

    assert len(non_matching_fac) == 11
    # Non-matching faculty clearly state no language overlap, yet remain accessible
    assert any("no language overlap" in r["recommendation_reason"] for r in non_matching_fac)


def test_student_and_faculty_language_update_routes(client: TestClient, session: Session):
    """Verify students and faculty can update their consultation language preferences via POST."""
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()

    # 1. Update Student Languages
    client.cookies.set(COOKIE_NAME, str(meera.id))
    resp_student = client.post(
        "/student/profile/languages",
        data={"languages": "English, French, German"},
        follow_redirects=False
    )
    assert resp_student.status_code == 303
    session.refresh(meera)
    assert meera.languages == ["English", "French", "German"]

    # 2. Update Faculty Languages
    client.cookies.set(COOKIE_NAME, str(raman.id))
    resp_faculty = client.post(
        "/faculty/profile/languages",
        data={"languages": "English, Tamil, Telugu, Hindi"},
        follow_redirects=False
    )
    assert resp_faculty.status_code == 303
    session.refresh(raman)
    assert "Telugu" in raman.languages
    assert "Hindi" in raman.languages


def test_faculty_directory_language_filtering(client: TestClient, session: Session):
    """Verify language filtering in the Faculty Directory."""
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(meera.id))

    # Filter by Tamil
    resp_tamil = client.get("/faculty-directory?language=Tamil")
    assert resp_tamil.status_code == 200
    tamil_fac_names = [f["faculty"].name for f in resp_tamil.context["faculty_list"]]
    assert "Dr. Rajesh Raman" in tamil_fac_names

    # Filter by Bengali
    resp_bengali = client.get("/faculty-directory?language=Bengali")
    assert resp_bengali.status_code == 200
    bengali_fac_names = [f["faculty"].name for f in resp_bengali.context["faculty_list"]]
    assert "Dr. S. Mukherjee" in bengali_fac_names
    # Dr. Raman does not speak Bengali, should be filtered out from directory results
    assert "Dr. Rajesh Raman" not in bengali_fac_names
    assert ">Dr. S. Mukherjee</h2>" in resp_bengali.text
    assert ">Dr. Rajesh Raman</h2>" not in resp_bengali.text

    # All languages
    resp_all = client.get("/faculty-directory?language=All")
    assert resp_all.status_code == 200
    all_fac_names = [f["faculty"].name for f in resp_all.context["faculty_list"]]
    assert "Dr. Rajesh Raman" in all_fac_names
    assert "Dr. S. Mukherjee" in all_fac_names


# =========================================================================
# FEATURE 2: Prerequisite Topic Suggestions Tests
# =========================================================================

def test_prerequisites_suggest_known_topic(client: TestClient, session: Session):
    """Test suggestions returned for a known topic mapping."""
    cs201 = session.exec(select(Course).where(Course.code == "CS201")).one()

    resp = client.get(f"/student/prerequisites-suggest?course_id={cs201.id}&topic=Binary Trees")
    assert resp.status_code == 200
    assert "Suggested Foundational Concepts (Optional)" in resp.text
    assert "Pointers and Dynamic Memory Allocation in C" in resp.text
    assert "Recursion &amp; Call Stack Basics" in resp.text or "Recursion & Call Stack Basics" in resp.text
    assert "Non-blocking" in resp.text


def test_prerequisites_suggest_case_insensitive_and_partial_match(client: TestClient, session: Session):
    """Test that student doubt variations (e.g. 'binary tree traversal') trigger mappings."""
    resp = client.get("/student/prerequisites-suggest?topic=binary tree traversal")
    assert resp.status_code == 200
    assert "Pointers and Dynamic Memory Allocation in C" in resp.text


def test_prerequisites_suggest_no_mapping_shows_clear_message(client: TestClient, session: Session):
    """
    Test that entering a topic without mappings shows a clear informational message
    without inventing or hallucinating suggestions.
    """
    resp = client.get("/student/prerequisites-suggest?topic=Quantum Cryptography Teleportation")
    assert resp.status_code == 200
    assert "No prerequisite concepts recorded for" in resp.text
    assert "You can proceed directly to booking." in resp.text


def test_prerequisites_empty_topic_returns_empty_response(client: TestClient):
    """Test that an empty topic query produces an empty response without errors."""
    resp = client.get("/student/prerequisites-suggest?topic=")
    assert resp.status_code == 200
    assert resp.text.strip() == ""


def test_booking_proceeds_directly_without_prerequisite_review(client: TestClient, session: Session):
    """
    Test requirement: 'Let students proceed directly to booking without reviewing the suggestions.'
    Student can submit consultation request for any topic without viewing or clicking prerequisites.
    """
    meera = session.exec(select(User).where(User.email == "meera.nair@campusconnect.edu")).one()
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    cs201 = session.exec(select(Course).where(Course.code == "CS201")).one()

    client.cookies.set(COOKIE_NAME, str(meera.id))
    booking_data = {
        "faculty_id": raman.id,
        "course_id": cs201.id,
        "topic_tag": "Custom Unlisted Topic Without Prerequisites",
        "type": "doubt",
        "description": "Direct booking without checking suggestions",
        "slot_start": "2026-10-23T14:00:00",
        "slot_end": "2026-10-23T14:15:00",
        "duration": 15,
        "source": "office_hour",
        "location": "Cabin 304, Academic Block A"
    }
    resp = client.post("/student/book", data=booking_data, follow_redirects=False)
    assert resp.status_code == 303
    assert "/student/meetings" in resp.headers.get("location", "")

    # Verify meeting request was saved in database with the custom unlisted topic
    saved = session.exec(
        select(MeetingRequest).where(
            MeetingRequest.student_id == meera.id,
            MeetingRequest.topic_tag == "Custom Unlisted Topic Without Prerequisites"
        )
    ).first()
    assert saved is not None
    assert saved.state in (RequestState.CONFIRMED.value, RequestState.PENDING.value)


def test_topic_prerequisite_db_extendability(client: TestClient, session: Session):
    """Test that topic mappings can be easily extended in database for new subjects."""
    new_prereq = TopicPrerequisite(
        subject_code="CS307",
        topic="A* Search",
        prerequisite_topic="Heuristic Functions & Dijkstra Algorithm",
        explanation="A* evaluates paths by summing known distance g(n) with admissible heuristic estimate h(n)."
    )
    session.add(new_prereq)
    session.commit()

    resp = client.get("/student/prerequisites-suggest?topic=A* Search")
    assert resp.status_code == 200
    assert "Heuristic Functions &amp; Dijkstra Algorithm" in resp.text or "Heuristic Functions & Dijkstra Algorithm" in resp.text


# =========================================================================
# FEATURE 3: Common Doubt Topics Analytics Tests
# =========================================================================

def test_common_doubt_topics_ranking_and_counts(session: Session):
    """
    Test that consultation requests are counted and ranked from most requested
    to least requested, distinguishing total requests from unique students.
    """
    topics = compute_common_doubt_topics(session=session)
    assert len(topics) > 0

    # Verify topics are ranked in descending order by request count (with faculty prioritization)
    for i in range(len(topics) - 1):
        if topics[i]["is_my_subject"] == topics[i+1]["is_my_subject"]:
            assert topics[i]["total_requests"] >= topics[i+1]["total_requests"]

    # CS201 Binary Trees has multiple requests from multiple students
    bt = next((t for t in topics if t["topic"] == "Binary Trees"), None)
    assert bt is not None
    assert bt["total_requests"] >= 3
    assert bt["unique_students"] >= 2
    # Verify unique students is distinguished from total requests
    assert isinstance(bt["unique_students"], int)
    assert isinstance(bt["total_requests"], int)


def test_counting_rules_exclude_cancelled_and_rejected(session: Session):
    """
    Test requirement: 'Exclude cancelled and rejected requests from the primary rankings.
    Explicitly define how pending, confirmed, completed, and no-show requests are treated.'
    """
    # Count requests in DB that are cancelled or rejected
    stmt_excluded = select(MeetingRequest).where(
        MeetingRequest.state.in_([RequestState.CANCELLED.value, RequestState.REJECTED.value])
    )
    excluded_requests = session.exec(stmt_excluded).all()
    assert len(excluded_requests) >= 2  # Seeded 1 cancelled and 1 rejected

    # Run doubt topic ranking
    topics = compute_common_doubt_topics(session=session)

    # Sum all counted requests across all topics
    total_counted = sum(t["total_requests"] for t in topics)

    # Total requests in DB
    all_requests = session.exec(select(MeetingRequest)).all()
    assert total_counted == len(all_requests) - len(excluded_requests)

    # Status breakdown should only include pending, confirmed, completed, no_show
    for t in topics:
        breakdown = t["status_breakdown"]
        assert breakdown["pending"] + breakdown["confirmed"] + breakdown["completed"] + breakdown["no_show"] == t["total_requests"]


def test_common_doubt_topics_department_and_course_filters(session: Session):
    """Test filtering by department and course."""
    # Filter by Mechanical Engineering
    mech_topics = compute_common_doubt_topics(session=session, department="Mechanical Engineering")
    assert len(mech_topics) > 0
    for t in mech_topics:
        assert t["department"] == "Mechanical Engineering"
    assert any(t["topic"] == "Rankine Cycle" for t in mech_topics)

    # Filter by CS201
    cs201 = session.exec(select(Course).where(Course.code == "CS201")).one()
    cs201_topics = compute_common_doubt_topics(session=session, course_id=str(cs201.id))
    assert len(cs201_topics) > 0
    for t in cs201_topics:
        assert t["course_id"] == cs201.id


def test_common_doubt_topics_date_range_filter(session: Session):
    """Test filtering by date range."""
    # Dates from 2026-10-18 to 2026-10-21
    topics_window = compute_common_doubt_topics(
        session=session,
        start_date="2026-10-18",
        end_date="2026-10-21"
    )
    assert len(topics_window) > 0

    # Narrow date window in future with 0 requests
    topics_empty = compute_common_doubt_topics(
        session=session,
        start_date="2027-01-01",
        end_date="2027-01-05"
    )
    assert len(topics_empty) == 0


def test_missing_subject_or_topic_handling(session: Session):
    """Test that missing topic or course is handled gracefully without guessing."""
    u_student = session.exec(select(User).where(User.role == UserRole.STUDENT.value)).first()
    u_faculty = session.exec(select(User).where(User.role == UserRole.FACULTY.value)).first()

    # Create request with empty topic and no course
    empty_req = MeetingRequest(
        student_id=u_student.id,
        faculty_id=u_faculty.id,
        course_id=None,
        topic_tag="",
        type="other",
        description="General academic question",
        preferred_window_start="2026-10-20T10:00:00",
        preferred_window_end="2026-10-20T11:00:00",
        slot_start="2026-10-20T10:00:00",
        slot_end="2026-10-20T10:15:00",
        duration=15,
        state=RequestState.CONFIRMED.value,
        source=RequestSource.OFFICE_HOUR.value,
        location="Office",
        created_at="2026-10-20T09:00:00",
        updated_at="2026-10-20T09:00:00"
    )
    session.add(empty_req)
    session.commit()

    topics = compute_common_doubt_topics(session=session)
    unspecified = next((t for t in topics if "Unspecified Topic" in t["topic"]), None)
    assert unspecified is not None
    assert unspecified["course_name"] == "General Academic Inquiries"
    assert unspecified["total_requests"] >= 1


def test_faculty_inbox_renders_common_doubt_topics_and_partial(client: TestClient, session: Session):
    """Test faculty inbox dashboard and HTMX partial endpoint for Common Doubt Topics."""
    raman = session.exec(select(User).where(User.email == "rajesh.raman@campusconnect.edu")).one()
    client.cookies.set(COOKIE_NAME, str(raman.id))

    # 1. Full inbox page contains Common Doubt Topics Analytics
    resp = client.get("/faculty/inbox")
    assert resp.status_code == 200
    assert "Common Doubt Topics Analytics" in resp.text
    assert "Binary Trees" in resp.text
    assert "Total Inquiries" in resp.text

    # 2. HTMX partial endpoint works with filters
    resp_partial = client.get("/faculty/common-doubts-partial", params={"department": "Computer Science & Engineering"})
    assert resp_partial.status_code == 200
    assert "Binary Trees" in resp_partial.text
    assert "Your Subject" in resp_partial.text
