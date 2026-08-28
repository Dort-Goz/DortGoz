

from __future__ import annotations

from pathlib import Path

import pytest

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import (
    EvidenceItem,
    EvidenceValidationResult,
    VerifiedEventType,
)
from dortgoz.domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
)
from dortgoz.domain.provenance import (
    AnalysisProvenance,
    HumanReview,
    ModelRunRef,
    ReviewDecision,
    TraceRecord,
)
from dortgoz.domain.video import VideoMetadata
from dortgoz.repositories.errors import (
    RepositoryConflictError,
    RepositoryDuplicateError,
)
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.event_service import EventMemoryService
from dortgoz.services.legacy_import import iter_legacy_jsonl

VIDEO_ID = "00000000-0000-0000-0000-000000000010"
ANALYSIS_ID = "00000000-0000-0000-0000-000000000011"


def metadata() -> VideoMetadata:
    return VideoMetadata(
        video_id=VIDEO_ID,
        original_filename="memory-fixture.mp4",
        stored_filename=f"{VIDEO_ID}.mp4",
        media_path=f"{VIDEO_ID}.mp4",
        file_size_bytes=100,
        file_hash_sha256="a" * 64,
        container="mov",
        codec="h264",
        width=640,
        height=480,
        fps=25,
        duration_seconds=60,
        has_audio=False,
        time_base="1/12800",
    )


def provenance() -> AnalysisProvenance:
    return AnalysisProvenance(
        contract_version="1.0.0",
        config_version="test-v1",
        code_revision="test-revision",
        model_runs=[
            ModelRunRef(
                model_id="fixture-screening",
                role="screening",
                config_version="test-v1",
                code_revision="test-revision",
            )
        ],
    )


def candidate() -> CandidateEvent:
    return CandidateEvent(
        candidate_id="candidate-memory-1",
        analysis_id=ANALYSIS_ID,
        video_id=VIDEO_ID,
        start_time=10,
        peak_time=12,
        end_time=15,
        candidate_type=CandidateType.POSSIBLE_FIGHT,
        peak_score=0.8,
        anomaly_score=0.8,
        trigger_signals=["fixture"],
        screening_model_id="fixture-screening",
        threshold_version="test-v1",
    )


def validation() -> EvidenceValidationResult:
    evidence = EvidenceItem(
        evidence_id="evidence-memory-1",
        timestamp=12,
        frame_id="frame-memory-1",
        frame_path="runs/frame-memory-1.jpg",
        clip_path="runs/candidate-memory-1.mp4",
        claim="Gözlenebilir temas örüntüsü mevcut.",
        source_model="fixture-vlm",
        validated=True,
    )
    return EvidenceValidationResult(
        candidate_id=candidate().candidate_id,
        schema_valid=True,
        timestamps_valid=True,
        evidence_valid=True,
        validated_evidence=[evidence],
        validator_version="test-v1",
    )


def event(status: EventStatus = EventStatus.HUMAN_REVIEW) -> VerifiedEvent:
    return VerifiedEvent(
        event_id="event-memory-1",
        analysis_id=ANALYSIS_ID,
        video_id=VIDEO_ID,
        candidate_id=candidate().candidate_id,
        status=status,
        event_type=VerifiedEventType.PHYSICAL_FIGHT,
        start_time=10,
        peak_time=12,
        end_time=15,
        confidence=0.9,
        validation=validation(),
        evidence=validation().validated_evidence,
    )


def trace(step: int = 1) -> TraceRecord:
    return TraceRecord(
        step=step,
        action="RUN_VLM",
        reason="fixture route",
        policy_rule_id="P-TEST",
        tool_name="fixture-vlm",
        success=True,
        duration_ms=5,
        policy_version="test-v1",
    )


def ready_repository() -> InMemoryEventRepository:
    repository = InMemoryEventRepository()
    repository.create_video(metadata())
    repository.create_analysis(VIDEO_ID, provenance(), analysis_id=ANALYSIS_ID)
    repository.save_candidate(candidate())
    return repository


def test_repository_crud_filters_and_defensive_copies() -> None:
    repository = ready_repository()
    repository.save_trace_item(ANALYSIS_ID, candidate().candidate_id, trace())
    saved = repository.save_event(event(EventStatus.REJECTED))

    saved.uncertainties.append("caller mutation")
    fetched = repository.get_event(saved.event_id)
    assert fetched is not None
    assert fetched.uncertainties == []
    assert repository.list_events(ANALYSIS_ID, "rejected")[0].status == EventStatus.REJECTED
    assert repository.get_trace(ANALYSIS_ID, candidate().candidate_id)[0].step == 1


