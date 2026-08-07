"""VLM ve kanıt doğrulama domain modelleri."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VLMStatus(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


class VerifiedEventType(StrEnum):
    PHYSICAL_FIGHT = "physical_fight"
    NORMAL_INTERACTION = "normal_interaction"
    PLAY_FIGHTING = "play_fighting"
    ASSAULT = "assault"
    FALL = "fall"
    CONTROLLED_SITTING = "controlled_sitting"
    PERSON_ON_GROUND = "person_on_ground"
    FIRE_SMOKE = "fire_smoke"
    VEHICLE_COLLISION = "vehicle_collision"
    CAMERA_BLACKOUT = "camera_blackout"
    CAMERA_FREEZE = "camera_freeze"
    CAMERA_OCCLUSION = "camera_occlusion"
    UNKNOWN_ANOMALY = "unknown_anomaly"
    UNCERTAIN = "uncertain"


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: float = Field(ge=0)
    frame_id: str = Field(min_length=1)
    claim: str = Field(min_length=5)
    claim_type: Literal["observation", "temporal_relation", "uncertainty"] = "observation"


class VLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    event_type: VerifiedEventType
    status: VLMStatus
    confidence: float = Field(ge=0, le=1)
    start_time: float | None = Field(default=None, ge=0)
    peak_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    before: str | None = None
    during: str | None = None
    after: str | None = None
    evidence: list[EvidenceClaim] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    model_id: str = Field(min_length=1)
    model_version: str | None = None
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_license: str | None = None
    model_source: str | None = None
    prompt_version: str = Field(min_length=1)
    attempt: int = Field(ge=1, le=2)
    raw_response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_status_and_times(self) -> VLMResult:
        times = (self.start_time, self.peak_time, self.end_time)
        if self.status == VLMStatus.CONFIRMED and any(value is None for value in times):
            raise ValueError("confirmed VLM sonucunda start/peak/end zorunludur")
        if self.status == VLMStatus.CONFIRMED and not self.evidence:
            raise ValueError("confirmed VLM sonucunda en az bir evidence zorunludur")
        if all(value is not None for value in times):
            start, peak, end = times
            assert start is not None and peak is not None and end is not None
            if not start <= peak <= end:
                raise ValueError("beklenen sıra: start_time <= peak_time <= end_time")
        return self


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    field: str | None = None
    severity: Literal["warning", "error"] = "error"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    event_id: str | None = None
    timestamp: float = Field(ge=0)
    frame_id: str = Field(min_length=1)
    frame_path: str = Field(min_length=1)
    clip_path: str = Field(min_length=1)
    claim: str = Field(min_length=5)
    source_model: str = Field(min_length=1)
    validated: bool
    validation_notes: list[str] = Field(default_factory=list)

    @field_validator("frame_path", "clip_path")
    @classmethod
    def evidence_path_must_be_relative(cls, value: str) -> str:
        posix = PurePosixPath(value.replace("\\", "/"))
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
            raise ValueError("evidence path göreli ve çalışma kökü içinde olmalıdır")
        return posix.as_posix()


class EvidenceValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    schema_valid: bool
    timestamps_valid: bool
    evidence_valid: bool
    language_valid: bool = True
    unsupported_critical_claim: bool = False
    critical_evidence_sufficient: bool = True
    validation_errors: list[ValidationIssue] = Field(default_factory=list)
    validated_evidence: list[EvidenceItem] = Field(default_factory=list)
    validator_version: str = Field(min_length=1)

    @property
    def permits_confirmation(self) -> bool:
        return (
            self.schema_valid
            and self.timestamps_valid
            and self.evidence_valid
            and self.language_valid
            and not self.unsupported_critical_claim
            and self.critical_evidence_sufficient
            and bool(self.validated_evidence)
        )
