"""Event, analiz ve insan incelemesi için audit/provenance modelleri."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .feedback import FalseAlarmReason


class TraceRecord(BaseModel):
    """Repository katmanının agent paketine bağımlı olmayan trace görünümü."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step: int = Field(ge=1, le=14)
    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    policy_rule_id: str = Field(min_length=1)
    tool_name: str | None = None
    input_ref: str | None = None
    output_ref: str | None = None
    success: bool | None = None
    error_code: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    policy_version: str = Field(min_length=1)


class ModelRunRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    prompt_version: str | None = None
    config_version: str = Field(min_length=1)
    code_revision: str = Field(min_length=1)
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_license: str | None = None
    model_source: str | None = None


class AnalysisProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    code_revision: str = Field(min_length=1)
    model_runs: list[ModelRunRef] = Field(default_factory=list)


class ProcedureSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    section: str = Field(min_length=1)
    version: str = Field(min_length=1)
    valid_from: date | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewDecision(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    EDIT = "edit"


class HumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    decision: ReviewDecision
    event_type: str | None = None
    start_time: float | None = Field(default=None, ge=0)
    peak_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    risk_level: str | None = None
    false_alarm_reason: FalseAlarmReason | None = None
    intervention_required: bool | None = None
    note: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    revision: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def review_times_are_ordered(self) -> HumanReview:
        values = (self.start_time, self.peak_time, self.end_time)
        if all(value is not None for value in values):
            start, peak, end = values
            assert start is not None and peak is not None and end is not None
            if not start <= peak <= end:
                raise ValueError("human review zamanları sıralı olmalıdır")
        return self
