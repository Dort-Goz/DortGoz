from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from dortgoz.agent.policy import RoutingConfig, decide_next_action
from dortgoz.agent.state import EventAgentState
from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.context import ContextClip, KeyframeRef
from dortgoz.domain.evidence import EvidenceClaim, VerifiedEventType, VLMResult, VLMStatus
from dortgoz.services.evidence_validator import validate_evidence


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _candidate() -> CandidateEvent:
    return CandidateEvent(
        candidate_id="candidate-validator",
        analysis_id="analysis-validator",
        video_id="video-validator",
        start_time=2,
        peak_time=4,
        end_time=6,
        candidate_type=CandidateType.POSSIBLE_ASSAULT,
        peak_score=0.9,
        anomaly_score=0.9,
        trigger_signals=["fixture"],
        screening_model_id="fixture-screening",
        threshold_version="fixture-thresholds",
    )


def _state(tmp_path: Path, *, evidence: list[EvidenceClaim], event_type: VerifiedEventType) -> EventAgentState:
    base = tmp_path / "runs" / "analysis-validator" / "candidate-validator"
    frames_dir = base / "frames"
    frames_dir.mkdir(parents=True)
    clip = base / "candidate-context.mp4"
    clip_payload = b"context-clip"
    clip.write_bytes(clip_payload)
    frames: list[KeyframeRef] = []
    for label, timestamp in (("before", 2.0), ("peak", 4.0), ("after", 6.0)):
        payload = f"{label}-jpeg".encode()
        target = frames_dir / f"{label}.jpg"
        target.write_bytes(payload)
        frames.append(
            KeyframeRef(
                frame_id=f"candidate-validator-{label}",
                timestamp=timestamp,
                frame_path=target.relative_to(tmp_path).as_posix(),
                hash_sha256=_hash(payload),
                selection_reason=label,
            )
        )
    result = VLMResult(
        candidate_id="candidate-validator",
        event_type=event_type,
        status=VLMStatus.CONFIRMED,
        confidence=0.9,
        start_time=2,
        peak_time=4,
        end_time=6,
        evidence=evidence,
        model_id="fixture-vlm",
        prompt_version="fixture-v1",
        attempt=1,
        raw_response_hash="a" * 64,
    )
    return EventAgentState(
        analysis_id="analysis-validator",
        video_id="video-validator",
        candidate_id="candidate-validator",
        candidate=_candidate(),
        video_duration=10,
        image_quality=0.9,
        context_clip=ContextClip(
            candidate_id="candidate-validator",
            clip_start=2,
            clip_end=6,
            clip_path=clip.relative_to(tmp_path).as_posix(),
            frame_count=8,
            fps=2,
            hash_sha256=_hash(clip_payload),
        ),
        keyframes=frames,
        vlm_result=result,
    )


def _claim(label: str, timestamp: float, text: str = "Tepe karesinde hızlı hareket gözleniyor.") -> EvidenceClaim:
    return EvidenceClaim(
        timestamp=timestamp,
        frame_id=f"candidate-validator-{label}",
        claim=text,
    )


def test_real_hash_matched_critical_evidence_permits_confirmation(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        evidence=[_claim("before", 2), _claim("peak", 4)],
        event_type=VerifiedEventType.ASSAULT,
    )

    result = validate_evidence(state, workspace_root=tmp_path)

    assert result.permits_confirmation
    assert len(result.validated_evidence) == 2
    assert result.validator_version == "task-09-validator-v1"


def test_tampered_frame_or_clip_cannot_confirm(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        evidence=[_claim("before", 2), _claim("peak", 4)],
        event_type=VerifiedEventType.ASSAULT,
    )
    (tmp_path / state.keyframes[0].frame_path).write_bytes(b"tampered")
    (tmp_path / state.context_clip.clip_path).write_bytes(b"tampered-clip")

    result = validate_evidence(state, workspace_root=tmp_path)

    assert not result.permits_confirmation
    assert {issue.code for issue in result.validation_errors} >= {
        "EVIDENCE_FRAME_HASH_MISMATCH",
        "EVIDENCE_CLIP_HASH_MISMATCH",
    }


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        (_claim("peak", 4, "olay var"), "EVIDENCE_CLAIM_VAGUE"),
        (_claim("peak", 4, "A person is running quickly."), "EVIDENCE_CLAIM_LANGUAGE"),
        (_claim("peak", 4, "Kişinin niyeti saldırı yönünde görünüyor."), "UNSUPPORTED_CRITICAL_CLAIM"),
    ],
)
def test_conservative_claim_rules_block_confirmation(
    tmp_path: Path, claim: EvidenceClaim, expected: str
) -> None:
    state = _state(tmp_path, evidence=[claim], event_type=VerifiedEventType.UNKNOWN_ANOMALY)

    result = validate_evidence(state, workspace_root=tmp_path)

    assert not result.permits_confirmation
    assert expected in {issue.code for issue in result.validation_errors}


def test_single_frame_critical_event_requires_human_review(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=[_claim("peak", 4)], event_type=VerifiedEventType.ASSAULT)
    validation = validate_evidence(state, workspace_root=tmp_path)
    validated_state = state.model_copy(update={"validation": validation})

    assert not validation.critical_evidence_sufficient
    assert decide_next_action(validated_state, RoutingConfig()).policy_rule_id == "P13"


def test_possible_armed_incident_keeps_observable_claim_but_requires_human_review(
    tmp_path: Path,
) -> None:
    state = _state(
        tmp_path,
        evidence=[
            _claim("before", 2, "Silaha benzeyen bir nesne görünmektedir."),
            _claim("peak", 4, "Silaha benzeyen bir nesne görünmektedir."),
        ],
        event_type=VerifiedEventType.POSSIBLE_ARMED_INCIDENT,
    )

    validation = validate_evidence(state, workspace_root=tmp_path)
    decision = decide_next_action(state.model_copy(update={"validation": validation}), RoutingConfig())

    assert validation.permits_confirmation
    assert "UNSUPPORTED_CRITICAL_CLAIM" not in {issue.code for issue in validation.validation_errors}
    assert decision.policy_rule_id == "P20"
