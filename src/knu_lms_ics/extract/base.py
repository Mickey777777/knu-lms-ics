import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic import BaseModel

from ..models import Announcement, CalEvent, Course

TAG_BREAKOUT = re.compile(r"<\s*/?\s*(announcement|metadata)\b", re.IGNORECASE)
FINGERPRINT_CHARS = 16


def neutralize_tags(text: str) -> str:
    return TAG_BREAKOUT.sub(lambda m: "[" + m.group(0).lstrip("<").strip(), text)


@dataclass
class Candidate:
    index: int
    item: BaseModel
    event: CalEvent | None
    reason: str | None


@dataclass
class AnnouncementOutcome:
    announcement: Announcement
    kind: str
    source: str
    candidates: list[Candidate] = field(default_factory=list)
    error: str | None = None


class Extractor(ABC):
    kind: str
    output_model: type[BaseModel]
    item_model: type[BaseModel]

    @abstractmethod
    def system_prompt(self) -> str: ...

    def fingerprint(self) -> str:
        schema = json.dumps(self.output_model.model_json_schema(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(f"{self.system_prompt()}\n{schema}".encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]

    @abstractmethod
    def user_message(self, ann: Announcement, course: Course, text: str) -> str: ...

    @abstractmethod
    def items(self, parsed: BaseModel) -> list[BaseModel]: ...

    @abstractmethod
    def build(self, ann: Announcement, course: Course, index: int, item: BaseModel, source_text: str) -> Candidate: ...

    def finalize(self, outcomes: list[AnnouncementOutcome]) -> list[CalEvent]:
        return [c.event for o in outcomes for c in o.candidates if c.event is not None]
