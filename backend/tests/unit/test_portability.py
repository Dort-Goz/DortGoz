from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_clone_preflight_accepts_tracked_repository() -> None:
    preflight = _load_script("preflight")
    errors: list[str] = []

    preflight._verify_repository(ROOT, errors)

    assert errors == []


def test_long_feed_rejects_invalid_explicit_dataset_path_without_fallback(tmp_path: Path) -> None:
    long_feed = _load_script("make_long_feed")

    with pytest.raises(SystemExit, match="UCF-Crime kopyası değil"):
        long_feed.resolve_ucf(tmp_path / "not-a-dataset")
