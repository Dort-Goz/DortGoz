"""Görev 10 deterministik risk ruleset testleri."""

from __future__ import annotations

from pathlib import Path

from dortgoz.domain.event import EventStatus, RiskLevel, VerifiedEvent
from dortgoz.domain.evidence import EvidenceItem, EvidenceValidationResult, VerifiedEventType
from dortgoz.services.risk_engine import (
    RiskEngine,
    RuntimeRiskDisposition,
    load_risk_ruleset,
)


def _event(
    event_type: VerifiedEventType, *, status: EventStatus = EventStatus.CONFIRMED, confidence: float = 0.9
) -> VerifiedEvent:
    evidence = EvidenceItem(evidence_id="evidence-risk", timestamp=4, frame_id="frame-risk", frame_path="runs/risk.jpg", clip_path="runs/risk.mp4", claim="Karede gözlenebilir hareket örüntüsü var.", source_model="fixture", validated=True)
    validation = EvidenceValidationResult(candidate_id="candidate-risk", schema_valid=True, timestamps_valid=True, evidence_valid=True, validated_evidence=[evidence], validator_version="fixture")
    return VerifiedEvent(event_id="event-risk", analysis_id="analysis-risk", video_id="video-risk", candidate_id="candidate-risk", status=status, event_type=event_type, start_time=2, peak_time=4, end_time=6, confidence=confidence, validation=validation if status == EventStatus.CONFIRMED else None, evidence=[evidence] if status == EventStatus.CONFIRMED else [])


def _engine() -> RiskEngine:
    root = Path(__file__).resolve().parents[3]
    return RiskEngine(load_risk_ruleset(root / "configs" / "risk_rules.yaml"))


def test_same_event_and_ruleset_always_select_same_rule() -> None:
    event = _event(VerifiedEventType.FIRE_SMOKE)

    first = _engine().assess(event)
    second = _engine().assess(event)

    assert first.model_dump(exclude={"calculated_at"}) == second.model_dump(
        exclude={"calculated_at"}
    )
    assert first.level == RiskLevel.CRITICAL
    assert first.rule_ids == ["RSK-FIRE"]
    assert first.reasons


def test_low_confidence_and_review_events_never_receive_automatic_risk() -> None:
    low_confidence = _engine().assess(_event(VerifiedEventType.ASSAULT, confidence=0.4))
    review = _engine().assess(_event(VerifiedEventType.ASSAULT, status=EventStatus.HUMAN_REVIEW))

    assert (low_confidence.level, low_confidence.review_required) == (RiskLevel.UNDETERMINED, True)
    assert (review.level, review.review_required) == (RiskLevel.REVIEW_REQUIRED, True)


def test_runtime_guard_only_returns_review_required_or_undetermined() -> None:
    grounded = RiskEngine.assess_runtime(RuntimeRiskDisposition.PROVISIONAL_GROUNDED)
    invalid = RiskEngine.assess_runtime(RuntimeRiskDisposition.INVALID_EVIDENCE)
    undetermined = RiskEngine.assess_runtime(RuntimeRiskDisposition.UNDETERMINED)

    assert (grounded.level, grounded.review_required) == (
        RiskLevel.REVIEW_REQUIRED,
        True,
    )
    assert {invalid.level, undetermined.level} == {RiskLevel.UNDETERMINED}
    assert invalid.review_required and undetermined.review_required
