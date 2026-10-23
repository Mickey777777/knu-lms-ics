import stat
from datetime import date, datetime, timezone

import pytest
from icalendar import Calendar

from knu_lms_ics.feed import FeedError, build_calendar, remove_stale, validate, write_atomic
from knu_lms_ics.models import KST, CalEvent

NOW = datetime(2026, 10, 23, 4, 0, tzinfo=timezone.utc)
EVENTS = [
    CalEvent("canvas-assignment-1@example.com", "✅ [과제] 운영체제 - HW1", datetime(2026, 10, 1, 23, 29, tzinfo=KST), datetime(2026, 10, 1, 23, 59, tzinfo=KST), description="LMS 원문: https://x", url="https://x"),
    CalEvent("exam-9-1@example.com", "[확인필요] [시험] 운영체제 - 중간고사", date(2026, 10, 28), date(2026, 10, 29), location="공학관 101호"),
]


def test_build_calendar_uses_utc_and_all_day_dates():
    data = build_calendar(EVENTS, NOW)
    validate(data, EVENTS)
    text = data.decode()
    assert "X-WR-CALNAME:KNU LMS" in text
    assert "DTSTART:20261001T142900Z" in text
    assert "DTSTART;VALUE=DATE:20261028" in text
    cal = Calendar.from_ical(data)
    assert [str(e["uid"]) for e in cal.walk("VEVENT")] == ["canvas-assignment-1@example.com", "exam-9-1@example.com"]


def test_validate_rejects_count_mismatch():
    data = build_calendar(EVENTS[:1], NOW)
    with pytest.raises(FeedError):
        validate(data, EVENTS)


def test_write_atomic_and_remove_stale(tmp_path):
    (tmp_path / "oldtoken.ics").write_text("old")
    write_atomic(tmp_path, "newtoken.ics", b"BEGIN:VCALENDAR")
    assert (tmp_path / "newtoken.ics").read_bytes() == b"BEGIN:VCALENDAR"
    assert stat.S_IMODE((tmp_path / "newtoken.ics").stat().st_mode) == 0o644
    assert remove_stale(tmp_path, "newtoken.ics") == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["newtoken.ics"]
