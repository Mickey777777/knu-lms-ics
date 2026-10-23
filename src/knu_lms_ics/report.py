from datetime import datetime, timedelta

from .models import KST, CalEvent
from .run import RunResult

WEEKDAYS_KO = "월화수목금토일"
SOURCE_LABELS = {"api": "API 호출", "cache": "캐시", "failed": "실패(미처리)", "disabled": "추출 꺼짐"}


def _when(ev: CalEvent) -> str:
    if ev.all_day:
        last = ev.end - timedelta(days=1)
        day = f"{ev.start:%Y-%m-%d}({WEEKDAYS_KO[ev.start.weekday()]})"
        return f"{day} 종일" if last == ev.start else f"{day}~{last:%m-%d} 종일"
    start, end = ev.start.astimezone(KST), ev.end.astimezone(KST)
    return f"{start:%Y-%m-%d}({WEEKDAYS_KO[start.weekday()]}) {start:%H:%M}~{end:%H:%M}"


def print_report(result: RunResult) -> None:
    c = result.collected
    if c is not None:
        print(f"== 과목: 포함 {len(c.courses)}개, 제외 {len(c.excluded_courses)}개")
        for course in c.courses:
            print(f"  + {course.display_name}  ({course.id}, {course.term_name})")
        for course in c.excluded_courses:
            print(f"  - {course.name}  ({course.id}, {course.term_name})")
        if c.skipped_planner_types:
            print(f"  처리하지 않은 플래너 유형: {dict(c.skipped_planner_types)}")

    print(f"\n== 피드 이벤트 {len(result.events)}개")
    for ev in sorted(result.events, key=lambda e: (e.sort_key(), e.uid)):
        loc = f"  @{ev.location}" if ev.location else ""
        print(f"  {_when(ev):<32} {ev.summary}{loc}")
        print(f"  {'':<32} {ev.uid}")

    print(f"\n== 시험 추출 (모델 {result.model}, effort {result.effort})")
    quiet = 0
    for o in result.outcomes:
        if not o.candidates and o.source != "failed":
            quiet += 1
            continue
        a = o.announcement
        posted = a.posted_at.astimezone(KST)
        print(f"  [공지 {a.id}] {a.title}  ({posted:%Y-%m-%d %H:%M} 작성, {SOURCE_LABELS.get(o.source, o.source)})")
        if o.error:
            print(f"      오류: {o.error}")
        for cand in o.candidates:
            it = cand.item
            times = it.start_time or "시각 없음"
            if it.end_time:
                times += f"~{it.end_time}"
            status = "채택" if cand.event is not None else f"제외: {cand.reason}"
            print(f"    #{cand.index} {status}")
            print(f"       {it.exam_name} | {it.date} {times} | 장소 {it.location or '-'} | 확신도 {it.confidence}")
            print(f"       evidence: {it.evidence}")
            if cand.event is not None:
                print(f"       → {cand.event.summary}  [{_when(cand.event)}]")
    print(f"  (시험 없는 공지 {quiet}개 생략)")

    u = result.usage
    print(
        f"\n== Claude 사용량: 호출 {u.calls}회, 입력 {u.input_tokens}, 출력 {u.output_tokens}, "
        f"캐시 읽기 {u.cache_read_tokens}, 캐시 쓰기 {u.cache_write_tokens}"
    )
    for w in result.override_warnings:
        print(f"== overrides 경고: {w}")
    for e in result.errors:
        print(f"== 오류: {e}")
    print(f"\n(dry-run: 피드·캐시를 쓰지 않음, {datetime.now(KST):%Y-%m-%d %H:%M} KST)")
