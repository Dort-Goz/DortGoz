from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_STORE_KEY = "DORTGOZ_EVENT_STORE_PATH"


def _store_is_configured() -> bool:
    if os.environ.get(_STORE_KEY):
        return True
    if not _ENV_PATH.is_file():
        return False
    for raw_line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and line.split("=", 1)[0].strip() == _STORE_KEY:
            return True
    return False


if _store_is_configured():
    os.environ[_STORE_KEY] = str(
        Path(tempfile.mkdtemp(prefix="dortgoz-tests-")) / "event_memory.sqlite3"
    )

import pytest  # noqa: E402

from dortgoz.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_event_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if settings.event_store_path is None:
        return
    monkeypatch.setattr(settings, "event_store_path", tmp_path / "event_memory.sqlite3")
