"""Build one hash-bound model evaluation from detector and shadow artifacts."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..benchmark_metrics import e2e_metrics
from ..domain.dataset import DatasetUse, OfflineDatasetManifest
from ..domain.model_lifecycle import ModelStage, ModelVersion
from .dataset_manifest import sha256_file

_MAX_DETECTOR_REPORT_BYTES = 1024 * 1024
_MAX_E2E_ARTIFACT_BYTES = 64 * 1024 * 1024


class EvaluationReportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DetectorEvaluationArtifact(BaseModel):
    """Normalized COCO evaluation emitted by the D-FINE evaluation adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_version: Literal["1.0.0"] = "1.0.0"
    candidate_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    map_50_95: float = Field(ge=0, le=1)
    map_50: float = Field(ge=0, le=1)
    measured_at: datetime
    evaluation_plan_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_log_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def metrics_are_consistent(self) -> DetectorEvaluationArtifact:
        if self.measured_at.utcoffset() is None:
            raise ValueError("detector measured_at saat dilimi içermelidir")
        if self.map_50 < self.map_50_95:
            raise ValueError("mAP50 değeri mAP50-95 değerinden küçük olamaz")
        return self


class ShadowEvaluationRecord(BaseModel):
    """One video-level row from a candidate run executed in shadow mode."""

    model_config = ConfigDict(extra="allow", frozen=True)

    evaluation_run_id: str = Field(min_length=1)
    candidate_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    shadow_mode: Literal[True]
    measured_at: datetime
    expected_critical: bool = False
    confirmed_critical: bool = False
    is_normal: bool = False
    duration_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    false_alarm: bool = False
    latency_ms: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    ram_mb: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    vram_mb: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def row_is_measurable(self) -> ShadowEvaluationRecord:
        if self.measured_at.utcoffset() is None:
            raise ValueError("shadow measured_at saat dilimi içermelidir")
        if self.confirmed_critical and not self.expected_critical and not self.false_alarm:
            raise ValueError("beklenmeyen kritik tespit false_alarm olarak işaretlenmelidir")
        if self.is_normal and self.duration_seconds <= 0:
            raise ValueError("normal shadow kaydı pozitif duration_seconds gerektirir")
        return self


