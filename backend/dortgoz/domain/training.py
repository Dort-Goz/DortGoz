

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .dataset import DatasetSplit


class FrameReviewResult(StrEnum):
    VERIFIED_BOXES = "verified_boxes"
    VERIFIED_NO_TARGET_OBJECTS = "verified_no_target_objects"


class TrainingSampleStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    VERIFIED = "verified"
    REVOKED = "revoked"


class VerifiedBoundingBox(BaseModel):


    model_config = ConfigDict(extra="forbid", frozen=True)

    category_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)
    width: float = Field(gt=0, allow_inf_nan=False)
    height: float = Field(gt=0, allow_inf_nan=False)
    is_crowd: bool = False


class TrainingFrameReview(BaseModel):


    model_config = ConfigDict(extra="forbid", frozen=True)

    annotation_version: Literal["1.0.0"] = "1.0.0"
    annotation_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_video_id: str = Field(min_length=1)
    source_video_ref: str = Field(min_length=1)
    frame_ref: str = Field(min_length=1)
    frame_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_size_bytes: int = Field(gt=0)
    timestamp_seconds: float = Field(ge=0, allow_inf_nan=False)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    split: DatasetSplit
    review_result: FrameReviewResult
    boxes: list[VerifiedBoundingBox] = Field(default_factory=list)
    human_verified: Literal[True]
    reviewer: str = Field(min_length=1)
    annotation_tool: str = Field(min_length=1)
    reviewed_at: datetime

    @model_validator(mode="after")
    def review_is_safe_and_consistent(self) -> TrainingFrameReview:
        for value, field_name in (
            (self.source_video_ref, "source_video_ref"),
            (self.frame_ref, "frame_ref"),
        ):
            if not _safe_reference(value):
                raise ValueError(f"{field_name} güvenli göreli POSIX yol olmalıdır")
        if self.split not in {DatasetSplit.TRAIN, DatasetSplit.VALIDATION}:
            raise ValueError("D-FINE eğitim karesi train veya validation olmalıdır")
        if self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at saat dilimi içermelidir")
        if self.reviewer.strip() != self.reviewer or not self.reviewer.strip():
            raise ValueError("reviewer boşluk içeremez veya boş olamaz")
        if self.annotation_tool.strip() != self.annotation_tool or not self.annotation_tool.strip():
            raise ValueError("annotation_tool boşluk içeremez veya boş olamaz")
        if self.review_result == FrameReviewResult.VERIFIED_BOXES and not self.boxes:
            raise ValueError("verified_boxes kararı en az bir kutu gerektirir")
        if self.review_result == FrameReviewResult.VERIFIED_NO_TARGET_OBJECTS and self.boxes:
            raise ValueError("verified_no_target_objects kararı kutu içeremez")
        for box in self.boxes:
            if box.x + box.width > self.image_width or box.y + box.height > self.image_height:
                raise ValueError("bounding box görüntü sınırları içinde olmalıdır")
        return self


class TrainingSample(BaseModel):


    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_version: Literal["1.0.0"] = "1.0.0"
    sample_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_revision: int = Field(ge=1)
    review_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    source_video_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_id: str = Field(min_length=1)
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_video_id: str = Field(min_length=1)
    source_video_ref: str = Field(min_length=1)
    split: DatasetSplit
    timestamp_seconds: float = Field(ge=0, allow_inf_nan=False)
    selection_reason: str = Field(min_length=1)
    frame_ref: str = Field(min_length=1)
    frame_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_size_bytes: int = Field(gt=0)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    status: TrainingSampleStatus
    prepared_by: str = Field(min_length=1)
    frame_review: TrainingFrameReview | None = None
    revoked_by_approval_id: str | None = None
    invalidated_by_review_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def sample_is_safe_and_consistent(self) -> TrainingSample:
        for value, field_name in (
            (self.source_video_ref, "source_video_ref"),
            (self.frame_ref, "frame_ref"),
        ):
            if not _safe_reference(value):
                raise ValueError(f"{field_name} güvenli göreli POSIX yol olmalıdır")
        if self.split not in {DatasetSplit.TRAIN, DatasetSplit.VALIDATION}:
            raise ValueError("training sample train veya validation olmalıdır")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at created_at değerinden önce olamaz")
        if self.status == TrainingSampleStatus.PENDING_REVIEW:
            if (
                self.frame_review is not None
                or self.revoked_by_approval_id is not None
                or self.invalidated_by_review_id is not None
            ):
                raise ValueError("pending training sample review veya revoke kaydı taşıyamaz")
        elif self.status == TrainingSampleStatus.VERIFIED:
            if (
                self.frame_review is None
                or self.revoked_by_approval_id is not None
                or self.invalidated_by_review_id is not None
            ):
                raise ValueError("verified training sample yalnız frame review taşımalıdır")
        elif (self.revoked_by_approval_id is None) == (self.invalidated_by_review_id is None):
            raise ValueError("revoked training sample tek bir geçersiz kılma nedeni taşımalıdır")
        if self.frame_review is not None:
            expected = {
                "annotation_id": self.sample_id,
                "dataset_id": self.dataset_id,
                "dataset_fingerprint": self.dataset_fingerprint,
                "dataset_video_id": self.dataset_video_id,
                "source_video_ref": self.source_video_ref,
                "frame_ref": self.frame_ref,
                "frame_sha256": self.frame_sha256,
                "frame_size_bytes": self.frame_size_bytes,
                "timestamp_seconds": self.timestamp_seconds,
                "image_width": self.image_width,
                "image_height": self.image_height,
                "split": self.split,
            }
            actual = self.frame_review.model_dump(include=set(expected))
            if actual != expected:
                raise ValueError("frame review training sample provenance alanlarıyla eşleşmiyor")
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
