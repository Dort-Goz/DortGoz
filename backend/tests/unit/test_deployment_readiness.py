"""Deployment profili ve fail-closed hazır olma kapısı."""

from pathlib import Path

import pytest

from dortgoz.config import Settings
from dortgoz.services.deployment_readiness import DeploymentReadinessService


class MemoryRepository:
    persistence_mode = "memory"


def local_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "mock": False,
        "deployment_profile": "development",
        "media_dir": tmp_path / "media",
        "runs_dir": tmp_path / "runs",
        "event_store_path": None,
        "vlm_manifest_path": None,
        "dfine_active_manifest": tmp_path / "models" / "active_manifest.json",
        "dfine_workspace_root": tmp_path,
        "dfine_onnx": str(tmp_path / "models" / "dfine.onnx"),
        "candidate_model_manifest": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_mock_profile_is_ready_without_models_or_sqlite(tmp_path: Path) -> None:
    settings = local_settings(tmp_path, mock=True)

    report = await DeploymentReadinessService(settings, MemoryRepository()).inspect(force=True)

    assert report.profile == "mock"
    assert report.ready is True
    assert report.degraded is False
    assert report.components["event_store"]["mode"] == "memory"
    assert report.components["model"]["ready"] is True


@pytest.mark.asyncio
async def test_competition_profile_lists_every_blocking_component(tmp_path: Path) -> None:
    settings = local_settings(tmp_path, deployment_profile="competition-real")

    report = await DeploymentReadinessService(settings, MemoryRepository()).inspect(force=True)

    assert report.ready is False
    reasons = "\n".join(report.blocking_reasons())
    assert "event_store" in reasons
    assert "model" in reasons
    assert "dfine" in reasons
    assert "siglip" in reasons
    assert "procedures" in reasons


@pytest.mark.asyncio
async def test_development_does_not_require_sqlite_but_requires_vlm(tmp_path: Path) -> None:
    settings = local_settings(tmp_path)

    report = await DeploymentReadinessService(settings, MemoryRepository()).inspect(force=True)

    assert report.components["event_store"]["required"] is False
    assert report.components["model"]["required"] is True
    assert report.ready is False
