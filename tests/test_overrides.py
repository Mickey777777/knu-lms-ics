from datetime import date, datetime, timedelta

import pytest

from knu_lms_ics.models import KST, CalEvent
from knu_lms_ics.overrides import OverridesError, apply_overrides, load_overrides

DOMAIN = "example.com"

A = CalEvent("canvas-assignment-1@example.com", "[과제] 운영체제 - HW1", datetime(2026, 10, 1, 23, 29, 59, tzinfo=KST), datetime(2026, 10, 1, 23, 59, 59, tzinfo=KST))
E = CalEvent("exam-9-1@example.com", "[확인필요] [시험] 운영체제 - 중간고사", date(2026, 10, 28), date(2026, 10, 29))


def load(tmp_path, text):
    path = tmp_path / "overrides.yaml"
    path.write_text(text, encoding="utf-8")
    return load_overrides(path)


def test_missing_file_is_empty(tmp_path):
    ov = load_overrides(tmp_path / "none.yaml")
    assert ov.add == [] and ov.hide == [] and ov.patch == {}


def test_add_hide_patch_and_unknown_uid_warnings(tmp_path):
    ov = load(tmp_path, """
add:
  - id: study
    title: 스터디 모임
    date: 2026-11-02
  - id: room
    title: 면담
    start: 2026-11-03T14:00
hide:
  - canvas-assignment-1@example.com
  - canvas-assignment-404@example.com
patch:
  exam-9-1@example.com:
    title: "[시험] 운영체제 - 중간고사"
    start: 2026-10-28T15:00
    end: 2026-10-28T16:15
    location: 공학관 101호
  nope@example.com:
    title: x
""")
    events, warnings = apply_overrides([A, E], ov, DOMAIN)
    by_uid = {e.uid: e for e in events}
    assert set(by_uid) == {"exam-9-1@example.com", "manual-study@example.com", "manual-room@example.com"}
    exam = by_uid["exam-9-1@example.com"]
    assert exam.summary == "[시험] 운영체제 - 중간고사" and exam.location == "공학관 101호"
    assert exam.start == datetime(2026, 10, 28, 15, 0, tzinfo=KST) and exam.end == datetime(2026, 10, 28, 16, 15, tzinfo=KST)
    assert by_uid["manual-study@example.com"].start == date(2026, 11, 2)
    assert by_uid["manual-room@example.com"].end - by_uid["manual-room@example.com"].start == timedelta(hours=1)
    assert len(warnings) == 2 and all("무시" in w for w in warnings)


def test_patch_start_only_keeps_duration(tmp_path):
    ov = load(tmp_path, "patch:\n  canvas-assignment-1@example.com:\n    start: 2026-10-02T10:00\n")
    (ev,), _ = apply_overrides([A], ov, DOMAIN)
    assert ev.start == datetime(2026, 10, 2, 10, 0, tzinfo=KST) and ev.end - ev.start == timedelta(minutes=30)


def test_patch_with_end_before_start_is_ignored(tmp_path):
    ov = load(tmp_path, "patch:\n  canvas-assignment-1@example.com:\n    end: 2026-09-01T10:00\n")
    (ev,), warnings = apply_overrides([A], ov, DOMAIN)
    assert ev == A and len(warnings) == 1


def test_invalid_file_raises(tmp_path):
    with pytest.raises(OverridesError):
        load(tmp_path, "add:\n  - id: 'bad id'\n    title: x\n    date: 2026-11-02\n")
    with pytest.raises(OverridesError):
        load(tmp_path, "unknown: 1\n")
