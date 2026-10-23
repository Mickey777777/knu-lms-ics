import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic
import pydantic

from ..htmltext import html_to_text
from ..models import Announcement, CalEvent, Course
from .base import AnnouncementOutcome, Extractor

log = logging.getLogger(__name__)

MAX_TOKENS = 16000
EFFORT_UNSUPPORTED_PREFIXES = ("claude-sonnet-4-5", "claude-haiku-4-5")


class ExtractionFailure(Exception):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += usage.cache_read_input_tokens or 0
        self.cache_write_tokens += usage.cache_creation_input_tokens or 0


class ExtractionRunner:
    def __init__(
        self,
        client: anthropic.Anthropic | None,
        model: str,
        effort: str | None,
        extractors: list[Extractor],
        cache: dict,
        write_cache: bool,
    ):
        self.client = client
        self.model = model
        self.effort = effort
        self.extractors = extractors
        self.cache = cache
        self.write_cache = write_cache
        self.usage = Usage()

    def run(
        self, announcements: list[Announcement], courses: dict[int, Course]
    ) -> tuple[list[CalEvent], list[AnnouncementOutcome], list[str]]:
        events: list[CalEvent] = []
        outcomes: list[AnnouncementOutcome] = []
        errors: list[str] = []
        for ex in self.extractors:
            if self.write_cache:
                bucket = self.cache.setdefault(ex.kind, {})
            else:
                bucket = {k: dict(v) for k, v in self.cache.get(ex.kind, {}).items()}
            seen: set[str] = set()
            kind_outcomes: list[AnnouncementOutcome] = []
            fingerprint = ex.fingerprint()
            for ann in announcements:
                course = courses.get(ann.course_id)
                if course is None:
                    continue
                seen.add(str(ann.id))
                kind_outcomes.append(self._process(ex, fingerprint, ann, course, bucket, errors))
            for key in [k for k in bucket if k not in seen]:
                del bucket[key]
            events += ex.finalize(kind_outcomes)
            outcomes += kind_outcomes
        return events, outcomes, errors

    @staticmethod
    def _cached_items(ex: Extractor, entry: dict | None) -> list[pydantic.BaseModel] | None:
        if not entry:
            return None
        try:
            return [ex.item_model.model_validate(raw) for raw in entry.get("items", [])]
        except pydantic.ValidationError:
            return None

    def _process(
        self, ex: Extractor, fingerprint: str, ann: Announcement, course: Course, bucket: dict, errors: list[str]
    ) -> AnnouncementOutcome:
        text = html_to_text(ann.message_html)
        source_text = f"{ann.title}\n{text}"
        digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        key = str(ann.id)
        entry = bucket.get(key)
        cached = self._cached_items(ex, entry)
        error = None
        fresh = cached is not None and (entry.get("model"), entry.get("prompt"), entry.get("hash")) == (self.model, fingerprint, digest)
        if fresh:
            source, items = "cache", cached
            entry["updated_at"] = ann.updated_at
        elif self.client is None:
            source, items = "disabled", cached or []
        else:
            try:
                items = ex.items(self._call(ex, ann, course, text))
            except ExtractionFailure as e:
                error = str(e)
                errors.append(f"{ex.kind} 추출 실패 (공지 {ann.id}): {error}")
                log.warning("%s 추출 실패 (공지 %s): %s", ex.kind, ann.id, error)
                source, items = "failed", cached or []
            else:
                source = "api"
                bucket[key] = {
                    "model": self.model,
                    "prompt": fingerprint,
                    "hash": digest,
                    "updated_at": ann.updated_at,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "items": [i.model_dump(mode="json") for i in items],
                }
        candidates = [ex.build(ann, course, i, item, source_text) for i, item in enumerate(items, 1)]
        return AnnouncementOutcome(ann, ex.kind, source, candidates, error)

    def _call(self, ex: Extractor, ann: Announcement, course: Course, text: str) -> pydantic.BaseModel:
        kwargs = {}
        if self.effort and not self.model.startswith(EFFORT_UNSUPPORTED_PREFIXES):
            kwargs["output_config"] = {"effort": self.effort}
        try:
            resp = self.client.messages.parse(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": ex.system_prompt(), "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": ex.user_message(ann, course, text)}],
                output_format=ex.output_model,
                **kwargs,
            )
        except pydantic.ValidationError as e:
            raise ExtractionFailure(f"응답 스키마 검증 실패 ({e.error_count()}건)") from e
        except anthropic.APIStatusError as e:
            raise ExtractionFailure(f"API 오류 HTTP {e.status_code} ({type(e).__name__})") from e
        except anthropic.APIConnectionError as e:
            raise ExtractionFailure(f"API 연결 실패 ({type(e).__name__})") from e
        self.usage.add(resp.usage)
        log.info(
            "Claude 호출 (공지 %s): 입력 %s, 출력 %s, 캐시 읽기 %s",
            ann.id, resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cache_read_input_tokens,
        )
        if resp.stop_reason != "end_turn":
            raise ExtractionFailure(f"stop_reason={resp.stop_reason}")
        parsed = resp.parsed_output
        if parsed is None:
            raise ExtractionFailure("구조화 출력이 비어 있음")
        return parsed
