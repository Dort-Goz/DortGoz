from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _relative_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    if path.is_absolute() or windows.is_absolute() or windows.drive or ".." in path.parts:
        raise ValueError("path göreli ve çalışma kökü içinde olmalıdır")
    return path.as_posix()


class KeyframeRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(min_length=1)
    timestamp: float = Field(ge=0)
    frame_path: str = Field(min_length=1)
    hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_reason: str = Field(min_length=1)
    quality_score: float | None = Field(default=None, ge=0, le=1)

    @field_validator("frame_path")
    @classmethod
    def validate_frame_path(cls, value: str) -> str:
        return _relative_path(value)


class ContextClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    clip_start: float = Field(ge=0)
    clip_end: float = Field(gt=0)
    clip_path: str = Field(min_length=1)
    frame_count: int = Field(gt=0)
    fps: float = Field(gt=0)
    hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expanded: bool = False

    @field_validator("clip_path")
    @classmethod
    def validate_clip_path(cls, value: str) -> str:
        return _relative_path(value)

    @model_validator(mode="after")
    def clip_times_are_ordered(self) -> ContextClip:
        if self.clip_start >= self.clip_end:
            raise ValueError("clip_start, clip_end değerinden küçük olmalıdır")
        return self


class TrackObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(min_length=1)
    timestamp: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)


class SignalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str = Field(min_length=1)
    timestamp: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)


class DenseAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    person_tracks: list[TrackObservation] = Field(default_factory=list)
    pose_signals: list[SignalObservation] = Field(default_factory=list)
    motion_signals: list[SignalObservation] = Field(default_factory=list)
    object_signals: list[SignalObservation] = Field(default_factory=list)
    cv_confidence: float = Field(ge=0, le=1)
    image_quality: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    tool_version: str = Field(min_length=1)
