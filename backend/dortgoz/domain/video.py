"""Video ingest domain modelleri ve typed hata sözleşmesi."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VideoErrorCode(StrEnum):
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_CONTAINER = "UNSUPPORTED_CONTAINER"
    UNSUPPORTED_CODEC = "UNSUPPORTED_CODEC"
    DECODE_FAILED = "DECODE_FAILED"
    INVALID_DURATION = "INVALID_DURATION"
    INVALID_FPS = "INVALID_FPS"
    PATH_REJECTED = "PATH_REJECTED"


class VideoIngestError(RuntimeError):
    """Ingest katmanından API ErrorEnvelope'a çevrilecek typed hata."""

    def __init__(self, code: VideoErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class VideoProbe(BaseModel):
    """ffprobe çıktısının uygulamanın kullandığı normalize edilmiş alt kümesi."""

    model_config = ConfigDict(extra="forbid")

    container: str = Field(min_length=1)
    codec: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    has_audio: bool
    time_base: str = Field(min_length=1)
    variable_fps: bool = False
    decode_error_ratio: float = Field(default=0.0, ge=0, le=1)


class VideoMetadata(BaseModel):
    """Güvenli storage ve ffprobe doğrulamasından geçmiş video kaydı."""

    model_config = ConfigDict(extra="forbid")

    video_id: str = Field(min_length=8)
    original_filename: str = Field(min_length=1)
    stored_filename: str = Field(min_length=1)
    media_path: str = Field(min_length=1)
    file_size_bytes: int = Field(gt=0)
    file_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    container: str = Field(min_length=1)
    codec: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    has_audio: bool
    time_base: str = Field(min_length=1)
    variable_fps: bool = False
    decode_error_ratio: float = Field(default=0.0, ge=0, le=1)
    black_frame_ratio: float | None = Field(default=None, ge=0, le=1)
    freeze_ratio: float | None = Field(default=None, ge=0, le=1)
    processable: bool = True
    warnings: list[str] = Field(default_factory=list)
    error_code: VideoErrorCode | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("video_id")
    @classmethod
    def video_id_must_be_uuid(cls, value: str) -> str:
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("video_id geçerli UUID olmalıdır") from exc

    @field_validator("original_filename", "stored_filename")
    @classmethod
    def filename_must_be_basename(cls, value: str) -> str:
        if PurePosixPath(value).name != value or PureWindowsPath(value).name != value:
            raise ValueError("dosya adı path parçası içeremez")
        if value in {".", ".."}:
            raise ValueError("geçersiz dosya adı")
        return value

    @field_validator("media_path")
    @classmethod
    def media_path_must_be_relative(cls, value: str) -> str:
        posix = PurePosixPath(value.replace("\\", "/"))
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
            raise ValueError("media_path göreli ve kök içinde olmalıdır")
        return posix.as_posix()

    @model_validator(mode="after")
    def processability_is_consistent(self) -> VideoMetadata:
        if not self.processable and self.error_code is None:
            raise ValueError("işlenemeyen videoda error_code zorunludur")
        if self.processable and self.error_code is not None:
            raise ValueError("işlenebilir videoda error_code bulunamaz")
        if PurePosixPath(self.stored_filename).stem != self.video_id:
            raise ValueError("stored_filename UUID tabanlı video_id ile eşleşmelidir")
        return self
