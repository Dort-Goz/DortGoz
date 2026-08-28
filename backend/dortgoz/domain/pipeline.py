
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .feedback import DevelopmentUse
from .learning import DriftSnapshot, LearningBand, LearningRouteItem
from .model_lifecycle import (
    DfineTrainingPolicy,
    ModelVersion,
    PromotionPolicy,
    TrainingJob,
)


class PipelineStage(StrEnum):
    REVIEW = "review"
    APPROVAL = "approval"
    QUEUE = "queue"
    TRAINING = "training"
    MEASUREMENT = "measurement"
    PROMOTION = "promotion"


class PipelineStageSummary(BaseModel):

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: PipelineStage
    count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    action_label: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class PipelineEventItem(BaseModel):

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    learning_score: int = Field(ge=0, le=100)
    learning_band: LearningBand
    recommended_uses: list[DevelopmentUse]
    ready_uses: list[DevelopmentUse]
    blockers: list[str]


class PipelineQueueGroup(BaseModel):

    model_config = ConfigDict(extra="forbid", frozen=True)

    use: DevelopmentUse
    downstream: str = Field(min_length=1)
    safety_gate: str = Field(min_length=1)
    count: int = Field(ge=0)
    items: list[LearningRouteItem]


class PipelineModelItem(BaseModel):
    """A candidate or champion plus the promotion gate verdict for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: ModelVersion
    gate_failures: list[str]
    gate_passed: bool
    # Ölçüm alt adımları: ONNX aktarımı, dedektör ölçümü, gölge koşusu.
    onnx_exported: bool
    measured: bool
    shadow_passed: bool


class PipelineReadiness(BaseModel):
    """What the machine can actually run right now, and why not."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    can_plan: bool
    can_run: bool
    blockers: list[str]
    active_workload: str | None = None
    training_policy_version: str | None = None
    promotion_policy_version: str | None = None


class LearningPipelineView(BaseModel):

    model_config = ConfigDict(extra="forbid", frozen=True)

    pipeline_version: Literal["dortgoz-learning-pipeline-v1"] = (
        "dortgoz-learning-pipeline-v1"
    )
    stages: list[PipelineStageSummary]
    review_items: list[PipelineEventItem]
    approval_items: list[PipelineEventItem]
    queue: list[PipelineQueueGroup]
    jobs: list[TrainingJob]
    candidates: list[PipelineModelItem]
    champion: PipelineModelItem | None = None
    readiness: PipelineReadiness
    # Politikalar arayüze gider: form üst sınırları ve terfi eşikleri buradan okunur.
    training_policy: DfineTrainingPolicy | None = None
    promotion_policy: PromotionPolicy | None = None
    drift: DriftSnapshot
    mode: Literal["human_gated"] = "human_gated"
    automatic_training: Literal[False] = False
    automatic_promotion: Literal[False] = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "LearningPipelineView",
    "PipelineEventItem",
    "PipelineModelItem",
    "PipelineQueueGroup",
    "PipelineReadiness",
    "PipelineStage",
    "PipelineStageSummary",
]
