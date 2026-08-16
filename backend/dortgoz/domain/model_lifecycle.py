"""Controlled D-FINE training and model-registry contracts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DfineArchitecture(StrEnum):
    NANO = "dfine_n"
    SMALL = "dfine_s"


class TrainingJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_STOPPED = "budget_stopped"


class ModelStage(StrEnum):
    CANDIDATE = "candidate"
    CHAMPION = "champion"
    RETIRED = "retired"
    REVOKED = "revoked"


class DfineTrainingPolicy(BaseModel):
    """Hard resource and data limits for one local training worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1)
    minimum_verified_frames: int = Field(default=100, ge=2)
    minimum_train_frames: int = Field(default=80, ge=1)
    minimum_validation_frames: int = Field(default=20, ge=1)
    minimum_source_videos: int = Field(default=10, ge=2)
    maximum_epochs: int = Field(default=20, ge=1, le=500)
    maximum_batch_size: int = Field(default=4, ge=1, le=128)
    maximum_workers: int = Field(default=4, ge=0, le=64)
    maximum_gpu_minutes_per_job: int = Field(default=90, ge=1, le=1440)
    maximum_gpu_minutes_per_day: int = Field(default=120, ge=1, le=1440)
    allowed_architectures: list[DfineArchitecture] = Field(
        default_factory=lambda: [DfineArchitecture.NANO, DfineArchitecture.SMALL],
        min_length=1,
    )

    @model_validator(mode="after")
    def limits_are_consistent(self) -> DfineTrainingPolicy:
        if self.minimum_train_frames + self.minimum_validation_frames > (
            self.minimum_verified_frames
        ):
            raise ValueError("split alt sınırları toplam kare alt sınırını aşamaz")
        if self.maximum_gpu_minutes_per_job > self.maximum_gpu_minutes_per_day:
            raise ValueError("iş GPU bütçesi günlük GPU bütçesini aşamaz")
        if len(set(self.allowed_architectures)) != len(self.allowed_architectures):
            raise ValueError("allowed_architectures tekrar eden değer içeremez")
        return self


class PromotionPolicy(BaseModel):
    """Fail-closed gate used before a candidate can become champion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1)
    minimum_map_50_95: float = Field(ge=0, le=1)
    minimum_critical_recall: float = Field(ge=0, le=1)
    maximum_false_alarms_per_hour: float = Field(ge=0, allow_inf_nan=False)
    maximum_p95_latency_ms: float = Field(gt=0, allow_inf_nan=False)
    maximum_peak_memory_mb: int = Field(gt=0)
    minimum_repetitions: int = Field(default=3, ge=1)
    maximum_critical_recall_drop: float = Field(default=0, ge=0, le=1)
    maximum_false_alarm_increase: float = Field(default=0, ge=0, allow_inf_nan=False)
    maximum_latency_increase_ratio: float = Field(default=0.10, ge=0, le=10)


class TrainingJob(BaseModel):
    """Persistent record for one bounded, reproducible D-FINE run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_version: Literal["1.0.0"] = "1.0.0"
    job_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    export_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    export_ref: str = Field(min_length=1)
    selection_policy_version: str | None = None
    selection_policy_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    selection_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    architecture: DfineArchitecture
    category_names: list[str] = Field(min_length=1)
    verified_frame_count: int = Field(gt=0)
    train_frame_count: int = Field(gt=0)
    validation_frame_count: int = Field(gt=0)
    source_video_count: int = Field(gt=0)
    box_count: int = Field(gt=0)
    dfine_repository_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    epochs: int = Field(ge=1, le=500)
    batch_size: int = Field(ge=1, le=128)
    workers: int = Field(default=2, ge=0, le=64)
    gpu_index: int = Field(default=0, ge=0, le=31)
    max_gpu_minutes: int = Field(ge=1, le=1440)
    daily_gpu_minutes: int = Field(ge=1, le=1440)
    status: TrainingJobStatus = TrainingJobStatus.QUEUED
    requested_by: str = Field(min_length=1, max_length=120)
    output_ref: str = Field(min_length=1)
    checkpoint_ref: str | None = None
    checkpoint_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{3,80}$")
    error_message: str | None = Field(default=None, max_length=4000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> TrainingJob:
        for value, field_name in (
            (self.export_ref, "export_ref"),
            (self.output_ref, "output_ref"),
            (self.checkpoint_ref, "checkpoint_ref"),
        ):
            if value is not None and not _safe_reference(value):
                raise ValueError(f"{field_name} güvenli göreli POSIX yol olmalıdır")
        if len(self.category_names) != len(set(self.category_names)):
            raise ValueError("category_names tekrar eden değer içeremez")
        selection_values = (
            self.selection_policy_version,
            self.selection_policy_fingerprint,
            self.selection_fingerprint,
        )
        if any(value is None for value in selection_values) and any(
            value is not None for value in selection_values
        ):
            raise ValueError("training job seçim politika ve fingerprint değerlerini birlikte taşır")
        if self.train_frame_count + self.validation_frame_count != self.verified_frame_count:
            raise ValueError("training job split kare sayıları toplam kare sayısıyla eşleşmiyor")
        if self.max_gpu_minutes > self.daily_gpu_minutes:
            raise ValueError("training job GPU bütçesi günlük GPU bütçesini aşamaz")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at created_at değerinden önce olamaz")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at created_at değerinden önce olamaz")
        if self.finished_at is not None and (
            self.started_at is None or self.finished_at < self.started_at
        ):
            raise ValueError("finished_at geçerli started_at değerinden sonra olmalıdır")

        if self.status == TrainingJobStatus.QUEUED:
            if any(
                value is not None
                for value in (
                    self.started_at,
                    self.finished_at,
                    self.checkpoint_ref,
                    self.checkpoint_sha256,
                    self.error_code,
                    self.error_message,
                )
            ) or self.elapsed_seconds != 0:
                raise ValueError("queued training job sonuç veya çalışma bilgisi taşıyamaz")
        elif self.status == TrainingJobStatus.RUNNING:
            if self.started_at is None or self.finished_at is not None:
                raise ValueError("running training job yalnız started_at taşımalıdır")
            if any(
                value is not None
                for value in (
                    self.checkpoint_ref,
                    self.checkpoint_sha256,
                    self.error_code,
                    self.error_message,
                )
            ):
                raise ValueError("running training job sonuç bilgisi taşıyamaz")
        elif self.status == TrainingJobStatus.SUCCEEDED:
            if (
                self.started_at is None
                or self.finished_at is None
                or self.checkpoint_ref is None
                or self.checkpoint_sha256 is None
                or self.error_code is not None
                or self.error_message is not None
            ):
                raise ValueError("succeeded training job doğrulanmış checkpoint gerektirir")
        elif (
            self.started_at is None
            or self.finished_at is None
            or self.error_code is None
            or self.error_message is None
            or self.checkpoint_ref is not None
            or self.checkpoint_sha256 is not None
        ):
            raise ValueError("başarısız training job zaman ve hata bilgisi gerektirir")
        return self


