import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "low"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT_NONE = "none"
DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_LOOKBACK_DAYS = 120
DEFAULT_LOOKAHEAD_DAYS = 180
DEFAULT_EXAM_DURATION_MINUTES = 60
DEFAULT_REVIEW_CONFIDENCE = ("low",)
DEFAULT_EXCLUDE_TERM_KEYWORDS = ("비교과",)
DEFAULT_FAILURE_ALERT_THRESHOLD = 3
FEED_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,}$")
DEFAULT_UID_DOMAIN = "knu-lms-ics"
UID_DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    canvas_base_url: str
    canvas_token: str
    anthropic_api_key: str | None
    feed_token: str | None
    alert_webhook_url: str | None
    uid_domain: str
    exam_model: str
    exam_effort: str | None
    interval_minutes: int
    data_dir: Path
    out_dir: Path
    config_path: Path
    overrides_path: Path


@dataclass(frozen=True)
class AppConfig:
    course_aliases: dict[str, str]
    exclude_term_keywords: tuple[str, ...]
    lookback_days: int
    lookahead_days: int
    exam_default_duration_minutes: int
    exam_review_confidence: tuple[str, ...]
    failure_alert_threshold: int


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as e:
        raise ConfigError(f"{name}는 정수여야 함") from e


def load_settings(require_feed: bool) -> Settings:
    base_url = _env("CANVAS_BASE_URL")
    token = _env("CANVAS_TOKEN")
    if not base_url or not token:
        raise ConfigError("CANVAS_BASE_URL, CANVAS_TOKEN이 필요함")
    feed_token = _env("FEED_TOKEN")
    if require_feed and (feed_token is None or not FEED_TOKEN_PATTERN.fullmatch(feed_token)):
        raise ConfigError("FEED_TOKEN이 없거나 형식이 틀림 (32바이트 이상 base64url, 43자 이상)")
    interval = _env_int("INTERVAL_MINUTES", DEFAULT_INTERVAL_MINUTES)
    if interval < 1:
        raise ConfigError("INTERVAL_MINUTES는 1 이상이어야 함")
    effort = _env("EXAM_EFFORT") or DEFAULT_EFFORT
    if effort not in (*EFFORT_LEVELS, EFFORT_NONE):
        raise ConfigError(f"EXAM_EFFORT는 {', '.join(EFFORT_LEVELS)}, {EFFORT_NONE} 중 하나여야 함")
    uid_domain = _env("UID_DOMAIN") or DEFAULT_UID_DOMAIN
    if not UID_DOMAIN_PATTERN.fullmatch(uid_domain):
        raise ConfigError("UID_DOMAIN은 영문·숫자·점·하이픈만 쓸 수 있음")
    return Settings(
        canvas_base_url=base_url.rstrip("/"),
        canvas_token=token,
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        feed_token=feed_token,
        alert_webhook_url=_env("ALERT_WEBHOOK_URL"),
        uid_domain=uid_domain,
        exam_model=_env("EXAM_MODEL") or DEFAULT_MODEL,
        exam_effort=None if effort == EFFORT_NONE else effort,
        interval_minutes=interval,
        data_dir=Path(_env("DATA_DIR") or "data"),
        out_dir=Path(_env("OUT_DIR") or "out"),
        config_path=Path(_env("CONFIG_PATH") or "config/config.yaml"),
        overrides_path=Path(_env("OVERRIDES_PATH") or "config/overrides.yaml"),
    )


def _positive_int(raw: dict, key: str, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"config: {key}는 1 이상의 정수여야 함")
    return value


def _str_list(raw: dict, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = raw.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"config: {key}는 문자열 목록이어야 함")
    return tuple(value)


def load_config(path: Path) -> AppConfig:
    raw: dict = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ConfigError(f"config 파싱 실패: {e}") from e
        if loaded is not None and not isinstance(loaded, dict):
            raise ConfigError("config 최상위는 매핑이어야 함")
        raw = loaded or {}
    aliases = raw.get("course_aliases") or {}
    if not isinstance(aliases, dict) or not all(isinstance(v, str) for v in aliases.values()):
        raise ConfigError("config: course_aliases는 {과목 id 또는 과목명: 별칭} 매핑이어야 함")
    exam = raw.get("exam") or {}
    alert = raw.get("alert") or {}
    if not isinstance(exam, dict) or not isinstance(alert, dict):
        raise ConfigError("config: exam, alert는 매핑이어야 함")
    return AppConfig(
        course_aliases={str(k): v for k, v in aliases.items()},
        exclude_term_keywords=_str_list(raw, "exclude_term_keywords", DEFAULT_EXCLUDE_TERM_KEYWORDS),
        lookback_days=_positive_int(raw, "lookback_days", DEFAULT_LOOKBACK_DAYS),
        lookahead_days=_positive_int(raw, "lookahead_days", DEFAULT_LOOKAHEAD_DAYS),
        exam_default_duration_minutes=_positive_int(exam, "default_duration_minutes", DEFAULT_EXAM_DURATION_MINUTES),
        exam_review_confidence=_str_list(exam, "review_confidence", DEFAULT_REVIEW_CONFIDENCE),
        failure_alert_threshold=_positive_int(alert, "consecutive_failures", DEFAULT_FAILURE_ALERT_THRESHOLD),
    )
