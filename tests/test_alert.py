import json

import httpx

from knu_lms_ics.alert import Alerter
from knu_lms_ics.state import State


def test_alerts_once_at_threshold_then_recovers(tmp_path):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["content"])
        return httpx.Response(204)

    state = State.load(tmp_path)
    alerter = Alerter("https://discord.example/webhook", 3, transport=httpx.MockTransport(handler))
    for _ in range(4):
        alerter.record(state, ["Canvas 수집 실패: /api/v1/users/self: 401 인증 실패"])
    assert len(sent) == 1 and "3회 연속 실패" in sent[0] and "401" in sent[0]
    alerter.record(state, [])
    assert len(sent) == 2 and "복구" in sent[1]
    assert state.data["consecutive_failures"] == 0 and state.data["alerted"] is False


def test_failed_webhook_retries_next_run(tmp_path):
    codes = [500, 204]
    state = State.load(tmp_path)
    alerter = Alerter("https://discord.example/webhook", 1, transport=httpx.MockTransport(lambda r: httpx.Response(codes.pop(0))))
    alerter.record(state, ["x"])
    assert state.data["alerted"] is False
    alerter.record(state, ["x"])
    assert state.data["alerted"] is True


def test_state_roundtrip_and_corrupt_file_is_moved(tmp_path):
    state = State.load(tmp_path)
    state.extract_cache["exam"] = {"1": {"hash": "h"}}
    state.save()
    assert State.load(tmp_path).extract_cache == {"exam": {"1": {"hash": "h"}}}
    (tmp_path / "state.json").write_text("{broken")
    fresh = State.load(tmp_path)
    assert fresh.extract_cache == {}
    assert any(p.name.startswith("state.json.corrupt-") for p in tmp_path.iterdir())
