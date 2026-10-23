from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@dataclass
class CalEvent:
    uid: str
    summary: str
    start: datetime | date
    end: datetime | date
    location: str | None = None
    description: str | None = None
    url: str | None = None

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)

    def sort_key(self) -> datetime:
        if isinstance(self.start, datetime):
            return self.start
        return datetime.combine(self.start, time.min, KST)


@dataclass(frozen=True)
class Course:
    id: int
    name: str
    display_name: str
    term_name: str | None

    @property
    def context_code(self) -> str:
        return f"course_{self.id}"


@dataclass(frozen=True)
class Announcement:
    id: int
    course_id: int
    title: str
    message_html: str
    posted_at: datetime
    updated_at: str | None
    html_url: str
