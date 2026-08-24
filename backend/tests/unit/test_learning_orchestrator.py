

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
    FalseAlarmReason,
)
from dortgoz.domain.learning import DriftState
from dortgoz.domain.provenance import AnalysisProvenance, HumanReview, ReviewDecision
from dortgoz.domain.taxonomy import VerifiedEventType
from dortgoz.domain.video import VideoMetadata
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.intervention_priority import InterventionPriorityService
from dortgoz.services.learning_orchestrator import LearningOrchestrator

VIDEO_ID = "00000000-0000-0000-0000-000000000801"
ANALYSIS_ID = "analysis-learning-test"


def _repository() -> InMemoryEventRepository:
    repository = InMemoryEventRepository()
    repository.create_video(
        VideoMetadata(
            video_id=VIDEO_ID,
            original_filename="learning.mp4",
            stored_filename=f"{VIDEO_ID}.mp4",
            media_path=f"{VIDEO_ID}.mp4",
            file_size_bytes=100,
            file_hash_sha256="b" * 64,
            container="mp4",
            codec="h264",
            width=640,
            height=360,
            fps=25,
            duration_seconds=120,
            has_audio=False,
            time_base="1/25",
        )
    )
    repository.create_analysis(
        VIDEO_ID,
        AnalysisProvenance(
            contract_version="1",
            config_version="learning-test",
            code_revision="test",
        ),
        analysis_id=ANALYSIS_ID,
    )
    return repository


def _event(
    repository: InMemoryEventRepository,
    index: int,
    *,
    event_type: VerifiedEventType = VerifiedEventType.POSSIBLE_THEFT,
    confidence: float = 0.5,
    uncertain: bool = False,
) -> VerifiedEvent:
    candidate_id = f"candidate-learning-{index}"
    event_id = f"event-learning-{index}"
    repository.save_candidate(
        CandidateEvent(
            candidate_id=candidate_id,
            analysis_id=ANALYSIS_ID,
            video_id=VIDEO_ID,
            start_time=index * 5,
            peak_time=index * 5 + 1,
            end_time=index * 5 + 2,
            candidate_type=CandidateType.UNKNOWN_ANOMALY,
            peak_score=confidence,
            anomaly_score=confidence,
            trigger_signals=["test"],
            screening_model_id="screening-test",
            threshold_version="test",
        )
    )
    return repository.save_event(
        VerifiedEvent(
            event_id=event_id,
            analysis_id=ANALYSIS_ID,
            video_id=VIDEO_ID,
            candidate_id=candidate_id,
            status=EventStatus.HUMAN_REVIEW,
            event_type=event_type,
            start_time=index * 5,
            peak_time=index * 5 + 1,
            end_time=index * 5 + 2,
            confidence=confidence,
            uncertainties=["model emin değil"] if uncertain else [],
        )
    )


def _review(
    repository: InMemoryEventRepository,
    event: VerifiedEvent,
    index: int,
    *,
    decision: ReviewDecision = ReviewDecision.EDIT,
    event_type: VerifiedEventType | None = VerifiedEventType.POSSIBLE_THEFT,
    false_alarm_reason: FalseAlarmReason | None = None,
) -> HumanReview:
    return repository.save_review(
        HumanReview(
            review_id=f"review-learning-{index}",
            event_id=event.event_id,
            decision=decision,
            event_type=event_type.value if event_type is not None else None,
            false_alarm_reason=false_alarm_reason,
            note="İnsan incelemesi tamamlandı.",
            reviewer="operator",
            revision=1,
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        )
    )


def test_learning_value_is_separate_from_intervention_priority() -> None:
    repository = _repository()
    event = _event(repository, 1, confidence=0.5, uncertain=True)
    review = _review(repository, event, 1)
    InterventionPriorityService(repository).assess_and_save(
        event.event_id,
        risk="kritik",
        event_type="hirsizlik",
        phase="sonuclandi",
        needs_review=False,
    )

    plan = LearningOrchestrator(repository).plan(event.event_id)

    assert plan.latest_review_id == review.review_id
    assert plan.learning_score >= 50
    assert plan.intervention_score == 80
    assert plan.automatic_training is False
    evaluation = next(route for route in plan.routes if route.use == DevelopmentUse.EVALUATION)
    assert evaluation.recommended is True
    assert evaluation.approval_state == "approval_required"
    assert evaluation.ready is False


def test_only_explicitly_approved_use_enters_route_queue() -> None:
    repository = _repository()
    event = _event(repository, 1)
    review = _review(repository, event, 1)
    repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-learning-1",
            event_id=event.event_id,
            review_id=review.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.EVALUATION],
            reviewer="operator",
            note="Yalnız sabit değerlendirme için onaylandı.",
        )
    )
    service = LearningOrchestrator(repository)

    evaluation = service.route_queue(DevelopmentUse.EVALUATION)
    calibration = service.route_queue(DevelopmentUse.THRESHOLD_CALIBRATION)

    assert evaluation.count == 1
    assert evaluation.items[0].approval_id == "approval-learning-1"
    assert evaluation.automatic_execution is False
    assert calibration.count == 0


