"""Mock, development ve competition-real çalışma kapıları."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..pipeline.candidate_model import load_candidate_scorer
from ..pipeline.perception import resolve_production_model_path
from ..pipeline.semantic import SemanticCandidateModel
from ..repositories.procedure_index import LocalProcedureIndex
from ..tools.local_vlm import load_local_vlm_manifest


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    profile: str
    components: dict[str, dict[str, Any]]

    @property
    def ready(self) -> bool:
        return all(
            component.get("ready", False)
            for component in self.components.values()
            if component.get("required", False)
        )

    @property
    def degraded(self) -> bool:
        return any(not component.get("ready", False) for component in self.components.values())

    def blocking_reasons(self) -> list[str]:
        return [
            f"{name}: {component.get('detail', 'hazır değil')}"
            for name, component in self.components.items()
            if component.get("required", False) and not component.get("ready", False)
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "profile": self.profile,
            "degraded": self.degraded,
            "components": self.components,
        }


class DeploymentReadinessService:
    """Yerel artifact, araç, kalıcılık ve model endpoint'ini birlikte denetle."""

    def __init__(self, settings: Settings, repository: Any, *, cache_seconds: float = 3.0) -> None:
        self.settings = settings
        self.repository = repository
        self.cache_seconds = cache_seconds
        self._cache: tuple[float, ReadinessReport] | None = None
        self._lock = asyncio.Lock()

    async def inspect(self, *, force: bool = False) -> ReadinessReport:
        now = time.monotonic()
        if not force and self._cache is not None and now - self._cache[0] < self.cache_seconds:
            return self._cache[1]
        async with self._lock:
            now = time.monotonic()
            if not force and self._cache is not None and now - self._cache[0] < self.cache_seconds:
                return self._cache[1]
            report = await self._inspect_uncached()
            self._cache = (time.monotonic(), report)
            return report

    async def _inspect_uncached(self) -> ReadinessReport:
        profile = self.settings.runtime_profile
        competition = profile == "competition-real"
        components: dict[str, dict[str, Any]] = {
            "storage": self._storage(),
            "event_store": self._event_store(
                required=competition,
                allow_memory=profile == "mock",
            ),
            "media_tools": self._media_tools(
                required=profile != "mock",
                skipped=profile == "mock",
            ),
        }
        if profile == "mock":
            components.update(
                {
                    "model": self._mock_component("mock olay akışı"),
                    "dfine": self._mock_component("mock profilde kullanılmaz"),
                    "siglip": self._mock_component("mock profilde kullanılmaz"),
                    "procedures": self._mock_component("mock profilde kullanılmaz"),
                }
            )
            return ReadinessReport(profile=profile, components=components)

        components["model"] = await self._vlm(required=True)
        components["dfine"] = self._dfine(required=competition)
        components["siglip"] = self._siglip(required=competition)
        components["procedures"] = self._procedures(required=competition)
        return ReadinessReport(profile=profile, components=components)

    def _storage(self) -> dict[str, Any]:
        try:
            for directory in (self.settings.media_dir, self.settings.runs_dir):
                directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=directory):
                    pass
        except OSError as exc:
            return self._component(True, False, f"{type(exc).__name__}: {exc}")
        return self._component(True, True, "medya ve koşu dizinleri yazılabilir")

    def _event_store(self, *, required: bool, allow_memory: bool = False) -> dict[str, Any]:
        mode = getattr(self.repository, "persistence_mode", "memory")
        path = self.settings.event_store_path
        sqlite_ready = mode == "sqlite" and path is not None
        ready = sqlite_ready or allow_memory
        if sqlite_ready:
            detail = "kalıcı SQLite etkin"
        elif allow_memory:
            detail = "mock profil için süreç içi depo etkin"
        else:
            detail = "kalıcı SQLite yapılandırılmadı"
        result = self._component(required, ready, detail)
        result.update({"mode": mode, "path": str(path) if path is not None else None})
        schema_version = getattr(self.repository, "schema_version", None)
        if schema_version is not None:
            result["schema_version"] = schema_version
        return result

    def _media_tools(self, *, required: bool, skipped: bool = False) -> dict[str, Any]:
        if skipped:
            return self._component(False, True, "mock profilde kullanılmaz")
        missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
        return self._component(
            required,
            not missing,
            "ffmpeg ve ffprobe bulundu" if not missing else f"eksik araçlar: {', '.join(missing)}",
        )

    async def _vlm(self, *, required: bool) -> dict[str, Any]:
        path = self.settings.vlm_manifest_path
        if path is None:
            result = self._component(required, False, "DORTGOZ_VLM_MANIFEST_PATH ayarlanmadı")
            result.update({"mode": "local_vlm", "endpoint_checked": False})
            return result
        try:
            manifest = load_local_vlm_manifest(path)
            if manifest.model_id != self.settings.main_model:
                raise ValueError("VLM manifest model_id ile DORTGOZ_MAIN_MODEL eşleşmiyor")
        except Exception as exc:
            result = self._component(required, False, str(exc))
            result.update(
                {"mode": "local_vlm", "manifest_path": str(path), "endpoint_checked": False}
            )
            return result

        endpoint = self.settings.llama_base_url.rstrip("/") + "/models"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    endpoint,
                    headers={"Authorization": f"Bearer {self.settings.api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
            model_ids = {
                item.get("id")
                for item in payload.get("data", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            if self.settings.main_model not in model_ids:
                raise ValueError("yapılandırılmış ana model endpoint üzerinde bulunamadı")
        except Exception as exc:
            result = self._component(required, False, f"yerel VLM endpoint hazır değil: {exc}")
            result.update(
                {"mode": "local_vlm", "manifest_path": str(path), "endpoint_checked": True}
            )
            return result
        result = self._component(required, True, "VLM manifest, hash ve endpoint doğrulandı")
        result.update(
            {
                "mode": "local_vlm",
                "manifest_path": str(path),
                "model_id": manifest.model_id,
                "endpoint_checked": True,
            }
        )
        return result

    def _dfine(self, *, required: bool) -> dict[str, Any]:
        if not self.settings.detector_enabled:
            return self._component(required, not required, "D-FINE devre dışı")
        manifest = Path(self.settings.dfine_active_manifest)
        if required and not manifest.is_file():
            return self._component(required, False, "hash doğrulamalı D-FINE active manifest yok")
        try:
            model = resolve_production_model_path(
                active_manifest=self.settings.dfine_active_manifest,
                fallback_onnx=self.settings.dfine_onnx,
                workspace_root=self.settings.dfine_workspace_root,
            )
            if not model.is_file():
                raise FileNotFoundError(f"D-FINE ONNX bulunamadı: {model}")
        except Exception as exc:
            return self._component(required, False, str(exc))
        result = self._component(required, True, "D-FINE artifact doğrulandı")
        result.update({"model_path": str(model), "manifest_path": str(manifest)})
        return result

    def _siglip(self, *, required: bool) -> dict[str, Any]:
        manifest_value = self.settings.candidate_model_manifest
        if not manifest_value:
            return self._component(required, False, "SigLIP production manifest yapılandırılmadı")
        manifest = Path(manifest_value)
        try:
            scorer = load_candidate_scorer(manifest)
            if not isinstance(scorer, SemanticCandidateModel):
                raise ValueError("competition-real profil SigLIP semantic scorer gerektirir")
            scorer.verify_artifacts()
        except Exception as exc:
            return self._component(required, False, str(exc))
        result = self._component(required, True, "SigLIP manifest ve artifact hash'leri doğrulandı")
        result.update({"manifest_path": str(manifest), "model_id": scorer.model_id})
        return result

    def _procedures(self, *, required: bool) -> dict[str, Any]:
        root = self.settings.media_dir.parent / "data" / "procedures"
        manifest = root / "manifest.json"
        try:
            index = LocalProcedureIndex.load(root, manifest)
            approved = sum(document.approved_for_demo for document in index.manifest.documents)
            if required and approved == 0:
                raise ValueError("onaylı yerel prosedür belgesi yok")
        except Exception as exc:
            return self._component(required, False, str(exc))
        result = self._component(required, approved > 0, "yerel prosedür manifest doğrulandı")
        result.update({"manifest_path": str(manifest), "approved_documents": approved})
        return result

    @staticmethod
    def _mock_component(detail: str) -> dict[str, Any]:
        return DeploymentReadinessService._component(False, True, detail)

    @staticmethod
    def _component(required: bool, ready: bool, detail: str) -> dict[str, Any]:
        return {"required": required, "ready": ready, "detail": detail}


__all__ = ["DeploymentReadinessService", "ReadinessReport"]
