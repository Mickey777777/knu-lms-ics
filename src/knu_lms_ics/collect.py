import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from .canvas import CanvasClient, CanvasError
from .models import KST, Announcement, CalEvent, Course
from .settings import AppConfig

log = logging.getLogger(__name__)

PER_PAGE = 100
PLANNER_PER_PAGE = 50
CONTEXT_CODES_LIMIT = 10
DUE_EVENT_MINUTES = 30
MIN_EVENT_MINUTES = 30
COURSE_NAME_SEPARATOR = " | "
PERSONAL_CONTEXT_LABEL = "개인"
SUBMITTED_MARK = "✅ "
PLANNER_LABELS = {"assignment": "[과제]", "quiz": "[퀴즈]"}
PLANNER_IGNORED = {"announcement", "calendar_event", "planner_note"}


@dataclass
class Collected:
    courses: list[Course]
    excluded_courses: list[Course]
    events: list[CalEvent]
    announcements: list[Announcement]
    skipped_planner_types: Counter = field(default_factory=Counter)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def display_name(course_id: int, name: str, aliases: dict[str, str]) -> str:
    alias = aliases.get(str(course_id)) or aliases.get(name)
    if alias:
        return alias
    return name.split(COURSE_NAME_SEPARATOR, 1)[0].strip() or name


def _chunks(seq: list[str], size: int) -> list[list[str]]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def fetch_courses(client: CanvasClient, cfg: AppConfig) -> tuple[list[Course], list[Course]]:
    raw = client.get_all(
        "/api/v1/courses",
        [("enrollment_state", "active"), ("include[]", "term"), ("per_page", PER_PAGE)],
    )
    included: list[Course] = []
    excluded: list[Course] = []
    for c in raw:
        if not isinstance(c, dict) or "id" not in c or not c.get("name"):
            continue
        term = (c.get("term") or {}).get("name")
        course = Course(
            id=c["id"],
            name=c["name"],
            display_name=display_name(c["id"], c["name"], cfg.course_aliases),
            term_name=term,
        )
        if term and any(k in term for k in cfg.exclude_term_keywords):
            excluded.append(course)
        else:
            included.append(course)
    if not included and not excluded:
        raise CanvasError("활성 과목이 0개 (토큰 권한 또는 학기 상태 확인)")
    return included, excluded


def due_event(uid: str, summary: str, due: datetime, url: str) -> CalEvent:
    return CalEvent(
        uid=uid,
        summary=summary,
        start=due - timedelta(minutes=DUE_EVENT_MINUTES),
        end=due,
        description=f"LMS 원문: {url}",
        url=url,
    )


def planner_to_events(
    items: list[dict], courses: dict[int, Course], base_url: str, uid_domain: str
) -> tuple[list[CalEvent], dict[int, str], Counter]:
    events: list[CalEvent] = []
    announcement_updated: dict[int, str] = {}
    skipped: Counter = Counter()
    for item in items:
        ptype = item.get("plannable_type")
        plannable = item.get("plannable") or {}
        course = courses.get(item.get("course_id"))
        if ptype == "announcement":
            if plannable.get("id") is not None and plannable.get("updated_at"):
                announcement_updated[plannable["id"]] = plannable["updated_at"]
            continue
        if ptype in PLANNER_IGNORED:
            continue
        if ptype not in PLANNER_LABELS:
            skipped[ptype] += 1
            continue
        if course is None or not plannable.get("due_at"):
            continue
        submissions = item.get("submissions")
        submitted = isinstance(submissions, dict) and submissions.get("submitted") is True
        summary = f"{SUBMITTED_MARK if submitted else ''}{PLANNER_LABELS[ptype]} {course.display_name} - {plannable.get('title', '')}"
        url = urljoin(base_url + "/", item.get("html_url") or "")
        events.append(due_event(f"canvas-{ptype}-{item['plannable_id']}@{uid_domain}", summary, parse_ts(plannable["due_at"]), url))
    return events, announcement_updated, skipped


