"""Doğrulanmış olay, risk ve prosedür çıktı modelleri."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import EvidenceItem, EvidenceValidationResult, VerifiedEventType
from .provenance import HumanReview, ModelRunRef, TraceRecord


class EventStatus(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    HUMAN_REVIEW = "human_review"
    PROCESSING_FAILED = "processing_failed"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    REVIEW_REQUIRED = "review_required"
    UNDETERMINED = "undetermined"


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: RiskLevel
    reasons: list[str] = Field(min_length=1)
    rule_ids: list[str] = Field(default_factory=list)
    review_required: bool = False
    ruleset_version: str = Field(min_length=1)
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProcedureAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=1)
    action: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    section: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requires_human_approval: bool = True


class VerifiedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    status: EventStatus
    event_type: VerifiedEventType
    start_time: float | None = Field(default=None, ge=0)
    peak_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    uncertainties: list[str] = Field(default_factory=list)
    validation: EvidenceValidationResult | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    risk: RiskAssessment | None = None
    actions: list[ProcedureAction] = Field(default_factory=list)
    legacy_event_type: str | None = None
    before: str | None = None
    during: str | None = None
    after: str | None = None
    decision_trace: list[TraceRecord] = Field(default_factory=list)
    review: HumanReview | None = None
    model_provenance: list[ModelRunRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def confirmation_requires_evidence(self) -> VerifiedEvent:
        if self.status == EventStatus.CONFIRMED:
            if self.validation is None or not self.validation.permits_confirmation:
                raise ValueError("confirmed event geçerli evidence validation gerektirir")
            if not self.evidence:
                raise ValueError("confirmed event en az bir kanıt gerektirir")
        times = (self.start_time, self.peak_time, self.end_time)
        if all(value is not None for value in times):
            start, peak, end = times
            assert start is not None and peak is not None and end is not None
            if not start <= peak <= end:
                raise ValueError("event zamanları sıralı olmalıdır")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at created_at değerinden önce olamaz")
        return self
