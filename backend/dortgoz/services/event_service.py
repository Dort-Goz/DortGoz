

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ..agent.state import EventAgentState
from ..domain.event import EventStatus, VerifiedEvent
from ..domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
    FalseAlarmReason,
)
from ..domain.memory import AnalysisRecord, AnalysisResult, AnalysisStatus
from ..domain.provenance import (
    AnalysisProvenance,
    HumanReview,
    MaintenanceReview,
    ModelRunRef,
    ReviewDecision,
    TraceRecord,
)
from ..domain.video import VideoMetadata
from ..repositories.errors import RepositoryConflictError
from ..repositories.protocols import EventRepository


class EventPersistResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: VerifiedEvent
    analysis: AnalysisRecord


class EventMemoryService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def start_analysis(
        self,
        metadata: VideoMetadata,
        provenance: AnalysisProvenance,
        *,
        analysis_id: str | None = None,
    ) -> AnalysisRecord:
        self.repository.create_video(metadata)
        return self.repository.create_analysis(
            metadata.video_id, provenance, analysis_id=analysis_id
        )

    def persist_terminal_state(
        self,
        state: EventAgentState,
        *,
        update_analysis: bool = True,
        progress: float = 1.0,
    ) -> EventPersistResult:
        if not state.completed:
            raise ValueError("terminal olmayan state event memory'ye yazılamaz")
        event = self._event_from_state(state)
        trace_items = [
            TraceRecord.model_validate(item.model_dump())
            for item in state.decision_trace
        ]
        saved_event = self.repository.save_agent_bundle(
            state.candidate, trace_items, event
        )
        status = (
            AnalysisStatus.COMPLETED
            if saved_event.status in {EventStatus.CONFIRMED, EventStatus.REJECTED}
            else AnalysisStatus.FAILED
            if saved_event.status == EventStatus.PROCESSING_FAILED
            else AnalysisStatus.REVIEW_REQUIRED
        )
        analysis = self.repository.get_analysis(state.analysis_id)
        if analysis is None:
            raise ValueError(f"analysis bulunamadı: {state.analysis_id}")
        if update_analysis:
            analysis = self.repository.update_analysis_status(
                state.analysis_id,
                status.value,
                progress=progress,
                error=state.processing_error,
            )
        return EventPersistResult(event=saved_event, analysis=analysis)

    def review_event(
        self,
        event_id: str,
        decision: ReviewDecision,
        *,
        reviewer: str,
        note: str,
        event_type: str | None = None,
        start_time: float | None = None,
        peak_time: float | None = None,
        end_time: float | None = None,
        risk_level: str | None = None,
        false_alarm_reason: FalseAlarmReason | None = None,
        intervention_required: bool | None = None,
    ) -> HumanReview:
        event = self.repository.get_event(event_id)
        if (
            decision == ReviewDecision.CONFIRM
            and event is not None
            and (
                event.validation is None
                or not event.validation.permits_confirmation
                or not event.evidence
            )
        ):
            # İnsan kararı model kanıt kapısını atlamaz. Triage ile aynı biçimde
            # olayı incelenmiş tutar ve operatör etiketini ayrı revizyon olarak yazar.
            decision = ReviewDecision.EDIT
            event_type = event_type or event.event_type.value
            start_time = event.start_time if start_time is None else start_time
            peak_time = event.peak_time if peak_time is None else peak_time
            end_time = event.end_time if end_time is None else end_time
        review = HumanReview(
            review_id=str(uuid4()),
            event_id=event_id,
            decision=decision,
            event_type=event_type,
            start_time=start_time,
            peak_time=peak_time,
            end_time=end_time,
            risk_level=risk_level,
            false_alarm_reason=false_alarm_reason,
            intervention_required=intervention_required,
            note=note,
            reviewer=reviewer,
            revision=1,
        )
        return self.repository.save_review(review)

    def record_development_decision(
        self,
        event_id: str,
        review_id: str,
        status: DevelopmentApprovalStatus,
        *,
        approved_uses: list[DevelopmentUse],
        reviewer: str,
        note: str,
        maintenance_review_id: str | None = None,
        supersedes_approval_id: str | None = None,
    ) -> DevelopmentApproval:
        approval = DevelopmentApproval(
            approval_id=str(uuid4()),
            event_id=event_id,
            review_id=review_id,
            maintenance_review_id=maintenance_review_id,
            status=status,
            approved_uses=approved_uses,
            reviewer=reviewer,
            note=note,
            supersedes_approval_id=supersedes_approval_id,
        )
        return self.repository.save_development_approval(approval)

    def review_maintenance_event(
        self,
        event_id: str,
        operator_review_id: str,
        decision: ReviewDecision,
        *,
        reviewer: str,
        note: str,
        event_type: str | None = None,
        start_time: float | None = None,
        peak_time: float | None = None,
        end_time: float | None = None,
        risk_level: str | None = None,
        false_alarm_reason: FalseAlarmReason | None = None,
    ) -> MaintenanceReview:
        operator_reviews = self.repository.list_reviews(event_id)
        if not operator_reviews:
            raise RepositoryConflictError("IT incelemesi için operatör kararı gerekli")
        latest_operator_review = operator_reviews[-1]
        if latest_operator_review.review_id != operator_review_id:
            raise RepositoryConflictError(
                "operatör kararı değişti; IT incelemesini yeniden açın"
            )
        review = MaintenanceReview(
            maintenance_review_id=str(uuid4()),
            event_id=event_id,
            operator_review_id=operator_review_id,
            decision=decision,
            event_type=event_type,
            start_time=start_time,
            peak_time=peak_time,
            end_time=end_time,
            risk_level=risk_level,
            false_alarm_reason=false_alarm_reason,
            note=note,
            reviewer=reviewer,
            revision=1,
        )
        return self.repository.save_maintenance_review(review)

    def get_analysis_result(self, analysis_id: str) -> AnalysisResult | None:
        return self.repository.get_analysis_result(analysis_id)

    def query(self, analysis_id: str, question: str) -> list[VerifiedEvent]:
        return self.repository.query_event_memory(analysis_id, question)

    @staticmethod
    def _event_from_state(state: EventAgentState) -> VerifiedEvent:
        if state.confirmed:
            status = EventStatus.CONFIRMED
        elif state.rejected:
            status = EventStatus.REJECTED
        elif state.processing_failed:
            status = EventStatus.PROCESSING_FAILED
        else:
            status = EventStatus.HUMAN_REVIEW

        result = state.vlm_result
        event_type = state.proposal_event_type
        if event_type is None:
            from ..domain.evidence import VerifiedEventType

            event_type = VerifiedEventType.UNKNOWN_ANOMALY
        start = result.start_time if result and result.start_time is not None else state.candidate.start_time
        peak = result.peak_time if result and result.peak_time is not None else state.candidate.peak_time
        end = result.end_time if result and result.end_time is not None else state.candidate.end_time
        now = datetime.now(UTC)
        model_runs = [
            ModelRunRef(
                model_id=state.candidate.screening_model_id,
                role="screening",
                config_version=state.config_version,
                code_revision="task-05-v1",
            )
        ]
        if result is not None:
            model_runs.append(
                ModelRunRef(
                    model_id=result.model_id,
                    role="vlm",
                    prompt_version=result.prompt_version,
                    config_version=state.config_version,
                    code_revision="task-05-v1",
                    output_hash=result.raw_response_hash,
                    artifact_sha256=result.artifact_sha256,
                    model_license=result.model_license,
                    model_source=result.model_source,
                )
            )
        return VerifiedEvent(
            event_id=f"{state.analysis_id}:{state.candidate_id}",
            analysis_id=state.analysis_id,
            video_id=state.video_id,
            candidate_id=state.candidate_id,
            status=status,
            event_type=event_type,
            start_time=start,
            peak_time=peak,
            end_time=end,
            confidence=state.proposal_confidence,
            before=result.before if result else None,
            during=result.during if result else None,
            after=result.after if result else None,
            uncertainties=result.uncertainties if result else [],
            validation=state.validation,
            evidence=(state.validation.validated_evidence if state.validation else []),
            risk=state.risk,
            actions=state.procedures,
            decision_trace=[
                TraceRecord.model_validate(item.model_dump())
                for item in state.decision_trace
            ],
            model_provenance=model_runs,
            created_at=now,
            updated_at=now,
        )
