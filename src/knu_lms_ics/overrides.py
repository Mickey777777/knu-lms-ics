import datetime as dt
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import KST, CalEvent

log = logging.getLogger(__name__)

DEFAULT_MANUAL_MINUTES = 60
DEFAULT_PATCH_MINUTES = 30


class OverridesError(Exception):
    pass


def _aware(value: datetime | date) -> datetime | date:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value


class ManualEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    title: str
    date: dt.date | None = None
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _when(self):
        if (self.date is None) == (self.start is None):
            raise ValueError("date(종일) 또는 start(시각) 중 하나만 지정")
        if self.date is not None and self.end is not None:
            raise ValueError("종일 일정에는 end를 쓰지 않음")
        return self

    def to_event(self, uid_domain: str) -> CalEvent:
        uid = f"manual-{self.id}@{uid_domain}"
        if self.date is not None:
            start, end = self.date, self.date + timedelta(days=1)
        else:
            start = _aware(self.start)
            end = _aware(self.end) if self.end else start + timedelta(minutes=DEFAULT_MANUAL_MINUTES)
        if not end > start:
            raise OverridesError(f"add {self.id}: end가 start보다 빠름")
        return CalEvent(uid=uid, summary=self.title, start=start, end=end, location=self.location, description=self.description)


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    start: datetime | date | None = None
    end: datetime | date | None = None
    location: str | None = None


class Overrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add: list[ManualEvent] = []
    hide: list[str] = []
    patch: dict[str, Patch] = {}


def load_overrides(path: Path) -> Overrides:
    if not path.is_file():
        return Overrides()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Overrides.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as e:
        raise OverridesError(f"overrides 파싱 실패: {e}") from e


def _patched_times(ev: CalEvent, p: Patch) -> tuple[datetime | date, datetime | date]:
    start = _aware(p.start) if p.start is not None else ev.start
    if p.end is not None:
        return start, _aware(p.end)
    if p.start is None:
        return start, ev.end
    if isinstance(start, datetime) == isinstance(ev.start, datetime):
        return start, start + (ev.end - ev.start)
    if isinstance(start, datetime):
        return start, start + timedelta(minutes=DEFAULT_PATCH_MINUTES)
    return start, start + timedelta(days=1)


def apply_overrides(events: list[CalEvent], ov: Overrides, uid_domain: str) -> tuple[list[CalEvent], list[str]]:
    warnings: list[str] = []
    by_uid = {e.uid: e for e in events}
    for manual in ov.add:
        ev = manual.to_event(uid_domain)
        if ev.uid in by_uid:
            warnings.append(f"add: {ev.uid} 이미 있음, 무시")
            continue
        by_uid[ev.uid] = ev
    for uid, p in ov.patch.items():
        ev = by_uid.get(uid)
        if ev is None:
            warnings.append(f"patch: 없는 UID {uid}, 무시")
            continue
        start, end = _patched_times(ev, p)
        if isinstance(start, datetime) != isinstance(end, datetime) or not end > start:
            warnings.append(f"patch: {uid} 시각이 잘못됨 (종일/시각 혼용 또는 end <= start), 무시")
            continue
        by_uid[uid] = replace(
            ev,
            summary=p.title if p.title is not None else ev.summary,
            start=start,
            end=end,
            location=p.location if p.location is not None else ev.location,
        )
    for uid in ov.hide:
        if by_uid.pop(uid, None) is None:
            warnings.append(f"hide: 없는 UID {uid}, 무시")
    for w in warnings:
        log.warning(w)
    return list(by_uid.values()), warnings
