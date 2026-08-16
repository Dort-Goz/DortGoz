"""Human-verified object-detection training contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .dataset import DatasetSplit


class FrameReviewResult(StrEnum):
    VERIFIED_BOXES = "verified_boxes"
    VERIFIED_NO_TARGET_OBJECTS = "verified_no_target_objects"


class VerifiedBoundingBox(BaseModel):
    """One reviewed COCO-style pixel-space box."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)
    width: float = Field(gt=0, allow_inf_nan=False)
    height: float = Field(gt=0, allow_inf_nan=False)
    is_crowd: bool = False


class TrainingFrameReview(BaseModel):
    """A frame review bound to one immutable dataset manifest and source video."""

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
