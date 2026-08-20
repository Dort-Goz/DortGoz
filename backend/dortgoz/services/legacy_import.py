from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LegacyRecord:
    line_number: int
    payload: dict[str, Any]


def iter_legacy_jsonl(path: Path) -> Iterator[LegacyRecord]:

    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    with resolved.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError(f"legacy JSON object değil: satır {line_number}")
            yield LegacyRecord(line_number=line_number, payload=payload)
