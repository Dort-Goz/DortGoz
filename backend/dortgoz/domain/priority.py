"""Sürümlü ve açıklanabilir müdahale önceliği sözleşmesi."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InterventionBand(StrEnum):
    ROUTINE = "routine"
    REVIEW = "review"
    HIGH = "high"
    URGENT = "urgent"


def intervention_band_for_score(score: int) -> InterventionBand:
    if score >= 80:
        return InterventionBand.URGENT
    if score >= 60:
        return InterventionBand.HIGH
    if score >= 30:
        return InterventionBand.REVIEW
    return InterventionBand.ROUTINE


class InterventionPriority(BaseModel):
    """Bir canonical olayın operatör kuyruğundaki açıklanabilir önceliği."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    priority_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    event_revision: int = Field(ge=1)
    score: int = Field(ge=0, le=100)
    band: InterventionBand
    reasons: list[str] = Field(min_length=1, max_length=12)
    risk_input: str = Field(pattern=r"^(dusuk|orta|yuksek|kritik)$")
    event_type_input: str = Field(min_length=1, max_length=100)
    phase_input: str = Field(pattern=r"^(basladi|gelisiyor|sonuclandi)$")
    needs_review_input: bool = False
    model_confidence: float | None = Field(default=None, ge=0, le=1)
    ruleset_version: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def score_and_band_match(self) -> InterventionPriority:
        if self.band != intervention_band_for_score(self.score):
            raise ValueError("intervention priority score ve band eşleşmiyor")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("intervention priority gerekçeleri tekrar edemez")
        if self.calculated_at < self.created_at:
            raise ValueError("intervention priority calculated_at created_at öncesinde olamaz")
        return self


__all__ = [
    "InterventionBand",
    "InterventionPriority",
    "intervention_band_for_score",
]
