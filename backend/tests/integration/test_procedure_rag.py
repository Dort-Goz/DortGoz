from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from dortgoz.domain.event import EventStatus, RiskAssessment, RiskLevel, VerifiedEvent
from dortgoz.domain.evidence import EvidenceItem, EvidenceValidationResult, VerifiedEventType
from dortgoz.services.procedure_index import LocalProcedureIndex
from dortgoz.services.procedure_service import ProcedureService


def _event() -> VerifiedEvent:
    evidence = EvidenceItem(evidence_id="evidence-procedure", timestamp=4, frame_id="frame-procedure", frame_path="runs/procedure.jpg", clip_path="runs/procedure.mp4", claim="Karede duman benzeri görünüm gözleniyor.", source_model="fixture", validated=True)
    validation = EvidenceValidationResult(candidate_id="candidate-procedure", schema_valid=True, timestamps_valid=True, evidence_valid=True, validated_evidence=[evidence], validator_version="fixture")
    return VerifiedEvent(event_id="event-procedure", analysis_id="analysis-procedure", video_id="video-procedure", candidate_id="candidate-procedure", status=EventStatus.CONFIRMED, event_type=VerifiedEventType.FIRE_SMOKE, start_time=2, peak_time=4, end_time=6, confidence=0.9, validation=validation, evidence=[evidence])


def _index(tmp_path: Path) -> LocalProcedureIndex:
    document = tmp_path / "demo.md"
    content = b"# Ornek prosedur\n\nOperatore bildir."
    document.write_bytes(content)
    (tmp_path / "manifest.json").write_text(json.dumps({"version": "fixture-v1", "documents": [{"document_id": "demo-fire", "path": "demo.md", "version": "1.0", "content_hash": hashlib.sha256(content).hexdigest(), "valid_from": "2026-01-01", "approved_for_demo": True, "event_types": ["fire_smoke"], "risk_levels": ["critical"], "sections": [{"section": "1", "action": "Operatöre acil değerlendirme öner."}]}]}), encoding="utf-8")
    return LocalProcedureIndex.load(tmp_path, tmp_path / "manifest.json")


def test_local_hash_cited_procedure_is_returned_only_for_actionable_risk(tmp_path: Path) -> None:
    service = ProcedureService(_index(tmp_path))
    risk = RiskAssessment(level=RiskLevel.CRITICAL, reasons=["fixture"], rule_ids=["fixture"], ruleset_version="fixture")

    recommendation = service.recommend(_event(), risk)

    assert recommendation.actions[0].requires_human_approval
    assert recommendation.actions[0].content_hash == recommendation.sources[0].content_hash
    assert recommendation.sources[0].document_id == "demo-fire"
    blocked = service.recommend(_event(), risk.model_copy(update={"level": RiskLevel.REVIEW_REQUIRED, "review_required": True}))
    assert not blocked.actions and blocked.reason


def test_repo_demo_procedure_manifest_is_hash_valid() -> None:
    root = Path(__file__).resolve().parents[3] / "data" / "procedures"
    index = LocalProcedureIndex.load(root, root / "manifest.json")
    assert any(document.approved_for_demo for document in index.manifest.documents)
    assert index.usable_documents(), "onaylı belge var ama bugün geçerli değil"
    assert index.usable_documents(on_date=date(2026, 10, 4)), (
        "prosedür belgesi TEKNOFEST ödül töreni bitmeden geçersizleşiyor"
    )


def test_expired_document_is_not_usable(tmp_path: Path) -> None:
    index = _index(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest["documents"][0]["valid_until"] = "2026-01-02"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    expired = LocalProcedureIndex.load(tmp_path, tmp_path / "manifest.json")

    assert expired.manifest.documents[0].approved_for_demo
    assert not expired.usable_documents(on_date=date(2026, 8, 25))
    assert not expired.find(VerifiedEventType.FIRE_SMOKE, RiskLevel.CRITICAL, on_date=date(2026, 8, 25))
    assert index.usable_documents(on_date=date(2026, 8, 25))


def test_hash_mismatch_rejects_local_document(tmp_path: Path) -> None:
    index = _index(tmp_path)
    (tmp_path / "demo.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        LocalProcedureIndex.load(tmp_path, tmp_path / "manifest.json")
    assert index.manifest.version == "fixture-v1"


@pytest.mark.parametrize("level", [RiskLevel.REVIEW_REQUIRED, RiskLevel.UNDETERMINED])
def test_runtime_guard_never_returns_procedure_actions(level: RiskLevel) -> None:
    risk = RiskAssessment(
        level=level,
        reasons=["runtime guard"],
        review_required=True,
        ruleset_version="runtime-policy-v1",
    )

    recommendation = ProcedureService.recommend_runtime(risk)

    assert recommendation.actions == []
    assert recommendation.sources == []
    assert recommendation.reason