def test_duplicate_candidate_and_revision_conflicts_are_typed() -> None:
    repository = ready_repository()
    with pytest.raises(RepositoryDuplicateError):
        repository.save_candidate(candidate().model_copy(update={"peak_score": 0.1}))
    repository.save_event(event())
    with pytest.raises(RepositoryConflictError):
        repository.save_event(event())


def test_human_review_creates_revision_without_erasing_automatic_event() -> None:
    repository = ready_repository()
    repository.save_event(event())
    review = HumanReview(
        review_id="review-memory-1",
        event_id="event-memory-1",
        decision=ReviewDecision.CONFIRM,
        note="Operatör kanıtı doğruladı.",
        reviewer="operator-1",
        revision=1,
    )

    saved_review = repository.save_review(review)
    current = repository.get_event("event-memory-1")
    revisions = repository.list_event_revisions("event-memory-1")

    assert saved_review.revision == 2
    assert current is not None and current.status == EventStatus.CONFIRMED
    assert current.revision == 2
    assert [item.status for item in revisions] == [EventStatus.HUMAN_REVIEW, EventStatus.CONFIRMED]


def test_service_keeps_confirm_when_model_evidence_gate_permits_it() -> None:
    repository = ready_repository()
    repository.save_event(event())
    service = EventMemoryService(repository)

    review = service.review_event(
        "event-memory-1",
        ReviewDecision.CONFIRM,
        reviewer="operator-1",
        note="Operatör geçerli kanıtı doğruladı.",
    )

    assert review.decision == ReviewDecision.CONFIRM
    assert repository.get_event("event-memory-1").status == EventStatus.CONFIRMED


def test_development_decision_is_separate_and_revocable() -> None:
    repository = ready_repository()
    repository.save_event(event())
    review = repository.save_review(
        HumanReview(
            review_id="review-development-1",
            event_id="event-memory-1",
            decision=ReviewDecision.REJECT,
            note="Yanlış alarm örneği.",
            reviewer="operator-1",
            revision=1,
        )
    )
    approved = repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-development-1",
            event_id="event-memory-1",
            review_id=review.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.THRESHOLD_CALIBRATION],
            reviewer="operator-1",
            note="Kalibrasyon için uygun.",
        )
    )
    revoked = repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-development-2",
            event_id="event-memory-1",
            review_id=review.review_id,
            status=DevelopmentApprovalStatus.REVOKED,
            reviewer="operator-1",
            note="Onay geri alındı.",
            supersedes_approval_id=approved.approval_id,
        )
    )

    assert [item.status for item in repository.list_development_approvals(event().event_id)] == [
        DevelopmentApprovalStatus.APPROVED,
        DevelopmentApprovalStatus.REVOKED,
    ]
    assert revoked.supersedes_approval_id == approved.approval_id


def test_bundle_is_atomic_when_trace_write_fails() -> None:
    repository = ready_repository()
    with pytest.raises(RepositoryDuplicateError):
        repository.save_agent_bundle(candidate(), [trace(), trace()], event())
    assert repository.get_event("event-memory-1") is None
    assert repository.get_trace(ANALYSIS_ID, candidate().candidate_id) == []


async def test_event_service_persists_mock_vertical_and_analysis_result() -> None:
    from dortgoz.services.mock_vertical import MockVerticalAnalysisService

    repository = InMemoryEventRepository()
    service = EventMemoryService(repository)
    vertical = await MockVerticalAnalysisService().analyze(metadata())
    service.start_analysis(metadata(), provenance(), analysis_id=vertical.analysis_id)

    for state in vertical.candidates:
        service.persist_terminal_state(state)

    result = service.get_analysis_result(vertical.analysis_id)
    assert result is not None
    assert result.candidate_count == 3
    assert result.confirmed_count == 1
    assert result.rejected_count == 1
    assert result.human_review_count == 1
    rejected = service.query(vertical.analysis_id, "rejected")
    assert len(rejected) == 1

    review_target = next(state for state in vertical.candidates if state.human_review_required)
    event_id = f"{vertical.analysis_id}:{review_target.candidate_id}"
    review = service.review_event(
        event_id,
        ReviewDecision.REJECT,
        reviewer="operator-1",
        note="Mock inceleme sonucu ret.",
    )
    assert review.revision == 2
    assert repository.get_event(event_id).status == EventStatus.REJECTED


def test_ui_replay_import_is_read_only() -> None:
    path = Path(__file__).parents[2] / "dortgoz" / "fixtures" / "ui_replay_events.jsonl"
    before = path.read_bytes()
    records = list(iter_legacy_jsonl(path))
    assert len(records) >= 10
    assert path.read_bytes() == before
