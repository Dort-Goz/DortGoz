"""Konteyner paketleme, bulut telemetrisi kapatma ve yapılandırma sınırları."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from dortgoz.config import Settings

ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_isolated_config():
    """config.py'yi ayrı bir modül adıyla yeniden çalıştırır (import yan etkisi testi)."""
    path = ROOT / "backend" / "dortgoz" / "config.py"
    spec = importlib.util.spec_from_file_location("dortgoz_config_izole", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_image_ships_live_feed_config_directory() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY config /app/config" in dockerfile


def test_compose_mounts_config_and_models_read_only() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "./config:/app/config:ro" in compose
    assert "./models:/app/models:ro" in compose


def test_compose_and_env_template_disable_cloud_tracing() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    template = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert 'LANGSMITH_TRACING: "false"' in compose
    assert 'LANGCHAIN_TRACING_V2: "false"' in compose
    assert "LANGSMITH_TRACING=false" in template
    assert "LANGCHAIN_TRACING_V2=false" in template


def test_config_import_overrides_enabled_cloud_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    module = _load_isolated_config()

    assert module.os.environ["LANGSMITH_TRACING"] == "false"
    assert module.os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_preflight_rejects_enabled_cloud_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    preflight = _load_script("preflight")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    errors: list[str] = []

    preflight._verify_cloud_telemetry(ROOT, errors)

    assert any("LANGSMITH_TRACING" in error for error in errors)


def test_preflight_accepts_disabled_cloud_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    preflight = _load_script("preflight")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    errors: list[str] = []

    preflight._verify_cloud_telemetry(ROOT, errors)

    assert errors == []


def test_preflight_reads_cloud_tracing_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _load_script("preflight")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    (tmp_path / ".env").write_text("LANGCHAIN_TRACING_V2=1\n", encoding="utf-8")
    errors: list[str] = []

    preflight._verify_cloud_telemetry(tmp_path, errors)

    assert any("LANGCHAIN_TRACING_V2" in error for error in errors)


def test_build_artifact_is_ignored() -> None:
    ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*.tsbuildinfo" in ignore_rules

    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git yok")
    result = subprocess.run(
        [git, "-C", str(ROOT), "check-ignore", "frontend/tsconfig.tsbuildinfo"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("field", ["live_keep_segments", "live_keep_runs"])
def test_live_retention_fields_reject_zero(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0})
