from datetime import date, datetime, timedelta, timezone

from knu_lms_ics.collect import calendar_to_event, display_name, fetch_courses, planner_to_events
from knu_lms_ics.models import Course
from knu_lms_ics.settings import load_config

DOMAIN = "example.com"

BASE = "https://canvas.example.edu"
OS = Course(101, "운영체제 | 001 분반 (Operating Systems | 001 Section)", "운영체제", "2026년 2학기")


def planner_item(ptype, pid, due, course_id=OS.id, submitted=None, title="Assignment #01"):
    subs = False if submitted is None else {"submitted": submitted, "graded": False}
    return {
        "plannable_type": ptype,
        "plannable_id": pid,
        "course_id": course_id,
        "html_url": f"/courses/{course_id}/assignments/{pid}",
        "plannable": {"id": pid, "title": title, "due_at": due, "updated_at": "2026-10-01T00:00:00Z"},
        "submissions": subs,
    }


def test_display_name_prefers_alias_then_strips_section():
    assert display_name(101, OS.name, {}) == "운영체제"
    assert display_name(101, OS.name, {"101": "OS"}) == "OS"
    assert display_name(101, OS.name, {OS.name: "오체"}) == "오체"


def test_planner_assignment_becomes_30_minute_event_ending_at_due():
    events, _, _ = planner_to_events([planner_item("assignment", 11, "2026-09-21T14:59:59Z", submitted=False)], {OS.id: OS}, BASE, DOMAIN)
    (ev,) = events
    assert ev.uid == "canvas-assignment-11@example.com"
    assert ev.summary == "[과제] 운영체제 - Assignment #01"
    assert ev.end == datetime(2026, 9, 21, 14, 59, 59, tzinfo=timezone.utc)
    assert ev.end - ev.start == timedelta(minutes=30)
    assert ev.url == f"{BASE}/courses/101/assignments/11"
    assert ev.url in ev.description


def test_planner_marks_submitted_and_labels_quiz():
    items = [
        planner_item("assignment", 1, "2026-09-21T14:59:59Z", submitted=True),
        planner_item("quiz", 2, "2026-09-22T14:59:59Z", title="퀴즈 1"),
    ]
    events, _, _ = planner_to_events(items, {OS.id: OS}, BASE, DOMAIN)
    assert [e.summary for e in events] == ["✅ [과제] 운영체제 - Assignment #01", "[퀴즈] 운영체제 - 퀴즈 1"]
    assert events[1].uid == "canvas-quiz-2@example.com"


def test_planner_skips_excluded_courses_missing_due_and_collects_announcement_times():
    items = [
        planner_item("assignment", 1, "2026-09-21T14:59:59Z", course_id=202),
        planner_item("assignment", 2, None),
        {"plannable_type": "announcement", "plannable_id": 9, "course_id": OS.id, "plannable": {"id": 9, "updated_at": "2026-10-02T00:00:00Z"}},
        planner_item("wiki_page", 3, "2026-09-21T14:59:59Z"),
    ]
    events, updated, skipped = planner_to_events(items, {OS.id: OS}, BASE, DOMAIN)
    assert events == []
    assert updated == {9: "2026-10-02T00:00:00Z"}
    assert skipped == {"wiki_page": 1}


def test_calendar_event_timed_all_day_and_hidden():
    timed = calendar_to_event({"id": 5, "title": "보강", "start_at": "2026-10-30T01:00:00Z", "end_at": "2026-10-30T01:00:00Z", "location_name": "공학관 101호", "html_url": "u"}, "운영체제", DOMAIN)
    assert timed.summary == "[일정] 운영체제 - 보강" and timed.location == "공학관 101호"
    assert timed.end - timed.start == timedelta(minutes=30)
    all_day = calendar_to_event({"id": 6, "title": "휴강", "start_at": "2026-10-29T15:00:00Z", "all_day": True, "all_day_date": "2026-10-30"}, "운영체제", DOMAIN)
    assert all_day.start == date(2026, 10, 30) and all_day.end == date(2026, 10, 31)
    assert calendar_to_event({"id": 7, "start_at": "2026-10-30T01:00:00Z", "hidden": True}, "x", DOMAIN) is None


class FakeClient:
    def __init__(self, courses):
        self.courses = courses

    def get_all(self, path, params):
        return self.courses


def test_fetch_courses_excludes_by_term_keyword(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    raw = [
        {"id": 1, "name": "운영체제 | 001 분반", "term": {"name": "2026년 2학기"}},
        {"id": 2, "name": "[비교과] 특강", "term": {"name": "비교과(2026)"}},
        {"id": 3},
    ]
    included, excluded = fetch_courses(FakeClient(raw), cfg)
    assert [c.id for c in included] == [1] and [c.id for c in excluded] == [2]
