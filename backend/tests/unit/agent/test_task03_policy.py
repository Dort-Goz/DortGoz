from __future__ import annotations

import pytest
from pydantic import ValidationError

from dortgoz.agent.actions import AgentAction
from dortgoz.agent.policy import RoutingConfig, decide_next_action
from dortgoz.agent.state import EventAgentState
from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.evidence import (
    EvidenceItem,
    EvidenceValidationResult,
    VerifiedEventType,
    VLMResult,
    VLMStatus,
)

EXPECTED_ACTIONS = [
    "CONTINUE_SCREENING",
    "RUN_CV_ONLY",
    "RUN_DENSE_ANALYSIS",
    "EXPAND_CONTEXT",
    "RUN_VLM",
    "RETRY_VLM_STRICT",
    "VALIDATE_EVIDENCE",
    "CONFIRM_EVENT",
    "REJECT_EVENT",
    "REQUEST_HUMAN_REVIEW",
    "CALCULATE_RISK",
    "RETRIEVE_PROCEDURE",
    "STORE_EVENT",
    "PROCESSING_FAILED",
    "COMPLETE",
]


def make_state(**updates: object) -> EventAgentState:
    candidate = CandidateEvent(
        candidate_id="candidate-1",
        analysis_id="analysis-1",
        video_id="video-1",
        start_time=2,
        peak_time=3,
        end_time=4,
        candidate_type=CandidateType.POSSIBLE_FIGHT,
        peak_score=0.70,
        anomaly_score=0.70,
        image_quality=0.90,
        trigger_signals=["fixture"],
        screening_model_id="mock-screening-v1",
        threshold_version="test-v1",
    )
    data: dict[str, object] = {
        "analysis_id": "analysis-1",
        "video_id": "video-1",
        "candidate_id": "candidate-1",
        "candidate": candidate,
        "video_duration": 10,
        "image_quality": 0.90,
    }
    data.update(updates)
    return EventAgentState.model_validate(data)


def validation(
    valid: bool,
    *,
    schema_valid: bool = True,
    timestamps_valid: bool = True,
    unsupported_critical_claim: bool = False,
) -> EvidenceValidationResult:
    evidence = []
    if valid:
        evidence.append(
            EvidenceItem(
                evidence_id="evidence-1",
                timestamp=3,
                frame_id="frame-1",
                frame_path="runs/frame-1.jpg",
                clip_path="runs/clip.mp4",
                claim="Gözlenebilir fiziksel temas mevcut.",
                source_model="fixture",
                validated=True,
            )
        )
    return EvidenceValidationResult(
        candidate_id="candidate-1",
        schema_valid=schema_valid,
        timestamps_valid=timestamps_valid,
        evidence_valid=valid,
        unsupported_critical_claim=unsupported_critical_claim,
        validated_evidence=evidence,
        validator_version="fixture-v1",
    )


def test_canonical_action_set_is_exact() -> None:
    assert [item.value for item in AgentAction] == EXPECTED_ACTIONS


def test_terminal_flags_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        make_state(confirmed=True, rejected=True, completed=True)


