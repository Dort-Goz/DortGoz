"""Gerçek WindowReport hattı için transient ve fail-closed evidence köprüsü."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

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

LOGGER = logging.getLogger(__name__)
CapturedFrames = Mapping[str, tuple[FrameReference, bytes]]


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceScope:
    """Public run_id'den bağımsız, yalnız artifact yolu için kullanılan kimlik."""

    public_run_id: str = field(repr=False, compare=False)
    artifact_run_id: UUID = field(default_factory=uuid4)

    @classmethod
    def create(cls, public_run_id: str) -> RuntimeEvidenceScope:
        return cls(public_run_id=public_run_id)


class RuntimeValidationStatus(StrEnum):
    """Yalnız gözlem sidecar'ı; terminal EventStatus veya VLMStatus değildir."""

    VALIDATED = "VALIDATED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    UNDETERMINED = "UNDETERMINED"


class RuntimeEvidenceDigest(BaseModel):
    """Cleanup sonrasında kalabilen, path taşımayan transient evidence özeti."""

    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(pattern=r"^f_[0-9]{3,}$")
    timestamp: float = Field(ge=0)
    hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuntimeEventValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_index: int = Field(ge=0)
    event_type: CanonicalEventType | None
    status: RuntimeValidationStatus
    evidence_digests: list[RuntimeEvidenceDigest] = Field(default_factory=list)
    validation: EvidenceValidationResult


class RuntimeWindowValidation(BaseModel):
    """WS'ye ve durable state'e eklenmeyen transient pencere sidecar'ı."""

    model_config = ConfigDict(extra="forbid")

    artifact_run_id: UUID
    window_index: int = Field(ge=0)
    window_start: float
    window_end: float
    status: RuntimeValidationStatus
    events: list[RuntimeEventValidation] = Field(default_factory=list)
    operational_issues: list[ValidationIssue] = Field(default_factory=list)


class RuntimeEvidenceOperationalError(RuntimeError):
    code = "RUNTIME_EVIDENCE_OPERATIONAL_FAILURE"


class RuntimeEvidencePathError(RuntimeEvidenceOperationalError):
    code = "RUNTIME_EVIDENCE_PATH_UNSAFE"


class RuntimeEvidenceIdentityConflict(RuntimeEvidenceOperationalError):
    code = "RUNTIME_EVIDENCE_IDENTITY_CONFLICT"


@dataclass(frozen=True, slots=True)
class _MaterializedFrame:
    keyframe: KeyframeRef
    digest: RuntimeEvidenceDigest


@dataclass(slots=True)
class _ArtifactTracker:
    scope: RuntimeEvidenceScope
    workspace_root: Path
    evidence_root: Path
    window_index: int
    paths: set[Path] = field(default_factory=set)
    owned_directories: list[Path] = field(default_factory=list)
    run_directory: Path | None = None
    window_directory: Path | None = None


