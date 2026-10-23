import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from icalendar import Calendar, Event

from .models import CalEvent

CALNAME = "KNU LMS"
PRODID = "-//knu-lms-ics//KO"
FEED_FILE_MODE = 0o644


class FeedError(Exception):
    pass


def _utc(value):
    return value.astimezone(timezone.utc) if isinstance(value, datetime) else value


def build_calendar(events: list[CalEvent], now: datetime) -> bytes:
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", CALNAME)
    cal.add("x-wr-timezone", "Asia/Seoul")
    stamp = now.astimezone(timezone.utc)
    for ev in sorted(events, key=lambda e: (e.sort_key(), e.uid)):
        item = Event()
        item.add("uid", ev.uid)
        item.add("dtstamp", stamp)
        item.add("dtstart", _utc(ev.start))
        item.add("dtend", _utc(ev.end))
        item.add("summary", ev.summary)
        if ev.location:
            item.add("location", ev.location)
        if ev.description:
            item.add("description", ev.description)
        if ev.url:
            item.add("url", ev.url)
        cal.add_component(item)
    return cal.to_ical()


def validate(data: bytes, events: list[CalEvent]) -> None:
    try:
        cal = Calendar.from_ical(data)
    except ValueError as e:
        raise FeedError(f"생성한 ICS 재파싱 실패: {e}") from e
    parsed = list(cal.walk("VEVENT"))
    uids = [str(v.get("uid")) for v in parsed]
    if len(parsed) != len(events):
        raise FeedError(f"이벤트 수 불일치: 기대 {len(events)}, 파싱 {len(parsed)}")
    if len(set(uids)) != len(uids):
        raise FeedError("UID 중복")
    if set(uids) != {e.uid for e in events}:
        raise FeedError("UID 집합 불일치")
    for v in parsed:
        start, end = v.decoded("dtstart"), v.decoded("dtend")
        if not (end > start):
            raise FeedError(f"{v.get('uid')}: DTEND가 DTSTART보다 빠름")
    if str(cal.get("x-wr-calname")) != CALNAME:
        raise FeedError("X-WR-CALNAME 누락")


def write_atomic(out_dir: Path, filename: str, data: bytes) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".feed-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, FEED_FILE_MODE)
        os.replace(tmp, out_dir / filename)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def remove_stale(out_dir: Path, keep: str) -> int:
    removed = 0
    for path in out_dir.glob("*.ics"):
        if path.name != keep:
            path.unlink()
            removed += 1
    return removed
