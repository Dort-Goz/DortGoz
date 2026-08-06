"""Local candidate scorer sözleşmesi, manifest doğrulaması ve scorer yükleme.

``MotionBaselineModel`` gerçek learned CNN değildir; Task 07'nin ölçülebilir
referansıdır. Model artifact'i geldiğinde yalnızca ``score_motion_profile``
implementasyonu değişir, interval builder ve agent contract aynı kalır.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..domain.candidate import CandidateEvent, ScreeningSample
from .candidate_intervals import IntervalConfig, build_candidate_intervals
from .ingest import MotionSample
from .temporal_cnn import TemporalCnnArtifact, TemporalCnnCandidateModel


class CandidateModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_type: Literal["motion_baseline", "onnx_cnn", "temporal_cnn"]
    artifact_path: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license: Literal["Apache-2.0", "MIT"]
    input_fps: float = Field(gt=0)
    feature_schema: tuple[str, ...] = Field(min_length=1)
    notes: str = ""


class CandidateScorer(Protocol):
    model_id: str

    def score(self, profile: list[MotionSample]) -> list[ScreeningSample]: ...


class MotionBaselineModel:
    """Hareket profilini reproducible screening skorlarına çeviren referans."""

    model_id = "motion-baseline-v1"
    _FEATURE_SCHEMA = ("changed", "fg", "mad", "activity")

    def __init__(
        self,
        *,
        activity_scale: float = 4.0,
        interaction_scale: float = 5.0,
        manifest: CandidateModelManifest | None = None,
    ) -> None:
        if activity_scale <= 0 or interaction_scale <= 0:
            raise ValueError("baseline scale değerleri pozitif olmalı")
        self.activity_scale = activity_scale
        self.interaction_scale = interaction_scale
        self.manifest = manifest or self.default_manifest()
        if self.manifest.model_id != self.model_id:
            raise ValueError("manifest model_id baseline ile eşleşmiyor")

    def score(self, profile: list[MotionSample]) -> list[ScreeningSample]:
        return [
            ScreeningSample(
                timestamp=sample.t,
                anomaly_score=_clamp(sample.activity * self.activity_scale),
                interaction_score=_clamp(sample.changed * self.interaction_scale),
                # Hareket profili fall/fire/vehicle semantiğini ayırt edemez;
                # bu alanlar bilinçli olarak sıfır kalır ve VLM'ye hüküm taşımaz.
                image_quality=1.0,
                source_model=self.model_id,
                feature_ref=f"motion:{index}",
            )
            for index, sample in enumerate(profile)
        ]

    def candidates(
        self,
        profile: list[MotionSample],
        *,
        analysis_id: str,
        video_id: str,
        duration_seconds: float,
        interval_config: IntervalConfig | None = None,
    ) -> list[CandidateEvent]:
        return build_candidate_intervals(
            self.score(profile),
            analysis_id=analysis_id,
            video_id=video_id,
            duration_seconds=duration_seconds,
            model_id=self.model_id,
            config=interval_config,
        )

    @classmethod
    def default_manifest(cls) -> CandidateModelManifest:
        return CandidateModelManifest(
            model_id=cls.model_id,
            version="1.0.0",
            model_type="motion_baseline",
            artifact_path="models/candidate/motion-baseline-v1.json",
            artifact_sha256="2e08133b90785380ec5a510e8defb059aa926ddf0395281b6c746bc9b90e2285",
            license="MIT",
            input_fps=1.0,
            feature_schema=cls._FEATURE_SCHEMA,
            notes="Ölçülebilir referans; learned candidate CNN yerine geçmez.",
        )


class MotionBaselineArtifact(BaseModel):
    """Baseline artifact'inin manifestten bağımsız strict biçimi."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    activity_scale: float = Field(gt=0)
    interaction_scale: float = Field(gt=0)
    feature_schema: tuple[str, ...] = MotionBaselineModel._FEATURE_SCHEMA
    license: Literal["Apache-2.0", "MIT"]


