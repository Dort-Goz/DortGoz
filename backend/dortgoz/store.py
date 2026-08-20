from __future__ import annotations

from pathlib import Path

from .config import settings
from .events import Event


class TraceStore:
    def __init__(self, run_id: str) -> None:
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        self.path: Path = settings.runs_dir / f"{run_id}.jsonl"

    def append(self, event: Event) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
