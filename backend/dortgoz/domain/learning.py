"""Explainable active-learning and drift-monitoring contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .feedback import DevelopmentUse


class LearningBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PRIORITY = "priority"


class DriftState(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    STABLE = "stable"
    WATCH = "watch"
    DRIFT = "drift"


class LearningValueComponents(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uncertainty: int = Field(ge=0, le=100)
    disagreement: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    drift: int = Field(ge=0, le=100)
    coverage_gap: int = Field(ge=0, le=100)
    redundancy: int = Field(ge=0, le=100)
    annotation_cost: int = Field(ge=0, le=100)


class DriftMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    baseline: float = Field(allow_inf_nan=False)
    current: float = Field(allow_inf_nan=False)
    delta: float = Field(allow_inf_nan=False)
    points: int = Field(ge=0, le=100)
    detail: str = Field(min_length=1)


class DriftSnapshot(BaseModel):
    """Read-only sentinel result. It never changes a live model or threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: DriftState
    score: int = Field(ge=0, le=100)
    reviewed_events: int = Field(ge=0)
    baseline_size: int = Field(ge=0)
    current_size: int = Field(ge=0)
    minimum_required: int = Field(ge=2)
    metrics: list[DriftMetric] = Field(default_factory=list)
    mode: Literal["shadow"] = "shadow"
    automatic_training: Literal[False] = False
    automatic_promotion: Literal[False] = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LearningRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    use: DevelopmentUse
    recommended: bool
    approval_state: Literal[
        "review_required",
        "approval_required",
        "approved",
        "not_approved",
        "rejected",
        "revoked",
        "stale",
    ]
    ready: bool
    downstream: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    safety_gate: str = Field(min_length=1)


class LearningPlan(BaseModel):
    """One event's learning value and safe downstream routing plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_version: Literal["learning-orchestrator-v1"] = "learning-orchestrator-v1"
    event_id: str = Field(min_length=1)
    event_revision: int = Field(ge=1)
    latest_review_id: str | None = None
    learning_score: int = Field(ge=0, le=100)
    learning_band: LearningBand
    components: LearningValueComponents
    reasons: list[str] = Field(min_length=1, max_length=10)
    intervention_score: int | None = Field(default=None, ge=0, le=100)
    intervention_band: str | None = None
    drift_state: DriftState
    routes: list[LearningRoute]
    automatic_training: Literal[False] = False
    automatic_promotion: Literal[False] = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LearningRouteItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    event_revision: int = Field(ge=1)
    review_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    use: DevelopmentUse
    learning_score: int = Field(ge=0, le=100)
    learning_band: LearningBand
    downstream: str = Field(min_length=1)


class LearningRouteQueue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    use: DevelopmentUse
    items: list[LearningRouteItem]
    count: int = Field(ge=0)
    automatic_execution: Literal[False] = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "DriftMetric",
    "DriftSnapshot",
    "DriftState",
    "LearningBand",
    "LearningPlan",
    "LearningRoute",
    "LearningRouteItem",
    "LearningRouteQueue",
    "LearningValueComponents",
]
