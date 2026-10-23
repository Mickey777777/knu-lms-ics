import httpx
import pytest

from knu_lms_ics.canvas import CanvasAuthError, CanvasClient, CanvasError

BASE = "https://canvas.example.edu"
JSON = {"content-type": "application/json; charset=utf-8"}


def client_for(handler, sleeps=None):
    return CanvasClient(BASE, "secret", transport=httpx.MockTransport(handler), sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_get_all_follows_next_link_and_sends_bearer():
    seen = []

    def handler(request):
        seen.append((request.url.params.get("page"), request.headers["authorization"]))
        if request.url.params.get("page") == "2":
            return httpx.Response(200, headers=JSON, json=[{"id": 3}])
        return httpx.Response(200, headers=JSON | {"link": f'<{BASE}/api/v1/x?page=2>; rel="next"'}, json=[{"id": 1}, {"id": 2}])

    with client_for(handler) as c:
        assert [i["id"] for i in c.get_all("/api/v1/x", {"per_page": 2})] == [1, 2, 3]
    assert seen == [(None, "Bearer secret"), ("2", "Bearer secret")]


def test_get_all_refuses_next_link_on_other_host():
    def handler(request):
        return httpx.Response(200, headers=JSON | {"link": '<https://evil.example/api/v1/x?page=2>; rel="next"'}, json=[])

    with client_for(handler) as c, pytest.raises(CanvasError, match="호스트"):
        c.get_all("/api/v1/x")


def test_throttled_request_is_retried_with_backoff():
    calls = []
    sleeps = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(403, text="403 Forbidden (Rate Limit Exceeded)")
        return httpx.Response(200, headers=JSON | {"x-rate-limit-remaining": "500.0"}, json={"id": 1})

    with client_for(handler, sleeps) as c:
        assert c.get("/api/v1/users/self") == {"id": 1}
    assert len(calls) == 2 and sleeps == [5]


def test_low_quota_triggers_pause():
    sleeps = []

    def handler(request):
        return httpx.Response(200, headers=JSON | {"x-rate-limit-remaining": "20.0"}, json={})

    with client_for(handler, sleeps) as c:
        c.get("/api/v1/users/self")
    assert sleeps == [10]


def test_401_raises_auth_error():
    with client_for(lambda r: httpx.Response(401, headers=JSON, json={})) as c, pytest.raises(CanvasAuthError):
        c.get("/api/v1/users/self")


def test_html_response_is_an_error():
    with client_for(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text="<html>")) as c:
        with pytest.raises(CanvasError, match="JSON"):
            c.get("/api/v1/users/self")
