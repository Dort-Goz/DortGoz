

from pathlib import Path

import pytest

from dortgoz.config import Settings
from dortgoz.services import deployment_readiness
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
        "api_key": "",
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
async def test_development_does_not_require_sqlite_but_requires_evren(tmp_path: Path) -> None:
    settings = local_settings(tmp_path)

    report = await DeploymentReadinessService(settings, MemoryRepository()).inspect(force=True)

    assert report.components["event_store"]["required"] is False
    assert report.components["model"]["required"] is True
    assert report.components["model"]["mode"] == "evren"
    assert report.ready is False


@pytest.mark.asyncio
async def test_evren_readiness_requires_every_configured_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aliases = [
        "llm-fast",
        "llm-large",
        "vlm",
        "router",
        "guard",
        "bge-m3-embed",
    ]

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"id": alias} for alias in aliases]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(deployment_readiness.httpx, "AsyncClient", lambda **_kwargs: Client())
    settings = local_settings(tmp_path, api_key="fixture-key")

    report = await DeploymentReadinessService(settings, MemoryRepository()).inspect(force=True)

    assert report.components["model"]["ready"] is True
    assert report.components["model"]["mode"] == "evren"
