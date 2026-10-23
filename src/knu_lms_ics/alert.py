import logging

import httpx

from .state import State

log = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SEC = 10
DISCORD_CONTENT_LIMIT = 1900
ALERT_PREFIX = "[knu-lms-ics]"


class Alerter:
    def __init__(self, webhook_url: str | None, threshold: int, transport: httpx.BaseTransport | None = None):
        self._url = webhook_url
        self._threshold = threshold
        self._transport = transport

    def record(self, state: State, errors: list[str]) -> None:
        data = state.data
        if errors:
            data["consecutive_failures"] += 1
            count = data["consecutive_failures"]
            if count >= self._threshold and not data["alerted"]:
                body = f"{ALERT_PREFIX} {count}회 연속 실패\n" + "\n".join(f"- {e}" for e in errors)
                data["alerted"] = self._send(body)
            return
        if data["alerted"]:
            self._send(f"{ALERT_PREFIX} 복구됨 (직전 연속 실패 {data['consecutive_failures']}회)")
        data["consecutive_failures"] = 0
        data["alerted"] = False

    def _send(self, text: str) -> bool:
        if not self._url:
            log.warning("ALERT_WEBHOOK_URL 미설정, 알림을 로그로만 남김: %s", text)
            return True
        try:
            with httpx.Client(timeout=WEBHOOK_TIMEOUT_SEC, transport=self._transport) as client:
                resp = client.post(self._url, json={"content": text[:DISCORD_CONTENT_LIMIT]})
        except httpx.HTTPError as e:
            log.error("알림 전송 실패 (%s)", type(e).__name__)
            return False
        if resp.status_code >= 300:
            log.error("알림 전송 실패 (HTTP %s)", resp.status_code)
            return False
        return True
