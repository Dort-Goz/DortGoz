"""Gerçek WindowReport hattı için dar, confirmation üretmeyen evidence köprüsü."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..domain.context import KeyframeRef
from ..domain.evidence import (
    EvidenceClaim,
    EvidenceValidationResult,
    ValidationIssue,
    VerifiedEventType,
)
from ..domain.taxonomy import CanonicalEventType, requires_human_review
from ..events import FrameReference, WindowReport
from .evidence_validator import VALIDATOR_VERSION, validate_runtime_evidence

CapturedFrames = Mapping[str, tuple[FrameReference, bytes]]


class RuntimeValidationStatus(StrEnum):
    """Yalnız gözlem sidecar'ı; terminal EventStatus veya VLMStatus değildir."""

    VALIDATED = "VALIDATED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    UNDETERMINED = "UNDETERMINED"


class RuntimeEventValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_index: int = Field(ge=0)
    event_type: CanonicalEventType | None
    status: RuntimeValidationStatus
    materialized_frames: list[KeyframeRef] = Field(default_factory=list)
    validation: EvidenceValidationResult


class RuntimeWindowValidation(BaseModel):
    """WS'ye eklenmeyen, RunContext içinde yaşayan pencere doğrulama sidecar'ı."""

    model_config = ConfigDict(extra="forbid")

    window_index: int = Field(ge=0)
    window_start: float
    window_end: float
    status: RuntimeValidationStatus
    events: list[RuntimeEventValidation] = Field(default_factory=list)


def materialize_runtime_evidence(
    *,
    report: WindowReport,
    captured_frames: CapturedFrames,
    run_id: str,
    window_index: int,
    workspace_root: Path,
    evidence_root: Path,
) -> list[KeyframeRef]:
    """Yalnız raporda referans verilen selected JPEG'leri hash'li olarak yaz."""

    if not report.events:
        return []

    root = workspace_root.resolve()
    output_root = evidence_root.resolve()
    if not output_root.is_relative_to(root):
        raise ValueError("runtime evidence dizini çalışma kökü içinde olmalıdır")

    requested_ids = {evidence.frame_id for event in report.events for evidence in event.evidence}
    run_key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    window_dir = output_root / "_runtime_evidence" / run_key / f"{window_index:06d}"
    materialized: list[KeyframeRef] = []
    for frame_id, (frame, jpeg) in captured_frames.items():
        if frame_id != frame.frame_id:
            raise ValueError("captured frame anahtarı ile FrameReference eşleşmiyor")
        if frame_id not in requested_ids:
            continue
        target = window_dir / f"{frame_id}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(jpeg)
        materialized.append(
            KeyframeRef(
                frame_id=frame.frame_id,
                timestamp=frame.timestamp,
                frame_path=target.relative_to(root).as_posix(),
                hash_sha256=hashlib.sha256(jpeg).hexdigest(),
                selection_reason="finalized_window_report_evidence",
            )
        )
    return materialized


def validate_materialized_report(
    *,
    report: WindowReport,
    materialized_frames: list[KeyframeRef],
    run_id: str,
    window_index: int,
    video_duration: float,
    workspace_root: Path,
) -> RuntimeWindowValidation:
    """Final raporu mevcut EvidenceValidator kurallarıyla sidecar'a dönüştür."""

    event_results: list[RuntimeEventValidation] = []
    for event_index, event in enumerate(report.events):
        candidate_id = _candidate_id(run_id, window_index, event_index)
        if event.event_type is None:
            validation = _missing_event_type(candidate_id)
            status = RuntimeValidationStatus.UNDETERMINED
        else:
            event_type = VerifiedEventType(event.event_type.value)
            validation = validate_runtime_evidence(
                candidate_id=candidate_id,
                event_type=event_type,
                evidence=[
                    EvidenceClaim(
                        frame_id=item.frame_id,
                        timestamp=item.timestamp,
                        claim=item.claim,
                    )
                    for item in event.evidence
                ],
                keyframes=materialized_frames,
                video_duration=video_duration,
                workspace_root=workspace_root,
            )
            status = _validation_status(validation, event_type)
        referenced = {item.frame_id for item in event.evidence}
        event_results.append(
            RuntimeEventValidation(
                event_index=event_index,
                event_type=event.event_type,
                status=status,
                materialized_frames=[
                    frame for frame in materialized_frames if frame.frame_id in referenced
                ],
                validation=validation,
            )
        )

    overall = max(
        (item.status for item in event_results),
        key=_status_rank,
        default=RuntimeValidationStatus.UNDETERMINED,
    )
    return RuntimeWindowValidation(
        window_index=window_index,
        window_start=report.window_start,
        window_end=report.window_end,
        status=overall,
        events=event_results,
    )


def postprocess_finalized_report(
    *,
    report: WindowReport,
    captured_frames: CapturedFrames,
    run_id: str,
    window_index: int,
    video_duration: float,
    workspace_root: Path,
    evidence_root: Path,
) -> RuntimeWindowValidation | None:
    """Normal dalı sıfır ek I/O ile atla; olaylı final raporu doğrula."""

    if not report.events:
        return None
    materialized = materialize_runtime_evidence(
        report=report,
        captured_frames=captured_frames,
        run_id=run_id,
        window_index=window_index,
        workspace_root=workspace_root,
        evidence_root=evidence_root,
    )
    return validate_materialized_report(
        report=report,
        materialized_frames=materialized,
        run_id=run_id,
        window_index=window_index,
        video_duration=video_duration,
        workspace_root=workspace_root,
    )


def _candidate_id(run_id: str, window_index: int, event_index: int) -> str:
    run_key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return f"runtime-{run_key}-{window_index:06d}-{event_index:03d}"


def _missing_event_type(candidate_id: str) -> EvidenceValidationResult:
    return EvidenceValidationResult(
        candidate_id=candidate_id,
        schema_valid=False,
        timestamps_valid=False,
        evidence_valid=False,
        validation_errors=[
            ValidationIssue(
                code="MISSING_RUNTIME_EVENT_TYPE",
                field="event_type",
                message="Runtime olayı canonical event_type taşımıyor.",
            )
        ],
        validator_version=VALIDATOR_VERSION,
    )


def _validation_status(
    validation: EvidenceValidationResult,
    event_type: VerifiedEventType,
) -> RuntimeValidationStatus:
    if (
        not validation.language_valid
        or validation.unsupported_critical_claim
        or not validation.critical_evidence_sufficient
        or requires_human_review(event_type)
    ):
        return RuntimeValidationStatus.HUMAN_REVIEW
    if (
        not validation.schema_valid
        or not validation.timestamps_valid
        or not validation.evidence_valid
    ):
        return RuntimeValidationStatus.INVALID_EVIDENCE
    return RuntimeValidationStatus.VALIDATED


def _status_rank(status: RuntimeValidationStatus) -> int:
    return {
        RuntimeValidationStatus.VALIDATED: 0,
        RuntimeValidationStatus.HUMAN_REVIEW: 1,
        RuntimeValidationStatus.INVALID_EVIDENCE: 2,
        RuntimeValidationStatus.UNDETERMINED: 3,
    }[status]


__all__ = [
    "RuntimeEventValidation",
    "RuntimeValidationStatus",
    "RuntimeWindowValidation",
    "materialize_runtime_evidence",
    "postprocess_finalized_report",
    "validate_materialized_report",
]