def postprocess_finalized_report(
    *,
    report: WindowReport,
    captured_frames: CapturedFrames,
    scope: RuntimeEvidenceScope,
    window_index: int,
    video_duration: float,
    workspace_root: Path,
    evidence_root: Path,
) -> RuntimeWindowValidation | None:
    """Olaylı final raporu doğrula ve her koşulda ephemeral dosyaları temizle."""

    if not report.events:
        return None

    tracker = _ArtifactTracker(
        scope=scope,
        workspace_root=workspace_root,
        evidence_root=evidence_root,
        window_index=window_index,
    )
    sidecar: RuntimeWindowValidation | None = None
    try:
        _prepare_workspace(tracker)
        materialized = materialize_runtime_evidence(
            report=report,
            captured_frames=captured_frames,
            tracker=tracker,
        )
        sidecar = validate_materialized_report(
            report=report,
            materialized_frames=materialized,
            scope=scope,
            window_index=window_index,
            video_duration=video_duration,
            workspace_root=workspace_root,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        issue = _operational_issue(exc)
        LOGGER.exception(
            "runtime_evidence_operation_failed",
            extra={
                "artifact_run_id": scope.artifact_run_id.hex,
                "public_run_id": scope.public_run_id,
                "window_index": window_index,
                "evidence_error_code": issue.code,
            },
        )
        sidecar = _undetermined_sidecar(
            report=report,
            scope=scope,
            window_index=window_index,
            issue=issue,
        )
    finally:
        cleanup_issues = _cleanup_tracker(tracker)
        for issue in cleanup_issues:
            LOGGER.warning(
                "runtime_evidence_cleanup_failed",
                extra={
                    "artifact_run_id": scope.artifact_run_id.hex,
                    "public_run_id": scope.public_run_id,
                    "window_index": window_index,
                    "evidence_error_code": issue.code,
                },
            )
        if sidecar is not None and cleanup_issues:
            sidecar = sidecar.model_copy(
                update={"operational_issues": sidecar.operational_issues + cleanup_issues}
            )
    return sidecar


def materialize_runtime_evidence(
    *,
    report: WindowReport,
    captured_frames: CapturedFrames,
    tracker: _ArtifactTracker,
) -> list[_MaterializedFrame]:
    """Yalnız raporda referans verilen selected JPEG'leri atomik yayımla."""

    if tracker.window_directory is None:
        raise RuntimeEvidenceOperationalError("ephemeral workspace hazırlanmadı")
    requested_ids = {evidence.frame_id for event in report.events for evidence in event.evidence}
    materialized: list[_MaterializedFrame] = []
    for frame_id, (frame, jpeg) in captured_frames.items():
        if frame_id != frame.frame_id:
            raise RuntimeEvidenceIdentityConflict(
                "captured frame anahtarı ile FrameReference eşleşmiyor"
            )
        if frame_id not in requested_ids:
            continue
        digest = hashlib.sha256(jpeg).hexdigest()
        target = _publish_frame(
            tracker=tracker,
            frame_id=frame.frame_id,
            payload=jpeg,
            digest=digest,
        )
        materialized.append(
            _MaterializedFrame(
                keyframe=KeyframeRef(
                    frame_id=frame.frame_id,
                    timestamp=frame.timestamp,
                    frame_path=target.relative_to(tracker.workspace_root.resolve()).as_posix(),
                    hash_sha256=digest,
                    selection_reason="finalized_window_report_evidence",
                ),
                digest=RuntimeEvidenceDigest(
                    frame_id=frame.frame_id,
                    timestamp=frame.timestamp,
                    hash_sha256=digest,
                ),
            )
        )
    return materialized


def validate_materialized_report(
    *,
    report: WindowReport,
    materialized_frames: list[_MaterializedFrame],
    scope: RuntimeEvidenceScope,
    window_index: int,
    video_duration: float,
    workspace_root: Path,
) -> RuntimeWindowValidation:
    """Final raporu mevcut EvidenceValidator kurallarıyla sidecar'a dönüştür."""

    keyframes = [item.keyframe for item in materialized_frames]
    event_results: list[RuntimeEventValidation] = []
    for event_index, event in enumerate(report.events):
        candidate_id = _candidate_id(scope, window_index, event_index)
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
                keyframes=keyframes,
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
                evidence_digests=[
                    item.digest
                    for item in materialized_frames
                    if item.digest.frame_id in referenced
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
        artifact_run_id=scope.artifact_run_id,
        window_index=window_index,
        window_start=report.window_start,
        window_end=report.window_end,
        status=overall,
        events=event_results,
    )


def _prepare_workspace(tracker: _ArtifactTracker) -> None:
    workspace = tracker.workspace_root.resolve()
    evidence_root = tracker.evidence_root.resolve(strict=False)
    if evidence_root != workspace and not evidence_root.is_relative_to(workspace):
        raise RuntimeEvidencePathError("evidence root çalışma kökü dışında")
    tracker.workspace_root = workspace
    tracker.evidence_root = evidence_root

    evidence_parent = evidence_root.parent
    _ensure_safe_directory(evidence_parent, workspace, tracker)
    _ensure_safe_directory(evidence_root, workspace, tracker)

    run_directory = evidence_root / tracker.scope.artifact_run_id.hex
    _ensure_safe_directory(run_directory, evidence_root, tracker, cleanup_owned=True)
    window_directory = run_directory / f"w{tracker.window_index:06d}"
    _ensure_safe_directory(window_directory, evidence_root, tracker, cleanup_owned=True)
    tracker.run_directory = run_directory
    tracker.window_directory = window_directory


def _ensure_safe_directory(
    path: Path,
    trusted_root: Path,
    tracker: _ArtifactTracker,
    *,
    cleanup_owned: bool = False,
) -> None:
    if _is_link_or_junction(path):
        raise RuntimeEvidencePathError(f"symlink/junction evidence yolu reddedildi: {path.name}")
    parent = path.parent.resolve()
    if path != trusted_root and not parent.is_relative_to(trusted_root):
        raise RuntimeEvidencePathError("evidence directory trusted root dışına çıkıyor")
    existed = path.exists()
    if not existed:
        try:
            path.mkdir()
        except FileExistsError:
            pass
        else:
            if cleanup_owned:
                tracker.owned_directories.append(path)
    if _is_link_or_junction(path):
        raise RuntimeEvidencePathError(f"symlink/junction evidence yolu reddedildi: {path.name}")
    resolved = path.resolve()
    if resolved != trusted_root and not resolved.is_relative_to(trusted_root):
        raise RuntimeEvidencePathError("resolved evidence directory trusted root dışına çıkıyor")


def _publish_frame(
    *,
    tracker: _ArtifactTracker,
    frame_id: str,
    payload: bytes,
    digest: str,
) -> Path:
    window = tracker.window_directory
    if window is None:
        raise RuntimeEvidenceOperationalError("ephemeral window directory bulunamadı")
    marker = window / f"{frame_id}.sha"
    _claim_logical_identity(tracker, marker, digest)

    target = window / f"{frame_id}-{digest[:32]}.jpg"
    _assert_safe_file_path(target, tracker.evidence_root.resolve())
    if target.exists():
        if _file_hash(target) != digest:
            raise RuntimeEvidenceIdentityConflict(
                "aynı content identity farklı evidence bytes içeriyor"
            )
        tracker.paths.add(target)
        return target

    temporary = window / f".{frame_id}-{uuid4().hex[:16]}.part"
    _write_complete_file(temporary, payload, tracker)
    try:
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _file_hash(target) != digest:
                raise RuntimeEvidenceIdentityConflict(
                    "concurrent evidence publish farklı bytes üretti"
                )
        tracker.paths.add(target)
    finally:
        _unlink_tracked(temporary, tracker)
    return target


def _claim_logical_identity(
    tracker: _ArtifactTracker,
    marker: Path,
    digest: str,
) -> None:
    _assert_safe_file_path(marker, tracker.evidence_root.resolve())
    if not marker.exists():
        temporary = marker.with_name(f".{marker.name}-{uuid4().hex[:16]}.part")
        _write_complete_file(temporary, digest.encode("ascii"), tracker)
        try:
            try:
                os.link(temporary, marker)
            except FileExistsError:
                pass
        finally:
            _unlink_tracked(temporary, tracker)
    _assert_safe_file_path(marker, tracker.evidence_root.resolve())
    try:
        claimed_digest = marker.read_text(encoding="ascii")
    except OSError as exc:
        raise RuntimeEvidenceOperationalError("evidence identity marker okunamadı") from exc
    tracker.paths.add(marker)
    if claimed_digest != digest:
        raise RuntimeEvidenceIdentityConflict(
            "aynı logical frame identity farklı bytes ile materialize edildi"
        )


def _write_complete_file(path: Path, payload: bytes, tracker: _ArtifactTracker) -> None:
    _assert_safe_file_path(path, tracker.evidence_root.resolve())
    tracker.paths.add(path)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _assert_safe_file_path(path: Path, trusted_root: Path) -> None:
    if _is_link_or_junction(path):
        raise RuntimeEvidencePathError(f"symlink/junction evidence dosyası reddedildi: {path.name}")
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(trusted_root):
        raise RuntimeEvidencePathError("resolved evidence file trusted root dışına çıkıyor")


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or is_junction()


def _cleanup_tracker(tracker: _ArtifactTracker) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in sorted(tracker.paths, key=lambda item: len(item.parts), reverse=True):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            issues.append(_cleanup_issue(path, exc))
    for directory in reversed(tracker.owned_directories):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            issues.append(_cleanup_issue(directory, exc))
    return issues


def _unlink_tracked(path: Path, tracker: _ArtifactTracker) -> None:
    path.unlink(missing_ok=True)
    tracker.paths.discard(path)


def _cleanup_issue(path: Path, exc: OSError) -> ValidationIssue:
    return ValidationIssue(
        code="RUNTIME_EVIDENCE_CLEANUP_FAILED",
        field="ephemeral_evidence",
        message=f"Ephemeral evidence cleanup başarısız: {path.name} ({type(exc).__name__}).",
        severity="warning",
    )


def _operational_issue(exc: Exception) -> ValidationIssue:
    code = getattr(exc, "code", RuntimeEvidenceOperationalError.code)
    detail = str(exc).strip() or type(exc).__name__
    return ValidationIssue(
        code=code,
        field="ephemeral_evidence",
        message=f"Runtime evidence doğrulanamadı: {type(exc).__name__}: {detail}"[:500],
    )


def _undetermined_sidecar(
    *,
    report: WindowReport,
    scope: RuntimeEvidenceScope,
    window_index: int,
    issue: ValidationIssue,
) -> RuntimeWindowValidation:
    events = []
    for event_index, event in enumerate(report.events):
        validation = EvidenceValidationResult(
            candidate_id=_candidate_id(scope, window_index, event_index),
            schema_valid=False,
            timestamps_valid=False,
            evidence_valid=False,
            validation_errors=[issue],
            validator_version=VALIDATOR_VERSION,
        )
        events.append(
            RuntimeEventValidation(
                event_index=event_index,
                event_type=event.event_type,
                status=RuntimeValidationStatus.UNDETERMINED,
                validation=validation,
            )
        )
    return RuntimeWindowValidation(
        artifact_run_id=scope.artifact_run_id,
        window_index=window_index,
        window_start=report.window_start,
        window_end=report.window_end,
        status=RuntimeValidationStatus.UNDETERMINED,
        events=events,
        operational_issues=[issue],
    )


def _candidate_id(
    scope: RuntimeEvidenceScope,
    window_index: int,
    event_index: int,
) -> str:
    return f"runtime-{scope.artifact_run_id.hex}-{window_index:06d}-{event_index:03d}"


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
        not validation.schema_valid
        or not validation.timestamps_valid
        or not validation.evidence_valid
    ):
        return RuntimeValidationStatus.INVALID_EVIDENCE
    if (
        not validation.language_valid
        or validation.unsupported_critical_claim
        or not validation.critical_evidence_sufficient
        or requires_human_review(event_type)
    ):
        return RuntimeValidationStatus.HUMAN_REVIEW
    return RuntimeValidationStatus.VALIDATED


def _status_rank(status: RuntimeValidationStatus) -> int:
    return {
        RuntimeValidationStatus.VALIDATED: 0,
        RuntimeValidationStatus.HUMAN_REVIEW: 1,
        RuntimeValidationStatus.INVALID_EVIDENCE: 2,
        RuntimeValidationStatus.UNDETERMINED: 3,
    }[status]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "RuntimeEvidenceDigest",
    "RuntimeEvidenceScope",
    "RuntimeEventValidation",
    "RuntimeValidationStatus",
    "RuntimeWindowValidation",
    "postprocess_finalized_report",
]
