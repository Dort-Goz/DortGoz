"""Görev 04 için saf ve deterministik ilk evidence doğrulayıcı."""

from __future__ import annotations

from ..agent.state import EventAgentState
from ..domain.evidence import (
    EvidenceItem,
    EvidenceValidationResult,
    ValidationIssue,
    VLMStatus,
)

VALIDATOR_VERSION = "task-04-validator-v1"


def validate_evidence(state: EventAgentState) -> EvidenceValidationResult:
    """Model önerisini state'teki gerçek keyframe referanslarına karşı sınar."""

    status = state.proposal_status
    issues: list[ValidationIssue] = []
    if status is None or state.proposal_event_type is None:
        issues.append(
            ValidationIssue(
                code="MISSING_PROPOSAL",
                field="proposal_status",
                message="Doğrulanacak CV/VLM önerisi bulunamadı.",
            )
        )
        return EvidenceValidationResult(
            candidate_id=state.candidate_id,
            schema_valid=False,
            timestamps_valid=False,
            evidence_valid=False,
            validation_errors=issues,
            validator_version=VALIDATOR_VERSION,
        )

    result = state.vlm_result
    timestamps_valid = True
    if result and status == VLMStatus.CONFIRMED:
        values = (result.start_time, result.peak_time, result.end_time)
        if any(value is None for value in values):
            timestamps_valid = False
        else:
            start, peak, end = values
            assert start is not None and peak is not None and end is not None
            lower = state.context_clip.clip_start if state.context_clip else 0.0
            upper = (
                state.context_clip.clip_end
                if state.context_clip
                else state.video_duration
            )
            timestamps_valid = lower <= start <= peak <= end <= upper
        if not timestamps_valid:
            issues.append(
                ValidationIssue(
                    code="TIMESTAMP_OUT_OF_BOUNDS",
                    field="start_time/peak_time/end_time",
                    message="Olay zamanları analiz bağlamının dışında veya sırasız.",
                )
            )

    frame_by_id = {frame.frame_id: frame for frame in state.keyframes}
    validated: list[EvidenceItem] = []
    for index, claim in enumerate(state.proposal_evidence, start=1):
        frame = frame_by_id.get(claim.frame_id)
        in_video = 0 <= claim.timestamp <= state.video_duration
        timestamp_matches = frame is not None and abs(frame.timestamp - claim.timestamp) <= 0.5
        if frame is None:
            issues.append(
                ValidationIssue(
                    code="UNKNOWN_FRAME_ID",
                    field=f"evidence[{index}].frame_id",
                    message="Evidence claim gerçek bir keyframe'e bağlı değil.",
                )
            )
            continue
        if not in_video or not timestamp_matches:
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_TIMESTAMP_MISMATCH",
                    field=f"evidence[{index}].timestamp",
                    message="Evidence zamanı keyframe veya video sınırıyla eşleşmiyor.",
                )
            )
            continue
        clip_path = (
            state.context_clip.clip_path
            if state.context_clip
            else f"runs/{state.analysis_id}/{state.candidate_id}/candidate.mp4"
        )
        validated.append(
            EvidenceItem(
                evidence_id=f"{state.candidate_id}-evidence-{index}",
                timestamp=claim.timestamp,
                frame_id=claim.frame_id,
                frame_path=frame.frame_path,
                clip_path=clip_path,
                claim=claim.claim,
                source_model=(
                    state.vlm_result.model_id if state.vlm_result else "mock-cv-v1"
                ),
                validated=True,
                validation_notes=["frame_id ve timestamp eşleşti"],
            )
        )

    if status == VLMStatus.UNCERTAIN:
        issues.append(
            ValidationIssue(
                code="UNCERTAIN_RESULT",
                field="status",
                message="Belirsiz model sonucu terminal hüküm üretemez.",
            )
        )

    evidence_valid = (
        len(validated) == len(state.proposal_evidence) and bool(validated)
        if status == VLMStatus.CONFIRMED
        else status == VLMStatus.REJECTED
    )
    return EvidenceValidationResult(
        candidate_id=state.candidate_id,
        schema_valid=True,
        timestamps_valid=timestamps_valid,
        evidence_valid=evidence_valid,
        validation_errors=issues,
        validated_evidence=validated,
        validator_version=VALIDATOR_VERSION,
    )
