from ..settings import AppConfig
from .base import Extractor
from .exam import ExamExtractor


def build_extractors(cfg: AppConfig, uid_domain: str) -> list[Extractor]:
    return [ExamExtractor(cfg.exam_default_duration_minutes, cfg.exam_review_confidence, uid_domain)]