class CandidatePipeline:
    """Profile → score → hysteresis candidate pipeline."""

    def __init__(
        self,
        scorer: CandidateScorer | None = None,
        interval_config: IntervalConfig | None = None,
    ) -> None:
        self.scorer = scorer or MotionBaselineModel()
        self.interval_config = interval_config or IntervalConfig()

    def run(
        self,
        profile: list[MotionSample],
        *,
        analysis_id: str,
        video_id: str,
        duration_seconds: float,
    ) -> list[CandidateEvent]:
        samples = self.scorer.score(profile)
        return build_candidate_intervals(
            samples,
            analysis_id=analysis_id,
            video_id=video_id,
            duration_seconds=duration_seconds,
            model_id=self.scorer.model_id,
            config=self.interval_config,
        )


def load_manifest(path: Path, *, verify_artifact: bool = True) -> CandidateModelManifest:
    """Model manifest'i strict yükler ve varsayılan olarak artifact hash doğrular."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"candidate manifest okunamadı: {path}") from exc
    manifest = CandidateModelManifest.model_validate(payload)
    if verify_artifact:
        verify_manifest_artifact(path, manifest)
    return manifest


def verify_manifest_artifact(
    manifest_path: Path, manifest: CandidateModelManifest | None = None
) -> bool:
    """Manifest'teki repo-relative artifact yolunu ve SHA-256 değerini doğrular."""

    loaded = manifest or load_manifest(manifest_path, verify_artifact=False)
    artifact = resolve_manifest_artifact_path(manifest_path, loaded)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != loaded.artifact_sha256:
        raise ValueError("candidate artifact SHA-256 manifest ile eşleşmiyor")
    return True


def resolve_manifest_artifact_path(
    manifest_path: Path, manifest: CandidateModelManifest
) -> Path:
    """Manifest artifact'ini proje kökü içinde path-safe olarak çözer."""

    artifact_ref = PurePosixPath(manifest.artifact_path.replace("\\", "/"))
    if artifact_ref.is_absolute() or ".." in artifact_ref.parts:
        raise ValueError("candidate artifact yolu repo kökü içinde olmalı")
    manifest_file = manifest_path.resolve()
    repo_root = _find_repo_root(manifest_file)
    artifact = (repo_root / artifact_ref).resolve()
    if not artifact.is_relative_to(repo_root) or not artifact.is_file():
        raise ValueError(f"candidate artifact bulunamadı: {manifest.artifact_path}")
    return artifact


def load_candidate_scorer(manifest_path: Path) -> CandidateScorer:
    """Hash'i doğrulanmış manifestten kayıtlı local scorer'ı yükler.

    ONNX tipi henüz yalnız kabul edilmiş manifest türüdür; runtime adapter'i
    ayrı bir bağımlılık/lisans kapısından geçmeden etkinleştirilmez.
    """

    manifest = load_manifest(manifest_path)
    artifact_path = resolve_manifest_artifact_path(manifest_path, manifest)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"candidate artifact okunamadı: {artifact_path}") from exc
    if manifest.model_type == "motion_baseline":
        artifact = MotionBaselineArtifact.model_validate(payload)
        if artifact.model_id != manifest.model_id:
            raise ValueError("baseline artifact model_id manifest ile eşleşmiyor")
        return MotionBaselineModel(
            activity_scale=artifact.activity_scale,
            interaction_scale=artifact.interaction_scale,
            manifest=manifest,
        )
    if manifest.model_type == "temporal_cnn":
        artifact = TemporalCnnArtifact.model_validate(payload)
        if artifact.model_id != manifest.model_id:
            raise ValueError("temporal CNN artifact model_id manifest ile eşleşmiyor")
        if artifact.license != manifest.license:
            raise ValueError("temporal CNN artifact lisansı manifest ile eşleşmiyor")
        return TemporalCnnCandidateModel(artifact)
    raise ValueError("onnx_cnn scorer için lisans doğrulanmış runtime adapter'i henüz kayıtlı değil")


def _find_repo_root(start: Path) -> Path:
    for parent in (start.parent, *start.parents):
        if (parent / "PROJECT_SPEC.md").is_file() and (parent / "backend").is_dir():
            return parent
    raise ValueError("candidate manifest proje kökü altında değil")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


__all__ = [
    "CandidateModelManifest",
    "CandidatePipeline",
    "CandidateScorer",
    "MotionBaselineArtifact",
    "MotionBaselineModel",
    "load_candidate_scorer",
    "load_manifest",
    "resolve_manifest_artifact_path",
    "verify_manifest_artifact",
]