class ModelEvaluation(BaseModel):
    """End-to-end evaluation bound to one immutable candidate checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_version: Literal["1.0.0"] = "1.0.0"
    evaluation_id: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    map_50_95: float = Field(ge=0, le=1)
    map_50: float = Field(ge=0, le=1)
    critical_recall: float = Field(ge=0, le=1)
    false_alarms_per_hour: float = Field(ge=0, allow_inf_nan=False)
    p95_latency_ms: float = Field(gt=0, allow_inf_nan=False)
    peak_memory_mb: int = Field(gt=0)
    repetitions: int = Field(ge=1)
    shadow_passed: bool
    evaluator: str = Field(min_length=1, max_length=120)
    measured_at: datetime
    detector_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    e2e_artifact_sha256s: list[str] = Field(min_length=3)
    metrics_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def timestamps_are_aware(self) -> ModelEvaluation:
        if self.measured_at.utcoffset() is None:
            raise ValueError("measured_at saat dilimi içermelidir")
        if len(self.e2e_artifact_sha256s) != len(set(self.e2e_artifact_sha256s)):
            raise ValueError("e2e artifact SHA-256 değerleri benzersiz olmalıdır")
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in self.e2e_artifact_sha256s
        ):
            raise ValueError("e2e artifact SHA-256 değeri geçersiz")
        return self


class ModelVersion(BaseModel):
    """Candidate/champion registry row. Model weights remain outside Git."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_version: Literal["1.0.0"] = "1.0.0"
    model_version_id: str = Field(min_length=1)
    training_job_id: str = Field(min_length=1)
    architecture: DfineArchitecture
    checkpoint_ref: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    export_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    dfine_repository_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    stage: ModelStage = ModelStage.CANDIDATE
    evaluation: ModelEvaluation | None = None
    promotion_policy_version: str | None = None
    approved_by: str | None = Field(default=None, min_length=1, max_length=120)
    promotion_reason: str | None = Field(default=None, min_length=1, max_length=4000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    promoted_at: datetime | None = None
    retired_at: datetime | None = None
    revoked_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def stage_is_consistent(self) -> ModelVersion:
        if not _safe_reference(self.checkpoint_ref):
            raise ValueError("checkpoint_ref güvenli göreli POSIX yol olmalıdır")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at created_at değerinden önce olamaz")
        if self.evaluation is not None and (
            self.evaluation.checkpoint_sha256 != self.checkpoint_sha256
        ):
            raise ValueError("evaluation checkpoint SHA-256 ile eşleşmiyor")
        if self.stage == ModelStage.CANDIDATE:
            if any(
                value is not None
                for value in (
                    self.promotion_policy_version,
                    self.approved_by,
                    self.promotion_reason,
                    self.promoted_at,
                    self.retired_at,
                    self.revoked_at,
                )
            ):
                raise ValueError("candidate model terfi bilgisi taşıyamaz")
        elif self.stage == ModelStage.CHAMPION:
            if (
                self.evaluation is None
                or self.promotion_policy_version is None
                or self.approved_by is None
                or self.promotion_reason is None
                or self.promoted_at is None
                or self.retired_at is not None
                or self.revoked_at is not None
            ):
                raise ValueError("champion model evaluation ve insan onayı gerektirir")
        elif self.stage == ModelStage.RETIRED:
            if (
                self.evaluation is None
                or self.promotion_policy_version is None
                or self.approved_by is None
                or self.promotion_reason is None
                or self.promoted_at is None
                or self.retired_at is None
                or self.revoked_at is not None
            ):
                raise ValueError("retired model önceki champion geçmişini taşımalıdır")
        elif self.revoked_at is None:
            raise ValueError("revoked model revoked_at gerektirir")
        return self


def _safe_reference(value: str) -> bool:
    posix = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ".." not in posix.parts
        and value == posix.as_posix()
    )


__all__ = [
    "DfineArchitecture",
    "DfineTrainingPolicy",
    "ModelEvaluation",
    "ModelStage",
    "ModelVersion",
    "PromotionPolicy",
    "TrainingJob",
    "TrainingJobStatus",
]
