import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anthropic

from .alert import Alerter
from .canvas import CanvasClient, CanvasError
from .collect import Collected, collect_all
from .extract import build_extractors
from .extract.base import AnnouncementOutcome
from .extract.runner import ExtractionRunner, Usage
from .feed import FeedError, build_calendar, remove_stale, validate, write_atomic
from .models import CalEvent
from .overrides import OverridesError, apply_overrides, load_overrides
from .settings import DEFAULT_FAILURE_ALERT_THRESHOLD, ConfigError, Settings, load_config
from .state import State

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    model: str
    effort: str | None
    errors: list[str] = field(default_factory=list)
    collected: Collected | None = None
    events: list[CalEvent] = field(default_factory=list)
    outcomes: list[AnnouncementOutcome] = field(default_factory=list)
    override_warnings: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    completed: bool = False

    @property
    def ok(self) -> bool:
        return self.completed and not self.errors


def run_once(settings: Settings, *, dry_run: bool, model: str, effort: str | None) -> RunResult:
    now = datetime.now(timezone.utc)
    result = RunResult(model=model, effort=effort)
    state = State.load(settings.data_dir)
    threshold = DEFAULT_FAILURE_ALERT_THRESHOLD
    try:
        cfg = load_config(settings.config_path)
        threshold = cfg.failure_alert_threshold
        overrides = load_overrides(settings.overrides_path)
        with CanvasClient(settings.canvas_base_url, settings.canvas_token) as canvas:
            collected = collect_all(canvas, settings.canvas_base_url, cfg, now, settings.uid_domain)
        result.collected = collected
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
        if client is None:
            log.warning("ANTHROPIC_API_KEY 없음: 시험 추출을 건너뛰고 캐시된 결과만 씀")
        runner = ExtractionRunner(
            client, model, effort, build_extractors(cfg, settings.uid_domain), state.extract_cache, write_cache=not dry_run
        )
        extracted, result.outcomes, extract_errors = runner.run(
            collected.announcements, {c.id: c for c in collected.courses}
        )
        result.usage = runner.usage
        result.errors += extract_errors
        result.events, result.override_warnings = apply_overrides(collected.events + extracted, overrides, settings.uid_domain)
        if not dry_run:
            filename = f"{settings.feed_token}.ics"
            data = build_calendar(result.events, now)
            validate(data, result.events)
            write_atomic(settings.out_dir, filename, data)
            removed = remove_stale(settings.out_dir, filename)
            if removed:
                log.info("이전 피드 파일 %d개 삭제", removed)
            log.info("피드 갱신: 이벤트 %d개", len(result.events))
        result.completed = True
    except CanvasError as e:
        result.errors.append(f"Canvas 수집 실패, 기존 피드 유지: {e}")
    except (ConfigError, OverridesError) as e:
        result.errors.append(f"설정 오류, 기존 피드 유지: {e}")
    except (FeedError, OSError) as e:
        result.errors.append(f"피드 쓰기 실패, 기존 피드 유지: {e}")
    except Exception as e:
        log.exception("예상하지 못한 오류")
        result.errors.append(f"예상하지 못한 오류, 기존 피드 유지: {type(e).__name__}")
    u = result.usage
    log.info(
        "Claude 사용량: 호출 %d회, 입력 %d, 출력 %d, 캐시 읽기 %d, 캐시 쓰기 %d (모델 %s, effort %s)",
        u.calls, u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens, model, effort,
    )
    for err in result.errors:
        log.error(err)
    if not dry_run:
        Alerter(settings.alert_webhook_url, threshold).record(state, result.errors)
        state.save()
    return result
