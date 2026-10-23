from datetime import date, datetime, timezone
from types import SimpleNamespace

import anthropic
import httpx

from knu_lms_ics.extract.exam import ExamExtraction, ExamExtractor, ExamItem
from knu_lms_ics.extract.runner import ExtractionRunner
from knu_lms_ics.models import Announcement, Course

DOMAIN = "example.com"

OS = Course(101, "운영체제 | 001 분반", "운영체제", "2026년 2학기")
BODY = "<p>중간고사는 10월 28일 15:00에 봅니다.</p>"
ANN = Announcement(1, OS.id, "중간고사 안내", BODY, datetime(2026, 10, 17, tzinfo=timezone.utc), "2026-10-17T00:00:00Z", "https://lms/a/1")
EXAM = ExamItem(course="운영체제", exam_name="중간고사", date=date(2026, 10, 28), start_time="15:00", end_time=None, location=None, evidence="중간고사는 10월 28일 15:00에 봅니다.", confidence="high")
USAGE = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=50)


class FakeMessages:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(usage=USAGE, stop_reason="end_turn", parsed_output=out)


def runner(outputs, cache, write_cache=True, model="claude-sonnet-5-5", effort="low"):
    client = SimpleNamespace(messages=FakeMessages(outputs))
    return ExtractionRunner(client, model, effort, [ExamExtractor(60, ("low",), DOMAIN)], cache, write_cache), client.messages


def test_first_run_calls_api_and_caches_then_second_run_uses_cache():
    cache = {}
    r, msgs = runner([ExamExtraction(exams=[EXAM])], cache)
    events, outcomes, errors = r.run([ANN], {OS.id: OS})
    assert [e.uid for e in events] == ["exam-1-1@example.com"] and errors == []
    assert outcomes[0].source == "api" and r.usage.calls == 1
    call = msgs.calls[0]
    assert call["output_config"] == {"effort": "low"} and call["output_format"] is ExamExtraction
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "<announcement>" in call["messages"][0]["content"]

    r2, msgs2 = runner([], cache)
    events2, outcomes2, _ = r2.run([ANN], {OS.id: OS})
    assert outcomes2[0].source == "cache" and msgs2.calls == [] and len(events2) == 1


def test_changed_body_or_model_triggers_new_call():
    cache = {}
    runner([ExamExtraction(exams=[EXAM])], cache)[0].run([ANN], {OS.id: OS})
    edited = Announcement(**(ANN.__dict__ | {"message_html": BODY + "<p>장소: 공학관 101호</p>", "updated_at": "2026-10-18T00:00:00Z"}))
    r, msgs = runner([ExamExtraction(exams=[])], cache)
    r.run([edited], {OS.id: OS})
    assert len(msgs.calls) == 1 and cache["exam"]["1"]["updated_at"] == "2026-10-18T00:00:00Z"
    r3, msgs3 = runner([ExamExtraction(exams=[])], cache, model="claude-haiku-5-5")
    r3.run([edited], {OS.id: OS})
    assert len(msgs3.calls) == 1


def test_failure_keeps_previous_items_and_leaves_cache_for_retry():
    cache = {}
    runner([ExamExtraction(exams=[EXAM])], cache)[0].run([ANN], {OS.id: OS})
    before = dict(cache["exam"]["1"])
    edited = Announcement(**(ANN.__dict__ | {"title": "중간고사 안내 (수정)"}))
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    r, _ = runner([err], cache)
    events, outcomes, errors = r.run([edited], {OS.id: OS})
    assert outcomes[0].source == "failed" and len(errors) == 1 and "API 연결 실패" in errors[0]
    assert len(events) == 1
    assert cache["exam"]["1"] == before


def test_dry_run_does_not_touch_cache_and_prunes_only_when_writing():
    cache = {"exam": {"999": {"model": "claude-sonnet-5-5", "hash": "x", "items": []}}}
    r, _ = runner([ExamExtraction(exams=[EXAM])], cache, write_cache=False)
    r.run([ANN], {OS.id: OS})
    assert list(cache["exam"]) == ["999"]
    r2, _ = runner([ExamExtraction(exams=[EXAM])], cache)
    r2.run([ANN], {OS.id: OS})
    assert list(cache["exam"]) == ["1"]


def test_effort_omitted_for_models_without_effort_support():
    r, msgs = runner([ExamExtraction(exams=[])], {}, model="claude-haiku-4-5")
    r.run([ANN], {OS.id: OS})
    assert "output_config" not in msgs.calls[0]


def test_no_client_uses_cache_only():
    r = ExtractionRunner(None, "claude-sonnet-5-5", "low", [ExamExtractor(60, ("low",), DOMAIN)], {}, True)
    events, outcomes, errors = r.run([ANN], {OS.id: OS})
    assert events == [] and outcomes[0].source == "disabled" and errors == []


def test_prompt_change_invalidates_cache():
    cache = {}
    runner([ExamExtraction(exams=[EXAM])], cache)[0].run([ANN], {OS.id: OS})
    cache["exam"]["1"]["prompt"] = "old-prompt"
    r, msgs = runner([ExamExtraction(exams=[])], cache)
    r.run([ANN], {OS.id: OS})
    assert len(msgs.calls) == 1 and cache["exam"]["1"]["prompt"] != "old-prompt"