def test_orchestrator_overview_exposes_human_gated_system_state() -> None:
    repository = _repository()
    approved_event = _event(repository, 1, confidence=0.5, uncertain=True)
    review = _review(repository, approved_event, 1)
    repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-learning-overview",
            event_id=approved_event.event_id,
            review_id=review.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.EVALUATION],
            reviewer="operator",
            note="Sabit değerlendirme rotası onaylandı.",
        )
    )
    pending_event = _event(repository, 2, confidence=0.5, uncertain=True)

    overview = LearningOrchestrator(repository).overview()

    assert overview.total_events == 2
    assert overview.reviewed_events == 1
    assert overview.pending_review_events == 1
    assert overview.ready_routes == 1
    assert overview.mode == "human_gated"
    assert overview.automatic_execution is False
    assert overview.automatic_training is False
    assert overview.automatic_promotion is False
    evaluation = next(
        route
        for route in overview.route_summaries
        if route.use == DevelopmentUse.EVALUATION
    )
    assert evaluation.recommended_count == 1
    assert evaluation.ready_count == 1
    assert evaluation.awaiting_gate_count == 0
    pending = next(
        candidate
        for candidate in overview.priority_candidates
        if candidate.event_id == pending_event.event_id
    )
    assert pending.ready_uses == []
    assert pending.blockers == ["İnsan incelemesi gerekli"]


def test_orchestrator_overview_rejects_unbounded_candidate_limit() -> None:
    service = LearningOrchestrator(_repository())

    for invalid in (0, 101):
        try:
            service.overview(candidate_limit=invalid)
        except ValueError as exc:
            assert "1 ile 100" in str(exc)
        else:
            raise AssertionError("geçersiz candidate_limit kabul edildi")


def test_orchestrator_overview_reads_review_history_linearly(monkeypatch) -> None:
    repository = _repository()
    for index in range(1, 5):
        event = _event(repository, index)
        _review(repository, event, index)
    original = repository.list_reviews
    calls = 0

    def counted(event_id: str):
        nonlocal calls
        calls += 1
        return original(event_id)

    monkeypatch.setattr(repository, "list_reviews", counted)

    LearningOrchestrator(repository).overview()

    assert calls == 8


def test_new_review_makes_old_learning_approval_stale() -> None:
    repository = _repository()
    event = _event(repository, 1)
    first = _review(repository, event, 1)
    repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-learning-1",
            event_id=event.event_id,
            review_id=first.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.EVALUATION],
            reviewer="operator",
            note="İlk karar için onaylandı.",
        )
    )
    current = repository.get_event(event.event_id)
    assert current is not None
    _review(repository, current, 2, event_type=VerifiedEventType.VANDALISM)

    plan = LearningOrchestrator(repository).plan(event.event_id)
    evaluation = next(route for route in plan.routes if route.use == DevelopmentUse.EVALUATION)

    assert evaluation.approval_state == "stale"
    assert evaluation.ready is False


def test_drift_sentinel_is_shadow_only_and_detects_large_shift() -> None:
    repository = _repository()
    for index in range(8):
        recent = index >= 4
        event = _event(
            repository,
            index + 1,
            event_type=(
                VerifiedEventType.VANDALISM
                if recent
                else VerifiedEventType.POSSIBLE_THEFT
            ),
            confidence=0.2 if recent else 0.9,
            uncertain=recent,
        )
        _review(
            repository,
            event,
            index + 1,
            decision=ReviewDecision.REJECT if recent else ReviewDecision.EDIT,
            event_type=None if recent else VerifiedEventType.POSSIBLE_THEFT,
            false_alarm_reason=(FalseAlarmReason.NORMAL_ACTIVITY if recent else None),
        )

    snapshot = LearningOrchestrator(repository).drift_snapshot()

    assert snapshot.state == DriftState.DRIFT
    assert snapshot.score >= 50
    assert snapshot.mode == "shadow"
    assert snapshot.automatic_training is False
    assert snapshot.automatic_promotion is False
    assert len(snapshot.metrics) == 4


def test_drift_sentinel_waits_for_minimum_review_count() -> None:
    repository = _repository()
    event = _event(repository, 1)
    _review(repository, event, 1)

    snapshot = LearningOrchestrator(repository).drift_snapshot()

    assert snapshot.state == DriftState.INSUFFICIENT_DATA
    assert snapshot.reviewed_events == 1
    assert snapshot.minimum_required == 8


def test_critical_event_never_becomes_camera_suppression_candidate() -> None:
    repository = _repository()
    event = _event(
        repository,
        1,
        event_type=VerifiedEventType.POSSIBLE_ARMED_INCIDENT,
    )
    _review(
        repository,
        event,
        1,
        decision=ReviewDecision.REJECT,
        event_type=None,
        false_alarm_reason=FalseAlarmReason.NORMAL_ACTIVITY,
    )

    plan = LearningOrchestrator(repository).plan(event.event_id)
    route = next(route for route in plan.routes if route.use == DevelopmentUse.CAMERA_RULE)

    assert route.recommended is False
    assert route.ready is False
