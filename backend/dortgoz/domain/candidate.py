from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CandidateType(StrEnum):
    INTENSE_PERSON_INTERACTION = "intense_person_interaction"
    POSSIBLE_FIGHT = "possible_fight"
    POSSIBLE_ASSAULT = "possible_assault"
    POSSIBLE_FALL = "possible_fall"
    PERSON_ON_GROUND = "person_on_ground"
    FIRE_SMOKE_CANDIDATE = "fire_smoke_candidate"
    VEHICLE_COLLISION = "vehicle_collision"
    CAMERA_BLACKOUT = "camera_blackout"
    CAMERA_FREEZE = "camera_freeze"
    CAMERA_OCCLUSION = "camera_occlusion"
    UNKNOWN_ANOMALY = "unknown_anomaly"


class ScreeningSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: float = Field(ge=0)
    anomaly_score: float = Field(ge=0, le=1)
    interaction_score: float = Field(default=0, ge=0, le=1)
    fall_score: float = Field(default=0, ge=0, le=1)
    fire_smoke_score: float = Field(default=0, ge=0, le=1)
    vehicle_conflict_score: float = Field(default=0, ge=0, le=1)
    tampering_score: float = Field(default=0, ge=0, le=1)
    image_quality: float = Field(default=1, ge=0, le=1)
    source_model: str = Field(min_length=1)
    feature_ref: str | None = None


class CandidateEvent(BaseModel):

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    start_time: float = Field(ge=0)
    peak_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    candidate_type: CandidateType
    peak_score: float = Field(ge=0, le=1)
    anomaly_score: float = Field(ge=0, le=1)
    interaction_score: float = Field(default=0, ge=0, le=1)
    fall_score: float = Field(default=0, ge=0, le=1)
    fire_score: float = Field(default=0, ge=0, le=1)
    vehicle_score: float = Field(default=0, ge=0, le=1)
    tampering_score: float = Field(default=0, ge=0, le=1)
    image_quality: float = Field(default=1, ge=0, le=1)
    trigger_signals: list[str] = Field(min_length=1)
    screening_model_id: str = Field(min_length=1)
    threshold_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def times_are_ordered(self) -> CandidateEvent:
        if not self.start_time <= self.peak_time <= self.end_time:
            raise ValueError("beklenen sıra: start_time <= peak_time <= end_time")
        return self
