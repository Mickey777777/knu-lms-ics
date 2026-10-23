import json
import logging
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

TIMEOUT_SEC = 30
MAX_PAGES = 50
THROTTLE_RETRIES = 4
THROTTLE_BASE_SLEEP_SEC = 5
TRANSIENT_RETRIES = 2
TRANSIENT_SLEEP_SEC = 3
RATE_LIMIT_FLOOR = 100.0
RATE_LIMIT_FLOOR_SLEEP_SEC = 10
JSON_HIJACK_PREFIX = "while(1);"


class CanvasError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class CanvasAuthError(CanvasError):
    pass


def _is_throttled(resp: httpx.Response) -> bool:
    return resp.status_code == 429 or (resp.status_code == 403 and "Rate Limit Exceeded" in resp.text)


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


class CanvasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._host = urlsplit(base_url).netloc
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT_SEC,
            follow_redirects=False,
            transport=transport,
        )

    def __enter__(self) -> "CanvasClient":
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    def get(self, path: str, params=None):
        return self._json(self._request(path, params), path)

    def get_all(self, path: str, params=None) -> list:
        items: list = []
        url, query = path, params
        for _ in range(MAX_PAGES):
            resp = self._request(url, query)
            body = self._json(resp, path)
            if not isinstance(body, list):
                raise CanvasError(f"{path}: 목록 응답이 아님")
            items.extend(body)
            next_url = resp.links.get("next", {}).get("url")
            if not next_url:
                return items
            next_host = urlsplit(next_url).netloc
            if next_host != self._host:
                raise CanvasError(f"{path}: 다음 페이지 호스트가 다름 ({next_host})")
            url, query = next_url, None
        raise CanvasError(f"{path}: 페이지가 {MAX_PAGES}개를 넘음")

    def _request(self, url: str, params) -> httpx.Response:
        path = urlsplit(url).path
        throttled = transient = 0
        while True:
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError as e:
                if transient >= TRANSIENT_RETRIES:
                    raise CanvasError(f"{path}: 네트워크 오류 ({type(e).__name__})") from e
                transient += 1
                self._sleep(TRANSIENT_SLEEP_SEC * transient)
                continue
            if _is_throttled(resp):
                if throttled >= THROTTLE_RETRIES:
                    raise CanvasError(f"{path}: 레이트리밋 재시도 초과", resp.status_code)
                wait = _retry_after(resp) or THROTTLE_BASE_SLEEP_SEC * 2**throttled
                throttled += 1
                log.warning("Canvas 레이트리밋(%s): %.0f초 대기", resp.status_code, wait)
                self._sleep(wait)
                continue
            if resp.status_code >= 500 and transient < TRANSIENT_RETRIES:
                transient += 1
                self._sleep(TRANSIENT_SLEEP_SEC * transient)
                continue
            break
        self._respect_quota(resp)
        if resp.status_code == 401:
            raise CanvasAuthError(f"{path}: 401 인증 실패 (토큰 만료·폐기 확인)", 401)
        if resp.status_code != 200:
            raise CanvasError(f"{path}: HTTP {resp.status_code}", resp.status_code)
        return resp

    def _respect_quota(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("x-rate-limit-remaining")
        try:
            if remaining is not None and float(remaining) < RATE_LIMIT_FLOOR:
                log.info("Canvas 남은 할당량 %s: %d초 대기", remaining, RATE_LIMIT_FLOOR_SLEEP_SEC)
                self._sleep(RATE_LIMIT_FLOOR_SLEEP_SEC)
        except ValueError:
            pass

    @staticmethod
    def _json(resp: httpx.Response, path: str):
        if "json" not in resp.headers.get("content-type", ""):
            raise CanvasError(f"{path}: JSON이 아닌 응답 ({resp.headers.get('content-type')})")
        text = resp.text
        if text.startswith(JSON_HIJACK_PREFIX):
            text = text[len(JSON_HIJACK_PREFIX):]
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise CanvasError(f"{path}: JSON 파싱 실패") from e
