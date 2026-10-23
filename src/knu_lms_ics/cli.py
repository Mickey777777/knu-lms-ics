import argparse
import logging
import time
from pathlib import Path

from .report import print_report
from .run import run_once
from .settings import EFFORT_LEVELS, EFFORT_NONE, ConfigError, load_dotenv, load_settings

QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "anthropic")

log = logging.getLogger("knu_lms_ics")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="knu-lms-ics", description="KNU LMS 일정을 ICS 피드로 만든다")
    parser.add_argument("--once", action="store_true", help="한 번만 실행하고 끝냄")
    parser.add_argument("--dry-run", action="store_true", help="피드·캐시를 쓰지 않고 수집·추출 결과만 출력")
    parser.add_argument("--model", help="시험 추출 모델 (기본: EXAM_MODEL)")
    parser.add_argument("--effort", choices=[*EFFORT_LEVELS, EFFORT_NONE], help="effort (none이면 보내지 않음)")
    parser.add_argument("--env-file", default=".env", help="환경변수 파일 (이미 설정된 값이 우선)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    load_dotenv(Path(args.env_file))
    try:
        settings = load_settings(require_feed=not args.dry_run)
    except ConfigError as e:
        log.error("설정 오류: %s", e)
        return 2
    model = args.model or settings.exam_model
    effort = settings.exam_effort if args.effort is None else (None if args.effort == EFFORT_NONE else args.effort)

    if args.dry_run:
        result = run_once(settings, dry_run=True, model=model, effort=effort)
        print_report(result)
        return 0 if result.ok else 1
    if args.once:
        return 0 if run_once(settings, dry_run=False, model=model, effort=effort).ok else 1

    interval = settings.interval_minutes * 60
    log.info("워커 시작: %d분 간격", settings.interval_minutes)
    while True:
        started = time.monotonic()
        run_once(settings, dry_run=False, model=model, effort=effort)
        time.sleep(max(0.0, interval - (time.monotonic() - started)))