def calendar_to_event(ev: dict, label: str, uid_domain: str) -> CalEvent | None:
    if ev.get("workflow_state") == "deleted" or ev.get("hidden") or not ev.get("start_at"):
        return None
    start_at = parse_ts(ev["start_at"])
    url = ev.get("html_url")
    common = dict(
        uid=f"canvas-event-{ev['id']}@{uid_domain}",
        summary=f"[일정] {label} - {ev.get('title', '')}",
        location=ev.get("location_name") or None,
        description=f"LMS 원문: {url}" if url else None,
        url=url,
    )
    if ev.get("all_day"):
        day = date.fromisoformat(ev["all_day_date"]) if ev.get("all_day_date") else start_at.astimezone(KST).date()
        return CalEvent(start=day, end=day + timedelta(days=1), **common)
    end_at = parse_ts(ev["end_at"]) if ev.get("end_at") else start_at
    if end_at <= start_at:
        end_at = start_at + timedelta(minutes=MIN_EVENT_MINUTES)
    return CalEvent(start=start_at, end=end_at, **common)


def fetch_calendar_events(
    client: CanvasClient, user_id: int, courses: list[Course], start: datetime, end: datetime, uid_domain: str
) -> list[CalEvent]:
    labels = {f"user_{user_id}": PERSONAL_CONTEXT_LABEL} | {c.context_code: c.display_name for c in courses}
    events: list[CalEvent] = []
    for group in _chunks(list(labels), CONTEXT_CODES_LIMIT):
        params = [("type", "event"), ("start_date", iso_utc(start)), ("end_date", iso_utc(end)), ("per_page", PER_PAGE)]
        params += [("context_codes[]", code) for code in group]
        for ev in client.get_all("/api/v1/calendar_events", params):
            event = calendar_to_event(ev, labels.get(ev.get("context_code"), PERSONAL_CONTEXT_LABEL), uid_domain)
            if event is not None:
                events.append(event)
    return events


def fetch_announcements(
    client: CanvasClient, courses: list[Course], start: datetime, end: datetime, updated: dict[int, str]
) -> list[Announcement]:
    by_code = {c.context_code: c for c in courses}
    result: list[Announcement] = []
    for group in _chunks(list(by_code), CONTEXT_CODES_LIMIT):
        params = [("start_date", iso_utc(start)), ("end_date", iso_utc(end)), ("per_page", PER_PAGE)]
        params += [("context_codes[]", code) for code in group]
        for a in client.get_all("/api/v1/announcements", params):
            course = by_code.get(a.get("context_code"))
            if course is None or not a.get("posted_at"):
                continue
            result.append(
                Announcement(
                    id=a["id"],
                    course_id=course.id,
                    title=a.get("title") or "",
                    message_html=a.get("message") or "",
                    posted_at=parse_ts(a["posted_at"]),
                    updated_at=updated.get(a["id"]),
                    html_url=a.get("html_url") or "",
                )
            )
    return result


def collect_all(client: CanvasClient, base_url: str, cfg: AppConfig, now: datetime, uid_domain: str) -> Collected:
    start = now - timedelta(days=cfg.lookback_days)
    end = now + timedelta(days=cfg.lookahead_days)
    me = client.get("/api/v1/users/self")
    included, excluded = fetch_courses(client, cfg)
    by_id = {c.id: c for c in included}
    planner = client.get_all(
        "/api/v1/planner/items",
        [("start_date", iso_utc(start)), ("end_date", iso_utc(end)), ("per_page", PLANNER_PER_PAGE)],
    )
    events, updated, skipped = planner_to_events(planner, by_id, base_url, uid_domain)
    if skipped:
        log.info("처리하지 않은 플래너 항목 유형: %s", dict(skipped))
    events += fetch_calendar_events(client, me["id"], included, start, end, uid_domain)
    announcements = fetch_announcements(client, included, start, now, updated)
    return Collected(included, excluded, events, announcements, skipped)
