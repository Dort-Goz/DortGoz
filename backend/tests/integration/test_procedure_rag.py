"""Yerel prosedür index/retrieval entegrasyon testleri."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dortgoz.domain.event import EventStatus, RiskAssessment, RiskLevel, VerifiedEvent
from dortgoz.domain.evidence import EvidenceItem, EvidenceValidationResult, VerifiedEventType
from dortgoz.repositories.procedure_index import LocalProcedureIndex
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


def test_hash_mismatch_rejects_local_document(tmp_path: Path) -> None:
    index = _index(tmp_path)
    (tmp_path / "demo.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        LocalProcedureIndex.load(tmp_path, tmp_path / "manifest.json")
    assert index.manifest.version == "fixture-v1"
