"""İz kaydı — her koşunun tam olay günlüğü (JSONL, salt-ekleme).

JSONL seçimi bilinçli: grep'lenebilir, kıyaslama düzeneğinin girdisidir ve
`replay_jsonl` ile demo tekrarı aynı dosyadan yapılır. (İlişkisel sorgu
gerekirse SQLite sonradan eklenir — şimdilik gerek yok.)
"""

from __future__ import annotations

from pathlib import Path

from .config import settings
from .events import Event
from .services.run_identity import safe_run_file


class TraceStore:
    def __init__(self, run_id: str) -> None:
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        self.path: Path = safe_run_file(settings.runs_dir, run_id, ".jsonl")

    def append(self, event: Event) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
