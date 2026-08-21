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


def _real_env(tmp_path: Path, manifest: Path) -> Path:
    (tmp_path / ".env").write_text(
        "DORTGOZ_MOCK=0\n"
        "DORTGOZ_LLAMA_BASE_URL=http://127.0.0.1:8080/v1\n"
        f"DORTGOZ_VLM_MANIFEST_PATH={manifest}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_preflight_accepts_manifest_whose_weights_live_on_remote_host(tmp_path: Path) -> None:
    preflight = _load_script("preflight")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"license": "Apache-2.0", "artifact_path": "/mnt/uzak/model.gguf",'
        ' "artifact_sha256": "' + "0" * 64 + '"}',
        encoding="utf-8",
    )
    errors: list[str] = []

    preflight._verify_real_config(_real_env(tmp_path, manifest), errors)

    assert errors == []


def test_preflight_still_rejects_local_weights_whose_hash_disagrees(tmp_path: Path) -> None:
    preflight = _load_script("preflight")
    weights = tmp_path / "model.gguf"
    weights.write_bytes(b"agirlik")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"license": "Apache-2.0", "artifact_path": "' + str(weights) + '",'
        ' "artifact_sha256": "' + "0" * 64 + '"}',
        encoding="utf-8",
    )
    errors: list[str] = []

    preflight._verify_real_config(_real_env(tmp_path, manifest), errors)

    assert errors == ["VLM artifact SHA-256 manifest ile eşleşmiyor"]


def test_long_feed_rejects_invalid_explicit_dataset_path_without_fallback(tmp_path: Path) -> None:
    long_feed = _load_script("make_long_feed")

    with pytest.raises(SystemExit, match="UCF-Crime kopyası değil"):
        long_feed.resolve_ucf(tmp_path / "not-a-dataset")
