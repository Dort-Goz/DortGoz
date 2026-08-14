"""Patch 2 runtime validation → Ledger/risk/procedure policy testleri."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from dortgoz.agent.memory import Ledger
from dortgoz.config import settings
from dortgoz.domain.event import RiskLevel
from dortgoz.domain.evidence import EvidenceValidationResult
from dortgoz.events import (
    EventEvidenceRef,
    FrameReference,
    IncidentUpdate,
    WindowEvent,
    WindowReport,
)
from dortgoz.pipeline import runner
from dortgoz.services.runtime_policy import decide_runtime_policy
from dortgoz.services.runtime_postprocess import (
    RuntimeEventValidation,
    RuntimeEvidenceScope,
    RuntimeValidationStatus,
    RuntimeWindowValidation,
    postprocess_finalized_report,
)


def _event(
    *,
    frame_id: str = "f_000",
    event_type: str = "possible_theft",
    severity: str = "orta",
    timestamp: float = 5,
    claim: str = "Kişinin raftaki nesneyi aldığı görülüyor.",
    with_evidence: bool = True,
) -> WindowEvent:
    return WindowEvent(
        t=timestamp,
        desc="Bir kişi raftaki nesneyi alıyor.",
        evidence=(
            [
                EventEvidenceRef(
                    frame_id=frame_id,
                    timestamp=timestamp,
                    claim=claim,
                )
            ]
            if with_evidence
            else []
        ),
        severity_hint=severity,
        event_type=event_type,
    )


def _report(*events: WindowEvent) -> WindowReport:
    return WindowReport(
        window_start=0,
        window_end=30,
        anomaly_type="hirsizlik",
        summary="Pencerede dikkat gerektiren hareket var.",
        events=list(events) or [_event()],
    )


def _validation(
    report: WindowReport,
    *statuses: RuntimeValidationStatus,
) -> RuntimeWindowValidation:
    return RuntimeWindowValidation(
        artifact_run_id=uuid4(),
        window_index=0,
        window_start=report.window_start,
        window_end=report.window_end,
        status=max(statuses, key=_rank),
        events=[
            RuntimeEventValidation(
                event_index=index,
                event_type=event.event_type,
                status=status,
                validation=EvidenceValidationResult(
                    candidate_id=f"candidate-{index}",
                    schema_valid=status
                    in {
                        RuntimeValidationStatus.VALIDATED,
                        RuntimeValidationStatus.HUMAN_REVIEW,
                    },
                    timestamps_valid=status
                    in {
                        RuntimeValidationStatus.VALIDATED,
                        RuntimeValidationStatus.HUMAN_REVIEW,
                    },
                    evidence_valid=status
                    in {
                        RuntimeValidationStatus.VALIDATED,
                        RuntimeValidationStatus.HUMAN_REVIEW,
                    },
                    validator_version="fixture",
                ),
            )
            for index, (event, status) in enumerate(zip(report.events, statuses))
        ],
    )


def _rank(status: RuntimeValidationStatus) -> int:
    return {
        RuntimeValidationStatus.VALIDATED: 0,
        RuntimeValidationStatus.HUMAN_REVIEW: 1,
        RuntimeValidationStatus.INVALID_EVIDENCE: 2,
        RuntimeValidationStatus.UNDETERMINED: 3,
    }[status]


def _ingest(decision, ledger: Ledger):
    assert decision.ledger_report is not None
    return ledger.ingest(
        decision.ledger_report,
        uncertain=decision.review_reason,
    )


def _captured(
    *,
    frame_id: str = "f_000",
    timestamp: float = 5,
) -> dict[str, tuple[FrameReference, bytes]]:
    return {
        frame_id: (
            FrameReference(frame_id=frame_id, timestamp=timestamp),
            b"\xff\xd8runtime-policy\xff\xd9",
        )
    }


def _postprocessed_decision(
    tmp_path: Path,
    report: WindowReport,
    captured_frames: dict[str, tuple[FrameReference, bytes]],
):
    validation = postprocess_finalized_report(
        report=report,
        captured_frames=captured_frames,
        scope=RuntimeEvidenceScope.create("runtime-policy-hardening"),
        window_index=0,
        video_duration=30,
        workspace_root=tmp_path,
        evidence_root=tmp_path / "runtime-evidence",
    )
    assert validation is not None
    return validation, decide_runtime_policy(report, validation)


def test_validated_event_is_admitted_only_as_provisional_review() -> None:
    report = _report(_event())
    decision = decide_runtime_policy(
        report,
        _validation(report, RuntimeValidationStatus.VALIDATED),
    )

    updates = _ingest(decision, Ledger())

    assert decision.admitted_event_indices == (0,)
    assert decision.held_event_indices == ()
    assert updates[0].needs_review
    assert decision.risk is not None
    assert decision.risk.level == RiskLevel.REVIEW_REQUIRED
    assert decision.procedure is not None and decision.procedure.actions == []


def test_human_review_is_admitted_and_second_pass_cannot_clear_sticky_flag() -> None:
    report = _report(_event(event_type="possible_armed_incident"))
    decision = decide_runtime_policy(
        report,
        _validation(report, RuntimeValidationStatus.HUMAN_REVIEW),
    )
    ledger = Ledger()
    incident_id = _ingest(decision, ledger)[0].incident_id

    revised = ledger.apply_review(
        incident_id,
        {
            "anomaly_type": "saldiri",
            "risk": "kritik",
            "baslangic": "Bir kişi yaklaştı.",
            "zirve": "İki kişi itişti.",
            "sonuc": "Kişiler ayrıldı.",
            "zirve_t": 8,
            "belirsizlikler": [],
        },
    )

    assert revised is not None and revised.needs_review
    assert ledger.incidents[incident_id].needs_review
    assert revised.risk == "orta"


def test_critical_event_with_empty_evidence_is_not_admitted(tmp_path: Path) -> None:
    report = _report(
        _event(
            event_type="assault",
            severity="kritik",
            with_evidence=False,
        )
    )

    validation, decision = _postprocessed_decision(tmp_path, report, {})

    assert validation.events[0].status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert not validation.events[0].validation.evidence_valid
    assert decision.ledger_report is None


def test_human_review_label_with_invalid_evidence_is_not_admitted() -> None:
    report = _report(_event(event_type="possible_armed_incident", severity="kritik"))
    validation = _validation(report, RuntimeValidationStatus.HUMAN_REVIEW)
    item = validation.events[0]
    validation = validation.model_copy(
        update={
            "events": [
                item.model_copy(
                    update={
                        "validation": item.validation.model_copy(update={"evidence_valid": False})
                    }
                )
            ]
        }
    )

    decision = decide_runtime_policy(report, validation)

    assert decision.ledger_report is None
    assert decision.held_event_indices == (0,)
    assert "INVALID_EVIDENCE" in decision.review_reason


def test_human_review_trigger_with_invalid_frame_id_is_not_admitted(tmp_path: Path) -> None:
    report = _report(
        _event(
            frame_id="f_999",
            event_type="possible_armed_incident",
            severity="kritik",
        )
    )

    validation, decision = _postprocessed_decision(tmp_path, report, _captured())

    assert validation.events[0].status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert decision.ledger_report is None


def test_human_review_trigger_with_timestamp_mismatch_is_not_admitted(
    tmp_path: Path,
) -> None:
    report = _report(
        _event(
            event_type="possible_armed_incident",
            severity="kritik",
            timestamp=5,
        )
    )

    validation, decision = _postprocessed_decision(
        tmp_path,
        report,
        _captured(timestamp=6),
    )

    assert validation.events[0].status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert not validation.events[0].validation.timestamps_valid
    assert decision.ledger_report is None


def test_valid_but_critical_insufficient_human_review_is_provisionally_admitted(
    tmp_path: Path,
) -> None:
    report = _report(
        _event(
            event_type="possible_armed_incident",
            severity="kritik",
        )
    )

    validation, decision = _postprocessed_decision(tmp_path, report, _captured())
    ledger = Ledger()
    updates = _ingest(decision, ledger)

    item = validation.events[0]
    assert item.status == RuntimeValidationStatus.HUMAN_REVIEW
    assert item.validation.evidence_valid
    assert not item.validation.critical_evidence_sufficient
    assert decision.ledger_report is not None
    assert updates[0].needs_review


def test_invalid_human_review_does_not_change_open_incident(tmp_path: Path) -> None:
    ledger = Ledger(grace_windows=0)
    opened = ledger.ingest(_report(_event(severity="orta")))[0]
    initial = ledger.incidents[opened.incident_id]
    initial_risk = initial.risk
    invalid_report = _report(
        _event(
            event_type="assault",
            severity="kritik",
            with_evidence=False,
        )
    )
    validation, decision = _postprocessed_decision(tmp_path, invalid_report, {})

    ledger.require_review(decision.review_reason)

    assert validation.events[0].status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert decision.ledger_report is None
    assert ledger.quiet_streak == 0
    assert ledger.open_incident is not None
    assert ledger.open_incident.incident_id == opened.incident_id
    assert ledger.open_incident.risk == initial_risk
    assert ledger.open_incident.phase == "basladi"


@pytest.mark.parametrize(
    "status",
    [
        RuntimeValidationStatus.INVALID_EVIDENCE,
        RuntimeValidationStatus.UNDETERMINED,
    ],
)
def test_invalid_or_undetermined_event_is_not_admitted(
    status: RuntimeValidationStatus,
) -> None:
    report = _report(_event())
    decision = decide_runtime_policy(report, _validation(report, status))

    assert decision.ledger_report is None
    assert decision.admitted_event_indices == ()
    assert decision.held_event_indices == (0,)
    assert decision.risk is not None
    assert decision.risk.level == RiskLevel.UNDETERMINED
    assert decision.procedure is not None and decision.procedure.actions == []


@pytest.mark.parametrize(
    "status",
    [
        RuntimeValidationStatus.INVALID_EVIDENCE,
        RuntimeValidationStatus.UNDETERMINED,
    ],
)
def test_held_observation_does_not_advance_quiet_but_real_normal_does(
    status: RuntimeValidationStatus,
) -> None:
    ledger = Ledger(grace_windows=1)
    opened = ledger.ingest(_report(_event()))[0]
    held_report = _report(_event())
    held = decide_runtime_policy(
        held_report,
        _validation(held_report, status),
    )

    ledger.require_review(held.review_reason)

    assert held.ledger_report is None
    assert ledger.quiet_streak == 0
    assert ledger.open_incident is not None
    assert ledger.open_incident.incident_id == opened.incident_id

    normal = WindowReport(window_start=30, window_end=60, summary="Sahne sakin.")
    normal_policy = decide_runtime_policy(normal, None)
    assert normal_policy.ledger_report is not None
    assert ledger.ingest(normal_policy.ledger_report) == []
    assert ledger.quiet_streak == 1


def test_mixed_status_filters_by_event_index_without_changing_wire_report() -> None:
    report = _report(
        _event(frame_id="f_000", event_type="possible_theft", severity="orta"),
        _event(
            frame_id="f_001",
            event_type="fire_smoke",
            severity="kritik",
            timestamp=7,
        ),
    )
    before = report.model_dump(mode="json")
    decision = decide_runtime_policy(
        report,
        _validation(
            report,
            RuntimeValidationStatus.VALIDATED,
            RuntimeValidationStatus.INVALID_EVIDENCE,
        ),
    )

    assert decision.admitted_event_indices == (0,)
    assert decision.held_event_indices == (1,)
    assert decision.ledger_report is not None
    assert len(decision.ledger_report.events) == 1
    assert decision.ledger_report.events[0].event_type == "possible_theft"
    assert report.model_dump(mode="json") == before


def test_runtime_policy_does_not_fabricate_confidence_or_event_bounds() -> None:
    report = _report(_event())
    decision = decide_runtime_policy(
        report,
        _validation(report, RuntimeValidationStatus.VALIDATED),
    )

    payload = json.dumps(decision.ledger_report.model_dump(mode="json"))
    assert '"confidence"' not in payload
    assert '"start_time"' not in payload
    assert '"end_time"' not in payload


def test_incident_update_wire_contract_is_unchanged() -> None:
    assert set(IncidentUpdate.model_fields) == {
        "type",
        "incident_id",
        "t",
        "phase",
        "title",
        "anomaly_type",
        "risk",
        "detail",
        "thumbnail",
        "boxes",
        "needs_review",
        "review_reason",
        "olay_baslangic",
        "olay_bitis",
    }


class _Recorder:
    def __init__(self) -> None:
        self.payloads = []

    async def emit(self, payload) -> None:
        self.payloads.append(payload)


def _closed_incident() -> tuple[Ledger, object]:
    ledger = Ledger()
    ledger.ingest(_report(_event()), uncertain="ilk geçiş provisional")
    return ledger, ledger.finalize()[0]


def _review_payload(
    *,
    frame_id: str,
    event_type: str = "physical_fight",
    claim: str = "İki kişinin fiziksel olarak itiştiği görülüyor.",
) -> dict:
    return {
        "baslangic": "Bir kişi diğerine yaklaştı.",
        "zirve": "İki kişi fiziksel olarak itişti.",
        "sonuc": "Kişiler birbirinden ayrıldı.",
        "zirve_t": 6,
        "event_type": event_type,
        "risk": "kritik",
        "evidence": [
            {
                "frame_id": frame_id,
                "timestamp": 6,
                "claim": claim,
            }
        ],
        "belirsizlikler": [],
    }


@pytest.mark.asyncio
async def test_invalid_second_pass_never_calls_apply_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger, closed = _closed_incident()
    recorder = _Recorder()
    monkeypatch.setattr(settings, "incident_review", True)
    monkeypatch.setattr(settings, "runs_dir", tmp_path / "runs")
    monkeypatch.setattr(runner.windowing, "select_keyframes", lambda *_args: [6.0])

    async def fake_review(*_args, captured_frames, **_kwargs):
        captured_frames["f_000"] = (
            FrameReference(frame_id="f_000", timestamp=6),
            b"\xff\xd8second-pass\xff\xd9",
        )
        return _review_payload(
            frame_id="f_000",
            event_type="possible_armed_incident",
            claim="olay var",
        )

    def unexpected_apply(*_args, **_kwargs):
        raise AssertionError("invalid second pass apply_review çağırmamalı")

    monkeypatch.setattr(runner.interpret, "review_incident", fake_review)
    monkeypatch.setattr(ledger, "apply_review", unexpected_apply)

    await runner.review_if_closed(
        recorder,
        ledger,
        tmp_path / "clip.mp4",
        [1.0],
        closed,
        "",
        evidence_scope=RuntimeEvidenceScope.create("second-pass-invalid"),
        video_duration=30,
        window_count=1,
    )

    incident = ledger.incidents[closed.incident_id]
    assert incident.anomaly_type == "hirsizlik"
    assert incident.risk == "orta"
    assert incident.needs_review
    assert recorder.payloads[-1].status == "error"


@pytest.mark.asyncio
async def test_valid_second_pass_keeps_review_and_does_not_apply_vlm_risk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger, closed = _closed_incident()
    recorder = _Recorder()
    monkeypatch.setattr(settings, "incident_review", True)
    monkeypatch.setattr(settings, "runs_dir", tmp_path / "runs")
    monkeypatch.setattr(runner.windowing, "select_keyframes", lambda *_args: [6.0])

    async def fake_review(*_args, captured_frames, **_kwargs):
        captured_frames["f_000"] = (
            FrameReference(frame_id="f_000", timestamp=6),
            b"\xff\xd8second-pass\xff\xd9",
        )
        return _review_payload(frame_id="f_000")

    async def fake_context_size(_model):
        return None

    monkeypatch.setattr(runner.interpret, "review_incident", fake_review)
    monkeypatch.setattr(runner, "context_size", fake_context_size)

    await runner.review_if_closed(
        recorder,
        ledger,
        tmp_path / "clip.mp4",
        [1.0],
        closed,
        "",
        evidence_scope=RuntimeEvidenceScope.create("second-pass-valid"),
        video_duration=30,
        window_count=1,
    )

    incident = ledger.incidents[closed.incident_id]
    assert incident.anomaly_type == "kavga"
    assert incident.risk == "orta"
    assert incident.needs_review
    assert "ilk geçiş provisional" in incident.review_reason
    evidence_root = tmp_path / "runs" / "_runtime_evidence"
    assert not [item for item in evidence_root.rglob("*") if item.is_file()]
