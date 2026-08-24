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


def _real_env(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "DORTGOZ_MOCK": "0",
        "DORTGOZ_DEPLOYMENT_PROFILE": "competition-real",
        "DORTGOZ_EVENT_STORE_PATH": "runs/event_memory.sqlite3",
        "DORTGOZ_LLAMA_BASE_URL": "https://inference.example.invalid/v1",
        "DORTGOZ_API_KEY": "fixture-key",
        "DORTGOZ_MAIN_MODEL": "llm-fast",
        "DORTGOZ_VIDEO_MODEL": "vlm",
        "DORTGOZ_SECOND_OPINION_MODEL": "llm-large",
        "DORTGOZ_AGENT_MODEL": "llm-fast",
        "DORTGOZ_ROUTER_MODEL": "router",
        "DORTGOZ_GUARD_MODEL": "guard",
        "DORTGOZ_EMBEDDING_MODEL": "bge-m3-embed",
        "DORTGOZ_QDRANT_URL": "https://qdrant.example.invalid",
        "DORTGOZ_QDRANT_PREFIX": "team-test",
        "DORTGOZ_QDRANT_API_KEY": "qdr-fixture",
    }
    values.update(overrides)
    (tmp_path / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    return tmp_path


def test_preflight_accepts_evren_configuration(tmp_path: Path) -> None:
    preflight = _load_script("preflight")
    errors: list[str] = []

    preflight._verify_real_config(_real_env(tmp_path), errors)

    assert errors == []


def test_preflight_rejects_wrong_evren_alias(tmp_path: Path) -> None:
    preflight = _load_script("preflight")
    errors: list[str] = []

    preflight._verify_real_config(
        _real_env(tmp_path, DORTGOZ_VIDEO_MODEL="qwen3-vl"), errors
    )

    assert errors == ["DORTGOZ_VIDEO_MODEL=vlm olmalı"]


def test_long_feed_rejects_invalid_explicit_dataset_path_without_fallback(tmp_path: Path) -> None:
    long_feed = _load_script("make_long_feed")

    with pytest.raises(SystemExit, match="UCF-Crime kopyası değil"):
        long_feed.resolve_ucf(tmp_path / "not-a-dataset")
