"""Deterministik evidence doğrulayıcı; modele veya ağa yeniden başvurmaz."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..agent.state import EventAgentState
from ..domain.evidence import (
    EvidenceItem,
    EvidenceValidationResult,
    ValidationIssue,
    VerifiedEventType,
    VLMStatus,
)

VALIDATOR_VERSION = "task-09-validator-v1"

CRITICAL_EVENT_TYPES = {
    VerifiedEventType.ASSAULT,
    VerifiedEventType.FALL,
    VerifiedEventType.PERSON_ON_GROUND,
    VerifiedEventType.FIRE_SMOKE,
    VerifiedEventType.VEHICLE_COLLISION,
}
UNSUPPORTED_CRITICAL_TERMS = re.compile(
    r"\b(kimlik\w*|isim\w*|suçlu\w*|fail\w*|niyet\w*|kasıt\w*|yaral\w*|silah\w*|bıçak\w*|tabanca\w*|öld\w*)\b",
    flags=re.IGNORECASE,
)
NON_TURKISH_TERMS = re.compile(
    r"\b(the|and|with|person|people|weapon|injury|assault|running|appears|seen)\b",
    flags=re.IGNORECASE,
)
VAGUE_CLAIMS = {"olay var", "hareket var", "anormallik var", "normal değil"}


def validate_evidence(
    state: EventAgentState, *, workspace_root: Path | None = None
) -> EvidenceValidationResult:
    """State'teki öneriyi gerçek keyframe/clip ve güvenlik kurallarına karşı sınar.

    ``workspace_root`` verilirse VLM akışında evidence dosyalarının varlığı ve
    SHA-256 bütünlüğü zorunlu hale gelir. Mock-only akışların sözleşmesini
    korumak için dosya denetimi açıkça opt-in'dir.
    """

    status = state.proposal_status
    issues: list[ValidationIssue] = []
    if status is None or state.proposal_event_type is None:
        return _missing_proposal(state, issues)

    root = workspace_root.resolve() if workspace_root is not None else None
    result = state.vlm_result
    schema_valid = _schema_valid(state, issues)
    timestamps_valid = _timestamps_valid(state, issues)
    artifact_valid = _context_artifact_valid(state, root, issues)
    frame_by_id = {frame.frame_id: frame for frame in state.keyframes}
    validated, claims_valid, language_valid, unsupported_claim = _validate_claims(
        state, frame_by_id, root, issues
    )

    if status == VLMStatus.UNCERTAIN:
        issues.append(
            ValidationIssue(
                code="UNCERTAIN_RESULT",
                field="status",
                message="Belirsiz model sonucu terminal hüküm üretemez.",
            )
        )

    critical_evidence_sufficient = True
    if result is not None and status == VLMStatus.CONFIRMED and result.event_type in CRITICAL_EVENT_TYPES:
        unique_frames = {claim.frame_id for claim in state.proposal_evidence}
        critical_evidence_sufficient = len(unique_frames) >= 2
        if not critical_evidence_sufficient:
            issues.append(
                ValidationIssue(
                    code="CRITICAL_EVIDENCE_INSUFFICIENT",
                    field="evidence",
                    message="Kritik olay otomatik doğrulama için iki farklı frame evidence ister.",
                )
            )

    evidence_valid = (
        claims_valid and artifact_valid and bool(validated)
        if status == VLMStatus.CONFIRMED
        else status == VLMStatus.REJECTED
    )
    return EvidenceValidationResult(
        candidate_id=state.candidate_id,
        schema_valid=schema_valid,
        timestamps_valid=timestamps_valid,
        evidence_valid=evidence_valid,
        language_valid=language_valid,
        unsupported_critical_claim=unsupported_claim,
        critical_evidence_sufficient=critical_evidence_sufficient,
        validation_errors=issues,
        validated_evidence=validated,
        validator_version=VALIDATOR_VERSION,
    )


def _missing_proposal(
    state: EventAgentState, issues: list[ValidationIssue]
) -> EvidenceValidationResult:
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


def _schema_valid(state: EventAgentState, issues: list[ValidationIssue]) -> bool:
    result = state.vlm_result
    if result is not None and result.candidate_id != state.candidate_id:
        issues.append(
            ValidationIssue(
                code="VLM_CANDIDATE_MISMATCH",
                field="candidate_id",
                message="VLM sonucu doğrulanan candidate ile eşleşmiyor.",
            )
        )
        return False
    return True


def _timestamps_valid(state: EventAgentState, issues: list[ValidationIssue]) -> bool:
    result = state.vlm_result
    if result is None or state.proposal_status != VLMStatus.CONFIRMED:
        return True
    values = (result.start_time, result.peak_time, result.end_time)
    if any(value is None for value in values):
        issues.append(
            ValidationIssue(
                code="TIMESTAMP_MISSING",
                field="start_time/peak_time/end_time",
                message="Confirmed VLM sonucu tüm olay zamanlarını taşımalıdır.",
            )
        )
        return False
    start, peak, end = values
    assert start is not None and peak is not None and end is not None
    lower = state.context_clip.clip_start if state.context_clip else 0.0
    upper = state.context_clip.clip_end if state.context_clip else state.video_duration
    valid = lower <= start <= peak <= end <= upper
    if not valid:
        issues.append(
            ValidationIssue(
                code="TIMESTAMP_OUT_OF_BOUNDS",
                field="start_time/peak_time/end_time",
                message="Olay zamanları analiz bağlamının dışında veya sırasız.",
            )
        )
    return valid


def _context_artifact_valid(
    state: EventAgentState, root: Path | None, issues: list[ValidationIssue]
) -> bool:
    if state.vlm_result is None or root is None:
        return True
    context = state.context_clip
    if context is None:
        issues.append(
            ValidationIssue(
                code="CONTEXT_CLIP_MISSING",
                field="context_clip",
                message="Yerel VLM sonucu hash'li context clip olmadan doğrulanamaz.",
            )
        )
        return False
    return _artifact_matches(root, context.clip_path, context.hash_sha256, "clip", issues)


def _validate_claims(
    state: EventAgentState,
    frame_by_id: dict[str, object],
    root: Path | None,
    issues: list[ValidationIssue],
) -> tuple[list[EvidenceItem], bool, bool, bool]:
    validated: list[EvidenceItem] = []
    claims_valid = True
    language_valid = True
    unsupported_claim = False
    for index, claim in enumerate(state.proposal_evidence, start=1):
        frame = frame_by_id.get(claim.frame_id)
        if frame is None:
            _issue(issues, "UNKNOWN_FRAME_ID", index, "frame_id", "Evidence claim gerçek bir keyframe'e bağlı değil.")
            claims_valid = False
            continue
        frame_timestamp = getattr(frame, "timestamp")
        frame_path = getattr(frame, "frame_path")
        frame_hash = getattr(frame, "hash_sha256")
        if not 0 <= claim.timestamp <= state.video_duration or abs(frame_timestamp - claim.timestamp) > 0.5:
            _issue(issues, "EVIDENCE_TIMESTAMP_MISMATCH", index, "timestamp", "Evidence zamanı keyframe veya video sınırıyla eşleşmiyor.")
            claims_valid = False
            continue
        if root is not None and not _artifact_matches(root, frame_path, frame_hash, "frame", issues, index):
            claims_valid = False
            continue
        claim_text = claim.claim.strip()
        if _is_vague(claim_text):
            _issue(issues, "EVIDENCE_CLAIM_VAGUE", index, "claim", "Evidence claim gözlenebilir ayrıntı içermiyor.")
            claims_valid = False
            continue
        if NON_TURKISH_TERMS.search(claim_text):
            _issue(issues, "EVIDENCE_CLAIM_LANGUAGE", index, "claim", "Evidence claim Türkçe operasyonel dilde olmalı.")
            claims_valid = False
            language_valid = False
            continue
        if UNSUPPORTED_CRITICAL_TERMS.search(claim_text):
            _issue(issues, "UNSUPPORTED_CRITICAL_CLAIM", index, "claim", "Kimlik, niyet, yaralanma veya silah iddiası doğrulanmış evidence olmadan kullanılamaz.")
            claims_valid = False
            unsupported_claim = True
            continue
        context = state.context_clip
        clip_path = context.clip_path if context else f"runs/{state.analysis_id}/{state.candidate_id}/candidate.mp4"
        validated.append(
            EvidenceItem(
                evidence_id=f"{state.candidate_id}-evidence-{index}",
                timestamp=claim.timestamp,
                frame_id=claim.frame_id,
                frame_path=frame_path,
                clip_path=clip_path,
                claim=claim_text,
                source_model=(state.vlm_result.model_id if state.vlm_result else "mock-cv-v1"),
                validated=True,
                validation_notes=["frame_id, timestamp, path ve hash doğrulandı"],
            )
        )
    return validated, claims_valid, language_valid, unsupported_claim


def _artifact_matches(
    root: Path,
    relative_path: str,
    expected_hash: str,
    artifact_kind: str,
    issues: list[ValidationIssue],
    index: int | None = None,
) -> bool:
    target = (root / relative_path).resolve()
    field = f"evidence[{index}].frame_path" if index is not None else "context_clip.clip_path"
    if not target.is_relative_to(root) or not target.is_file():
        issues.append(
            ValidationIssue(
                code=f"EVIDENCE_{artifact_kind.upper()}_NOT_FOUND",
                field=field,
                message=f"Evidence {artifact_kind} çalışma kökü içinde bulunamadı.",
            )
        )
        return False
    if _file_hash(target) != expected_hash:
        issues.append(
            ValidationIssue(
                code=f"EVIDENCE_{artifact_kind.upper()}_HASH_MISMATCH",
                field=field,
                message=f"Evidence {artifact_kind} hash'i kaydedilen referansla eşleşmiyor.",
            )
        )
        return False
    return True


def _issue(
    issues: list[ValidationIssue], code: str, index: int, field: str, message: str
) -> None:
    issues.append(ValidationIssue(code=code, field=f"evidence[{index}].{field}", message=message))


def _is_vague(claim: str) -> bool:
    normalized = claim.casefold().rstrip(".!?")
    return normalized in VAGUE_CLAIMS or len(re.findall(r"\w+", normalized)) < 3


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
