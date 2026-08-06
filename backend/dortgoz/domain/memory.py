"""Analysis ve repository sonuç modelleri."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .event import VerifiedEvent
from .provenance import AnalysisProvenance
from .video import VideoMetadata


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REVIEW_REQUIRED = "review_required"


class AnalysisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    status: AnalysisStatus = AnalysisStatus.QUEUED
    progress: float = Field(default=0, ge=0, le=1)
    error: str | None = None
    provenance: AnalysisProvenance
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    video: VideoMetadata
    status: AnalysisStatus
    summary_tr: str = ""
    events: list[VerifiedEvent] = Field(default_factory=list)
    candidate_count: int = Field(ge=0)
    confirmed_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    human_review_count: int = Field(ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    processing_seconds: float | None = Field(default=None, ge=0)
    provenance: AnalysisProvenance
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def counters_match_events(self) -> AnalysisResult:
        confirmed = sum(event.status.value == "confirmed" for event in self.events)
        rejected = sum(event.status.value == "rejected" for event in self.events)
        review = sum(
            event.status.value in {"human_review", "processing_failed"}
            for event in self.events
        )
        if (confirmed, rejected, review) != (
            self.confirmed_count,
            self.rejected_count,
            self.human_review_count,
        ):
            raise ValueError("analysis event sayaçları events ile eşleşmiyor")
        if self.candidate_count < len(self.events):
            raise ValueError("candidate_count event sayısından küçük olamaz")
        return self
