"""Memory adapter'larının ortak, storage'dan bağımsız protokolü."""

from __future__ import annotations

from typing import Protocol

from ..domain.candidate import CandidateEvent
from ..domain.event import VerifiedEvent
from ..domain.feedback import DevelopmentApproval, RuleProposal
from ..domain.media import IncidentMedia
from ..domain.memory import AnalysisRecord, AnalysisResult
from ..domain.model_lifecycle import ModelVersion, TrainingJob
from ..domain.priority import InterventionPriority
from ..domain.provenance import (
    AnalysisProvenance,
    HumanReview,
    TraceRecord,
)
from ..domain.training import TrainingFrameReview, TrainingSample
from ..domain.video import VideoMetadata


class EventRepository(Protocol):
    def create_video(self, metadata: VideoMetadata) -> VideoMetadata: ...

    def get_video(self, video_id: str) -> VideoMetadata | None: ...

    def get_analysis(self, analysis_id: str) -> AnalysisRecord | None: ...

    def find_video_by_hash(self, file_hash_sha256: str) -> VideoMetadata | None: ...

    def find_video_by_stored_filename(self, stored_filename: str) -> VideoMetadata | None: ...

    def create_analysis(
        self,
        video_id: str,
        provenance: AnalysisProvenance,
        analysis_id: str | None = None,
    ) -> AnalysisRecord: ...

    def update_analysis_status(
        self,
        analysis_id: str,
        status: str,
        progress: float,
        error: str | None = None,
    ) -> AnalysisRecord: ...

    def save_candidate(self, candidate: CandidateEvent) -> CandidateEvent: ...

    def save_trace_item(
        self, analysis_id: str, candidate_id: str, trace_item: TraceRecord
    ) -> TraceRecord: ...

    def save_event(self, event: VerifiedEvent) -> VerifiedEvent: ...

    def save_agent_bundle(
        self,
        candidate: CandidateEvent,
        trace_items: list[TraceRecord],
        event: VerifiedEvent,
    ) -> VerifiedEvent: ...

    def get_event(self, event_id: str) -> VerifiedEvent | None: ...

    def list_events(
        self, analysis_id: str, status: str | None = None
    ) -> list[VerifiedEvent]: ...

    def save_review(self, review: HumanReview) -> HumanReview: ...

    def list_reviews(self, event_id: str) -> list[HumanReview]: ...

    def save_development_approval(
        self, approval: DevelopmentApproval
    ) -> DevelopmentApproval: ...

    def list_development_approvals(
        self, event_id: str
    ) -> list[DevelopmentApproval]: ...

    def create_rule_proposal(self, proposal: RuleProposal) -> RuleProposal: ...

    def get_rule_proposal(self, proposal_id: str) -> RuleProposal | None: ...

    def list_rule_proposals(self) -> list[RuleProposal]: ...

    def update_rule_proposal(self, proposal: RuleProposal) -> RuleProposal: ...

    def save_incident_media(self, media: IncidentMedia) -> IncidentMedia: ...

    def get_incident_media(self, media_id: str) -> IncidentMedia | None: ...

    def get_incident_media_for_event(self, event_id: str) -> IncidentMedia | None: ...

    def list_incident_media(self, analysis_id: str | None = None) -> list[IncidentMedia]: ...

    def save_intervention_priority(
        self, priority: InterventionPriority
    ) -> InterventionPriority: ...

    def get_intervention_priority(
        self, priority_id: str
    ) -> InterventionPriority | None: ...

    def get_intervention_priority_for_event(
        self, event_id: str
    ) -> InterventionPriority | None: ...

    def list_intervention_priorities(
        self, analysis_id: str | None = None
    ) -> list[InterventionPriority]: ...

    def create_training_samples(
        self, samples: list[TrainingSample]
    ) -> list[TrainingSample]: ...

    def get_training_sample(self, sample_id: str) -> TrainingSample | None: ...

    def list_training_samples(self, event_id: str | None = None) -> list[TrainingSample]: ...

    def verify_training_sample(
        self, sample_id: str, review: TrainingFrameReview
    ) -> TrainingSample: ...

    def create_training_job(self, job: TrainingJob) -> TrainingJob: ...

    def get_training_job(self, job_id: str) -> TrainingJob | None: ...

    def list_training_jobs(self) -> list[TrainingJob]: ...

    def update_training_job(self, job: TrainingJob) -> TrainingJob: ...

    def create_model_version(self, version: ModelVersion) -> ModelVersion: ...

    def get_model_version(self, model_version_id: str) -> ModelVersion | None: ...

    def list_model_versions(self) -> list[ModelVersion]: ...

    def update_model_version(self, version: ModelVersion) -> ModelVersion: ...

    def switch_champion(
        self,
        champion: ModelVersion,
        previous_champion: ModelVersion | None,
    ) -> ModelVersion: ...

    def get_analysis_result(self, analysis_id: str) -> AnalysisResult | None: ...

    def query_event_memory(self, analysis_id: str, query: str) -> list[VerifiedEvent]: ...
