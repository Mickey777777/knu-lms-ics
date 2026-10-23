import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

STATE_FILE = "state.json"


def _default() -> dict:
    return {"consecutive_failures": 0, "alerted": False, "extract_cache": {}}


class State:
    def __init__(self, path: Path, data: dict):
        self.path = path
        self.data = data

    @classmethod
    def load(cls, data_dir: Path) -> "State":
        path = data_dir / STATE_FILE
        if not path.is_file():
            return cls(path, _default())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("최상위가 객체가 아님")
        except (json.JSONDecodeError, ValueError) as e:
            backup = path.with_name(f"{STATE_FILE}.corrupt-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
            path.rename(backup)
            log.error("state 파일이 손상돼 %s로 옮기고 새로 시작함: %s", backup.name, e)
            return cls(path, _default())
        return cls(path, _default() | data)

    @property
    def extract_cache(self) -> dict:
        return self.data["extract_cache"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
