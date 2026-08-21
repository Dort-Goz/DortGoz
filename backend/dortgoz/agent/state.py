from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain.candidate import CandidateEvent
from ..domain.context import ContextClip, DenseAnalysisResult, KeyframeRef
from ..domain.event import ProcedureAction, RiskAssessment
from ..domain.evidence import (
    EvidenceClaim,
    EvidenceValidationResult,
    VerifiedEventType,
    VLMResult,
    VLMStatus,
)
from .trace import DecisionTraceItem


class EventAgentState(BaseModel):

    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    trace_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    candidate: CandidateEvent
    video_processable: bool = True
    video_duration: float = Field(gt=0)
    image_quality: float = Field(ge=0, le=1)

    dense_result: DenseAnalysisResult | None = None
    dense_analysis_done: bool = False
    dense_analysis_count: int = Field(default=0, ge=0, le=1)
    cv_status: VLMStatus | None = None
    cv_event_type: VerifiedEventType | None = None
    cv_confidence: float | None = Field(default=None, ge=0, le=1)
    cv_evidence: list[EvidenceClaim] = Field(default_factory=list)

    context_clip: ContextClip | None = None
    context_expanded: bool = False
    context_expansion_count: int = Field(default=0, ge=0, le=1)
    keyframes: list[KeyframeRef] = Field(default_factory=list)

    vlm_result: VLMResult | None = None
    vlm_attempts: int = Field(default=0, ge=0, le=2)
    strict_schema_used: bool = False
    validation: EvidenceValidationResult | None = None

    confirmed: bool = False
    rejected: bool = False
    human_review_required: bool = False
    processing_error: str | None = None
    processing_failed: bool = False

    risk: RiskAssessment | None = None
    procedures: list[ProcedureAction] = Field(default_factory=list)
    stored_event_id: str | None = None

    current_step: int = Field(default=0, ge=0, le=14)
    completed: bool = False
    decision_trace: list[DecisionTraceItem] = Field(default_factory=list)
    contract_version: str = "1.0.0"
    config_version: str = "task-03-v1"
    policy_version: str = "task-03-v1"

    @model_validator(mode="after")
    def state_invariants(self) -> EventAgentState:
        if self.candidate.analysis_id != self.analysis_id:
            raise ValueError("candidate.analysis_id state ile eşleşmelidir")
        if self.candidate.video_id != self.video_id:
            raise ValueError("candidate.video_id state ile eşleşmelidir")
        if self.candidate.candidate_id != self.candidate_id:
            raise ValueError("candidate_id state ile eşleşmelidir")
        if self.candidate.end_time > self.video_duration:
            raise ValueError("candidate video süresinin dışında kalamaz")
        if self.dense_analysis_done != (self.dense_analysis_count == 1):
            raise ValueError("dense analysis bayrağı ile sayacı tutarsız")
        if self.context_expanded != (self.context_expansion_count == 1):
            raise ValueError("context expansion bayrağı ile sayacı tutarsız")
        terminals = (
            self.confirmed,
            self.rejected,
            self.human_review_required,
            self.processing_failed,
        )
        if sum(terminals) > 1:
            raise ValueError("terminal karar bayrakları birbirini dışlar")
        if self.completed and sum(terminals) != 1:
            raise ValueError("completed state tam bir terminal karar gerektirir")
        if self.current_step == 14 and not self.completed:
            raise ValueError("14. adımda state terminal olmalıdır")
        return self

    @property
    def proposal_status(self) -> VLMStatus | None:
        return self.vlm_result.status if self.vlm_result else self.cv_status

    @property
    def proposal_confidence(self) -> float | None:
        return self.vlm_result.confidence if self.vlm_result else self.cv_confidence

    @property
    def proposal_event_type(self) -> VerifiedEventType | None:
        return self.vlm_result.event_type if self.vlm_result else self.cv_event_type

    @property
    def proposal_evidence(self) -> list[EvidenceClaim]:
        return self.vlm_result.evidence if self.vlm_result else self.cv_evidence
