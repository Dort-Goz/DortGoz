

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DatasetSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    UNASSIGNED = "unassigned"


class DatasetUse(StrEnum):
    TRAINING = "training"
    EVALUATION = "evaluation"
    BENCHMARK = "benchmark"


class DatasetLicenseStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class DatasetVideoRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_video_id: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_label: str = Field(min_length=1)
    split: DatasetSplit
    file_size_bytes: int = Field(gt=0)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    annotation_ref: str | None = None
    annotation_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    allowed_uses: list[DatasetUse] = Field(min_length=1)

    @model_validator(mode="after")
    def record_is_safe_and_consistent(self) -> DatasetVideoRecord:
        posix = PurePosixPath(self.source_ref.replace("\\", "/"))
        windows = PureWindowsPath(self.source_ref)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or windows.drive
            or ".." in posix.parts
            or self.source_ref != posix.as_posix()
        ):
            raise ValueError("source_ref güvenli göreli POSIX yol olmalıdır")
        if (self.annotation_ref is None) != (self.annotation_sha256 is None):
            raise ValueError("annotation_ref ve annotation_sha256 birlikte bulunmalıdır")
        if len(set(self.allowed_uses)) != len(self.allowed_uses):
            raise ValueError("allowed_uses tekrar eden değer içeremez")
        if self.split == DatasetSplit.TEST and DatasetUse.TRAINING in self.allowed_uses:
            raise ValueError("test videosu training kullanımına açılamaz")
        return self


class OfflineDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: Literal["1.0.0"] = "1.0.0"
    dataset_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    license_status: DatasetLicenseStatus
    license_id: str | None = None
    redistribution_allowed: bool
    training_allowed: bool
    allowed_uses: list[DatasetUse] = Field(min_length=1)
    entries: list[DatasetVideoRecord] = Field(min_length=1)
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def manifest_policy_and_fingerprint_are_valid(self) -> OfflineDatasetManifest:
        if len(set(self.allowed_uses)) != len(self.allowed_uses):
            raise ValueError("manifest allowed_uses tekrar eden değer içeremez")
        if self.license_status == DatasetLicenseStatus.UNVERIFIED:
            if self.license_id is not None:
                raise ValueError("unverified dataset doğrulanmış license_id taşıyamaz")
            if self.redistribution_allowed or self.training_allowed:
                raise ValueError("unverified dataset dağıtım veya training izni alamaz")
        if self.training_allowed:
            if self.license_status != DatasetLicenseStatus.VERIFIED:
                raise ValueError("training için doğrulanmış dataset lisansı zorunludur")
            if self.license_id not in {"Apache-2.0", "MIT"}:
                raise ValueError("training dataset lisansı Apache-2.0 veya MIT olmalıdır")
            if DatasetUse.TRAINING not in self.allowed_uses:
                raise ValueError("training_allowed manifest training kullanımını içermelidir")

        ids = [item.dataset_video_id for item in self.entries]
        refs = [item.source_ref for item in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("dataset_video_id benzersiz olmalıdır")
        if len(refs) != len(set(refs)):
            raise ValueError("source_ref benzersiz olmalıdır")
        for entry in self.entries:
            if not set(entry.allowed_uses).issubset(self.allowed_uses):
                raise ValueError("video allowed_uses manifest politikasını aşamaz")

        hash_splits: dict[str, set[DatasetSplit]] = {}
        for entry in self.entries:
            hash_splits.setdefault(entry.file_sha256, set()).add(entry.split)
        if any(len(splits) > 1 for splits in hash_splits.values()):
            raise ValueError("aynı video içeriği birden fazla split içinde bulunamaz")
        if self.dataset_fingerprint != calculate_dataset_fingerprint(self.entries):
            raise ValueError("dataset_fingerprint entries ile eşleşmiyor")
        return self


def calculate_dataset_fingerprint(entries: list[DatasetVideoRecord]) -> str:
    payload = [
        {
            "dataset_video_id": item.dataset_video_id,
            "source_ref": item.source_ref,
            "source_label": item.source_label,
            "split": item.split.value,
            "file_size_bytes": item.file_size_bytes,
            "file_sha256": item.file_sha256,
            "annotation_ref": item.annotation_ref,
            "annotation_sha256": item.annotation_sha256,
            "allowed_uses": sorted(value.value for value in item.allowed_uses),
        }
        for item in sorted(entries, key=lambda value: value.source_ref)
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
