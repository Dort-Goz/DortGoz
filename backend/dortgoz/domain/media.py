

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IncidentMedia(BaseModel):


    model_config = ConfigDict(extra="forbid", frozen=True)

    media_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    event_revision: int = Field(ge=1)
    source_refs: list[str] = Field(min_length=1)
    source_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    clip_ref: str = Field(min_length=1, max_length=500)
    thumbnail_ref: str = Field(min_length=1, max_length=500)
    clip_start: float = Field(ge=0)
    clip_end: float = Field(gt=0)
    peak_time: float = Field(ge=0)
    pre_capture_seconds: float = Field(ge=0)
    post_capture_seconds: float = Field(ge=0)
    clip_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    thumbnail_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    clip_size_bytes: int = Field(gt=0)
    thumbnail_size_bytes: int = Field(gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def paths_and_times_are_safe(self) -> IncidentMedia:
        if not self.clip_start <= self.peak_time <= self.clip_end:
            raise ValueError("incident media zamanları sıralı olmalıdır")
        if self.updated_at < self.created_at:
            raise ValueError("incident media updated_at created_at öncesinde olamaz")
        for field_name in ("clip_ref", "thumbnail_ref", "source_refs"):
            values = getattr(self, field_name)
            refs = values if isinstance(values, list) else [values]
            for ref in refs:
                path = PurePosixPath(ref)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in ref
                    or ref != path.as_posix()
                ):
                    raise ValueError(f"{field_name} güvenli göreli POSIX yol olmalıdır")
        return self


__all__ = ["IncidentMedia"]