class DfineEvaluationReport(BaseModel):
    """Exact payload accepted by ``ModelRegistryService.record_evaluation``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    test_dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    map_50_95: float = Field(ge=0, le=1)
    map_50: float = Field(ge=0, le=1)
    critical_recall: float = Field(ge=0, le=1)
    false_alarms_per_hour: float = Field(ge=0, allow_inf_nan=False)
    p95_latency_ms: float = Field(gt=0, allow_inf_nan=False)
    peak_memory_mb: int = Field(gt=0)
    repetitions: int = Field(ge=3)
    shadow_passed: Literal[True]
    evaluator: str = Field(min_length=1, max_length=120)
    measured_at: datetime
    detector_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    e2e_artifact_sha256s: list[str] = Field(min_length=3)

    @model_validator(mode="after")
    def sources_are_verifiable(self) -> DfineEvaluationReport:
        if self.measured_at.utcoffset() is None:
            raise ValueError("measured_at saat dilimi içermelidir")
        if len(self.e2e_artifact_sha256s) != len(set(self.e2e_artifact_sha256s)):
            raise ValueError("e2e artifact SHA-256 değerleri benzersiz olmalıdır")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in self.e2e_artifact_sha256s):
            raise ValueError("e2e artifact SHA-256 değeri geçersiz")
        return self


def build_dfine_evaluation_report(
    *,
    candidate: ModelVersion,
    test_dataset_manifest: OfflineDatasetManifest,
    detector_report_path: Path,
    e2e_artifact_paths: list[Path],
    evaluator: str,
    output_path: Path | None = None,
) -> DfineEvaluationReport:
    if candidate.stage != ModelStage.CANDIDATE or candidate.evaluation is not None:
        raise EvaluationReportError(
            "MODEL_NOT_EVALUATABLE",
            "yalnız değerlendirilmemiş candidate model için rapor üretilebilir",
        )
    if not (
        DatasetUse.EVALUATION in test_dataset_manifest.allowed_uses
        or DatasetUse.BENCHMARK in test_dataset_manifest.allowed_uses
    ):
        raise EvaluationReportError(
            "TEST_DATASET_USE_REJECTED",
            "test dataset evaluation veya benchmark kullanımına açık değil",
        )
    if len(e2e_artifact_paths) < 3:
        raise EvaluationReportError(
            "EVALUATION_REPETITIONS_MISSING",
            "model değerlendirmesi en az üç ayrı shadow artifact gerektirir",
        )

    detector = _load_detector_report(detector_report_path)
    detector_sha = sha256_file(detector_report_path.resolve())
    artifact_hashes: list[str] = []
    all_records: list[ShadowEvaluationRecord] = []
    run_ids: set[str] = set()
    for artifact_path in e2e_artifact_paths:
        records = _load_shadow_records(artifact_path)
        artifact_hash = sha256_file(artifact_path.resolve())
        if artifact_hash in artifact_hashes:
            raise EvaluationReportError(
                "DUPLICATE_EVALUATION_ARTIFACT",
                "aynı shadow artifact birden fazla tekrar olarak kullanılamaz",
            )
        artifact_hashes.append(artifact_hash)
        artifact_run_ids = {record.evaluation_run_id for record in records}
        if len(artifact_run_ids) != 1:
            raise EvaluationReportError(
                "SHADOW_RUN_ID_INVALID",
                f"shadow artifact tek evaluation_run_id taşımalıdır: {artifact_path}",
            )
        run_id = next(iter(artifact_run_ids))
        if run_id in run_ids:
            raise EvaluationReportError(
                "DUPLICATE_EVALUATION_RUN",
                f"evaluation_run_id tekrar ediyor: {run_id}",
            )
        run_ids.add(run_id)
        _validate_run_coverage(records, artifact_path)
        all_records.extend(records)

    expected_checkpoint = candidate.checkpoint_sha256
    expected_dataset = test_dataset_manifest.dataset_fingerprint
    if detector.candidate_checkpoint_sha256 != expected_checkpoint:
        raise EvaluationReportError(
            "DETECTOR_CHECKPOINT_MISMATCH",
            "detector raporu candidate checkpoint ile eşleşmiyor",
        )
    if detector.test_dataset_fingerprint != expected_dataset:
        raise EvaluationReportError(
            "DETECTOR_DATASET_MISMATCH",
            "detector raporu test dataset fingerprint ile eşleşmiyor",
        )
    for record in all_records:
        if record.candidate_checkpoint_sha256 != expected_checkpoint:
            raise EvaluationReportError(
                "SHADOW_CHECKPOINT_MISMATCH",
                f"shadow kaydı candidate checkpoint ile eşleşmiyor: {record.evaluation_run_id}",
            )
        if record.test_dataset_fingerprint != expected_dataset:
            raise EvaluationReportError(
                "SHADOW_DATASET_MISMATCH",
                f"shadow kaydı test dataset ile eşleşmiyor: {record.evaluation_run_id}",
            )
        if record.code_revision != detector.code_revision:
            raise EvaluationReportError(
                "EVALUATION_CODE_REVISION_MISMATCH",
                "detector ve shadow artifact code revision değerleri eşleşmiyor",
            )

    metrics = e2e_metrics([record.model_dump(mode="json") for record in all_records])
    if metrics["critical_total"] <= 0 or metrics["normal_seconds"] <= 0:
        raise EvaluationReportError(
            "EVALUATION_COVERAGE_INVALID",
            "değerlendirme kritik ve normal video kapsamı gerektirir",
        )
    if metrics["p95_latency_ms"] is None or metrics["peak_memory_mb"] is None:
        raise EvaluationReportError(
            "EVALUATION_RESOURCE_METRICS_MISSING",
            "shadow artifact gecikme ve bellek ölçümü gerektirir",
        )

    measured_at = max(
        detector.measured_at,
        *(record.measured_at for record in all_records),
    )
    report = DfineEvaluationReport(
        test_dataset_fingerprint=expected_dataset,
        code_revision=detector.code_revision,
        map_50_95=detector.map_50_95,
        map_50=detector.map_50,
        critical_recall=metrics["critical_recall"],
        false_alarms_per_hour=metrics["false_alarms_per_hour"],
        p95_latency_ms=metrics["p95_latency_ms"],
        peak_memory_mb=math.ceil(metrics["peak_memory_mb"]),
        repetitions=len(run_ids),
        shadow_passed=True,
        evaluator=evaluator,
        measured_at=measured_at,
        detector_report_sha256=detector_sha,
        e2e_artifact_sha256s=artifact_hashes,
    )
    if output_path is not None:
        _atomic_write_report(output_path, report)
    return report


def _load_detector_report(path: Path) -> DetectorEvaluationArtifact:
    raw = _read_file(path, maximum_bytes=_MAX_DETECTOR_REPORT_BYTES)
    try:
        return DetectorEvaluationArtifact.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        raise EvaluationReportError(
            "DETECTOR_REPORT_INVALID", f"detector raporu geçersiz: {path}: {exc}"
        ) from exc


def _load_shadow_records(path: Path) -> list[ShadowEvaluationRecord]:
    raw = _read_file(path, maximum_bytes=_MAX_E2E_ARTIFACT_BYTES)
    records: list[ShadowEvaluationRecord] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload: Any = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("JSON object değil")
            records.append(ShadowEvaluationRecord.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
            raise EvaluationReportError(
                "SHADOW_ARTIFACT_INVALID",
                f"shadow artifact satırı geçersiz: {path}:{line_number}: {exc}",
            ) from exc
    if not records:
        raise EvaluationReportError("SHADOW_ARTIFACT_EMPTY", f"shadow artifact boş: {path}")
    return records


def _validate_run_coverage(records: list[ShadowEvaluationRecord], path: Path) -> None:
    if not any(record.expected_critical for record in records):
        raise EvaluationReportError(
            "SHADOW_CRITICAL_COVERAGE_MISSING",
            f"shadow tekrarı kritik video içermiyor: {path}",
        )
    if not any(record.is_normal and record.duration_seconds > 0 for record in records):
        raise EvaluationReportError(
            "SHADOW_NORMAL_COVERAGE_MISSING",
            f"shadow tekrarı normal video içermiyor: {path}",
        )


def _read_file(path: Path, *, maximum_bytes: int) -> str:
    if path.is_symlink():
        raise EvaluationReportError("EVALUATION_FILE_UNSAFE", f"symlink reddedildi: {path}")
    resolved = path.resolve()
    if not resolved.is_file():
        raise EvaluationReportError("EVALUATION_FILE_MISSING", f"dosya bulunamadı: {path}")
    if resolved.stat().st_size > maximum_bytes:
        raise EvaluationReportError("EVALUATION_FILE_TOO_LARGE", f"dosya çok büyük: {path}")
    try:
        return resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvaluationReportError(
            "EVALUATION_FILE_UNREADABLE", f"dosya okunamadı: {path}: {exc}"
        ) from exc


def _atomic_write_report(path: Path, report: DfineEvaluationReport) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


__all__ = [
    "DetectorEvaluationArtifact",
    "DfineEvaluationReport",
    "EvaluationReportError",
    "ShadowEvaluationRecord",
    "build_dfine_evaluation_report",
]
