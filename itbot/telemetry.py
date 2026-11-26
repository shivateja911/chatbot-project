"""One JSON line per turn. Text is redacted before it gets here."""
import json
from datetime import datetime
from pathlib import Path


class TurnLogger:
    def __init__(self, path: Path):
        self.path = path

    def log(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": datetime.now().isoformat(timespec="seconds"), **record}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