def test_step_fourteen_must_already_be_terminal() -> None:
    with pytest.raises(ValidationError):
        make_state(current_step=14)


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"confirmed": True, "completed": True}, AgentAction.COMPLETE),
        ({"video_processable": False}, AgentAction.PROCESSING_FAILED),
        ({"processing_error": "fixture"}, AgentAction.REQUEST_HUMAN_REVIEW),
        (
            {
                "cv_status": VLMStatus.CONFIRMED,
                "cv_event_type": VerifiedEventType.PHYSICAL_FIGHT,
                "cv_confidence": 0.96,
            },
            AgentAction.VALIDATE_EVIDENCE,
        ),
        (
            {
                "cv_status": VLMStatus.CONFIRMED,
                "cv_event_type": VerifiedEventType.PHYSICAL_FIGHT,
                "cv_confidence": 0.96,
                "validation": validation(True),
            },
            AgentAction.CONFIRM_EVENT,
        ),
        (
            {
                "cv_status": VLMStatus.REJECTED,
                "cv_event_type": VerifiedEventType.NORMAL_INTERACTION,
                "cv_confidence": 0.95,
                "validation": validation(False),
            },
            AgentAction.REJECT_EVENT,
        ),
        (
            {
                "cv_status": VLMStatus.UNCERTAIN,
                "cv_event_type": VerifiedEventType.UNCERTAIN,
                "cv_confidence": 0.4,
                "validation": validation(False),
            },
            AgentAction.RUN_DENSE_ANALYSIS,
        ),
        (
            {
                "cv_status": VLMStatus.UNCERTAIN,
                "cv_event_type": VerifiedEventType.UNCERTAIN,
                "cv_confidence": 0.4,
                "validation": validation(False),
                "context_expanded": True,
                "context_expansion_count": 1,
            },
            AgentAction.RUN_DENSE_ANALYSIS,
        ),
        (
            {
                "cv_status": VLMStatus.UNCERTAIN,
                "cv_event_type": VerifiedEventType.UNCERTAIN,
                "cv_confidence": 0.4,
                "validation": validation(False),
                "context_expanded": True,
                "context_expansion_count": 1,
                "dense_analysis_done": True,
                "dense_analysis_count": 1,
                "vlm_attempts": 1,
            },
            AgentAction.RETRY_VLM_STRICT,
        ),
        (
            {
                "cv_status": VLMStatus.UNCERTAIN,
                "cv_event_type": VerifiedEventType.UNCERTAIN,
                "cv_confidence": 0.4,
                "validation": validation(False),
                "context_expanded": True,
                "context_expansion_count": 1,
                "dense_analysis_done": True,
                "dense_analysis_count": 1,
                "vlm_attempts": 2,
            },
            AgentAction.REQUEST_HUMAN_REVIEW,
        ),
        ({"image_quality": 0.1}, AgentAction.RUN_DENSE_ANALYSIS),
        (
            {
                "cv_status": VLMStatus.CONFIRMED,
                "cv_event_type": VerifiedEventType.PHYSICAL_FIGHT,
                "cv_confidence": 0.75,
                "validation": validation(False, timestamps_valid=False),
            },
            AgentAction.EXPAND_CONTEXT,
        ),
        (
            {
                "cv_status": VLMStatus.UNCERTAIN,
                "cv_event_type": VerifiedEventType.UNCERTAIN,
                "cv_confidence": 0.4,
                "vlm_attempts": 1,
                "validation": validation(False, schema_valid=False),
            },
            AgentAction.RETRY_VLM_STRICT,
        ),
        (
            {
                "cv_status": VLMStatus.CONFIRMED,
                "cv_event_type": VerifiedEventType.ASSAULT,
                "cv_confidence": 0.95,
                "validation": validation(
                    True, unsupported_critical_claim=True
                ),
            },
            AgentAction.REQUEST_HUMAN_REVIEW,
        ),
        (
            {
                "candidate": CandidateEvent(
                    candidate_id="candidate-1",
                    analysis_id="analysis-1",
                    video_id="video-1",
                    start_time=2,
                    peak_time=3,
                    end_time=4,
                    candidate_type=CandidateType.CAMERA_FREEZE,
                    peak_score=0.9,
                    anomaly_score=0.9,
                    trigger_signals=["fixture"],
                    screening_model_id="fixture",
                    threshold_version="fixture",
                )
            },
            AgentAction.RUN_CV_ONLY,
        ),
        ({}, AgentAction.RUN_VLM),
        ({"current_step": 13}, AgentAction.REQUEST_HUMAN_REVIEW),
    ],
)
def test_policy_routing_cases(
    updates: dict[str, object], expected: AgentAction
) -> None:
    decision = decide_next_action(make_state(**updates), RoutingConfig())
    assert decision.action == expected
    assert decision.reason
    assert 1 <= decision.priority <= 10
    assert decision.policy_rule_id


def test_medium_score_runs_dense_before_vlm() -> None:
    state = make_state()
    candidate = state.candidate.model_copy(update={"anomaly_score": 0.60})
    decision = decide_next_action(
        make_state(candidate=candidate), RoutingConfig()
    )
    assert decision.action == AgentAction.RUN_DENSE_ANALYSIS
    assert decision.policy_rule_id == "P44"


def test_critical_candidate_prioritizes_vlm() -> None:
    state = make_state()
    candidate = state.candidate.model_copy(
        update={
            "candidate_type": CandidateType.POSSIBLE_ASSAULT,
            "peak_score": 0.92,
            "anomaly_score": 0.92,
        }
    )
    decision = decide_next_action(make_state(candidate=candidate), RoutingConfig())
    assert decision.action == AgentAction.RUN_VLM
    assert decision.priority == 9
    assert decision.policy_rule_id == "P45"


def test_cv_vlm_conflict_expands_context_first() -> None:
    vlm_result = VLMResult(
        candidate_id="candidate-1",
        event_type=VerifiedEventType.NORMAL_INTERACTION,
        status=VLMStatus.REJECTED,
        confidence=0.50,
        model_id="fixture-vlm",
        prompt_version="fixture-v1",
        attempt=1,
        raw_response_hash="b" * 64,
    )
    state = make_state(
        cv_status=VLMStatus.CONFIRMED,
        cv_event_type=VerifiedEventType.PHYSICAL_FIGHT,
        cv_confidence=0.97,
        vlm_result=vlm_result,
        vlm_attempts=1,
        validation=validation(False),
    )
    decision = decide_next_action(state, RoutingConfig())
    assert decision.action == AgentAction.EXPAND_CONTEXT
    assert decision.policy_rule_id == "P16"
