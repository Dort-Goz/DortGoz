"""Authoritative runner finalized WindowReport evidence gate testleri."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dortgoz import session
from dortgoz.config import settings
from dortgoz.events import Event, EventEvidenceRef, FrameReference, WindowEvent, WindowReport
from dortgoz.pipeline import runner
from dortgoz.pipeline.ingest import MotionSample
from dortgoz.services import runtime_postprocess
from dortgoz.services.runtime_postprocess import (
    RuntimeValidationStatus,
    materialize_runtime_evidence,
    postprocess_finalized_report,
    validate_materialized_report,
)


def _report(*, frame_id: str = "f_000", timestamp: float = 12.5) -> WindowReport:
    return WindowReport(
        window_start=0,
        window_end=30,
        anomaly_type="hirsizlik",
        summary="Bir kişi raftaki nesneyi alıp uzaklaşıyor.",
        events=[
            WindowEvent(
                t=timestamp,
                desc="Bir kişi raftaki nesneyi alıyor.",
                evidence=[
                    EventEvidenceRef(
                        frame_id=frame_id,
                        timestamp=timestamp,
                        claim="Kişinin raftaki nesneyi aldığı görülüyor.",
                    )
                ],
                severity_hint="dusuk",
                event_type="possible_theft",
            )
        ],
    )


def _captured(timestamp: float = 12.5) -> dict[str, tuple[FrameReference, bytes]]:
    return {
        "f_000": (
            FrameReference(frame_id="f_000", timestamp=timestamp),
            b"\xff\xd8runtime-jpeg\xff\xd9",
        )
    }


def _postprocess(
    tmp_path: Path,
    report: WindowReport,
    *,
    captured_frames: dict[str, tuple[FrameReference, bytes]] | None = None,
):
    return postprocess_finalized_report(
        report=report,
        captured_frames=captured_frames or _captured(),
        run_id="operator-supplied-name",
        window_index=0,
        video_duration=30,
        workspace_root=tmp_path,
        evidence_root=tmp_path / "runs",
    )


def test_valid_event_materializes_hashes_and_runs_validator(tmp_path: Path) -> None:
    sidecar = _postprocess(tmp_path, _report())

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.VALIDATED
    event = sidecar.events[0]
    frame = event.materialized_frames[0]
    target = tmp_path / frame.frame_path
    assert target.is_file()
    assert frame.hash_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert event.validation.evidence_valid
    assert event.validation.validator_version == "task-09-validator-v1"


def test_unknown_frame_id_fails_closed_without_confirming(tmp_path: Path) -> None:
    report = _report(frame_id="f_999")
    wire_before = report.model_dump(mode="json")

    sidecar = _postprocess(tmp_path, report)

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert "UNKNOWN_FRAME_ID" in {
        issue.code for issue in sidecar.events[0].validation.validation_errors
    }
    assert not sidecar.events[0].validation.permits_confirmation
    assert report.model_dump(mode="json") == wire_before
    assert "confirmed" not in json.dumps(wire_before)


def test_runtime_timestamp_uses_phase_b_tolerance(tmp_path: Path) -> None:
    sidecar = _postprocess(tmp_path, _report(timestamp=12.52))

    assert sidecar is not None
    validation = sidecar.events[0].validation
    assert sidecar.status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert not validation.timestamps_valid
    assert "EVIDENCE_TIMESTAMP_MISMATCH" in {issue.code for issue in validation.validation_errors}


def test_sha_mismatch_is_not_silently_accepted(tmp_path: Path) -> None:
    report = _report()
    frames = materialize_runtime_evidence(
        report=report,
        captured_frames=_captured(),
        run_id="sha-test",
        window_index=0,
        workspace_root=tmp_path,
        evidence_root=tmp_path / "runs",
    )
    (tmp_path / frames[0].frame_path).write_bytes(b"tampered")

    sidecar = validate_materialized_report(
        report=report,
        materialized_frames=frames,
        run_id="sha-test",
        window_index=0,
        video_duration=30,
        workspace_root=tmp_path,
    )

    assert sidecar.status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert "EVIDENCE_FRAME_HASH_MISMATCH" in {
        issue.code for issue in sidecar.events[0].validation.validation_errors
    }


def test_normal_branch_has_no_validator_or_file_io(tmp_path: Path, monkeypatch) -> None:
    report = WindowReport(window_start=0, window_end=30, summary="Sahne sakin.")

    def unexpected_validator(**_kwargs):
        raise AssertionError("normal branch validator çağırmamalı")

    monkeypatch.setattr(runtime_postprocess, "validate_runtime_evidence", unexpected_validator)
    sidecar = _postprocess(tmp_path, report)

    assert sidecar is None
    assert not (tmp_path / "runs").exists()


def test_ws_window_report_snapshot_is_unchanged(tmp_path: Path) -> None:
    report = _report()
    before = Event(seq=7, ts=123.0, payload=report).model_dump(mode="json")

    _postprocess(tmp_path, report)
    after = Event(seq=7, ts=123.0, payload=report).model_dump(mode="json")

    assert after == before
    assert set(after["payload"]) == {
        "type",
        "window_start",
        "window_end",
        "anomaly_type",
        "summary",
        "events",
        "uncertainties",
    }
    assert set(after["payload"]["events"][0]) == {
        "t",
        "desc",
        "evidence",
        "severity_hint",
        "event_type",
    }


def test_sidecar_does_not_fabricate_confirmation_confidence_or_event_bounds(
    tmp_path: Path,
) -> None:
    sidecar = _postprocess(tmp_path, _report())

    assert sidecar is not None
    payload = json.dumps(sidecar.model_dump(mode="json"))
    assert '"confidence"' not in payload
    assert '"start_time"' not in payload
    assert '"end_time"' not in payload
    assert '"confirmed"' not in payload
    assert not sidecar.events[0].validation.permits_confirmation


@pytest.mark.asyncio
async def test_escalation_validates_only_the_final_report(tmp_path: Path, monkeypatch) -> None:
    media_dir = tmp_path / "media"
    runs_dir = tmp_path / "runs"
    media_dir.mkdir()
    (media_dir / "clip.mp4").write_bytes(b"fixture")
    monkeypatch.setattr(settings, "media_dir", media_dir)
    monkeypatch.setattr(settings, "runs_dir", runs_dir)
    monkeypatch.setattr(settings, "dynamic_windows", False)
    monkeypatch.setattr(settings, "candidate_screening", False)
    monkeypatch.setattr(settings, "detector_enabled", False)
    monkeypatch.setattr(settings, "motion_gate_adaptive", False)
    monkeypatch.setattr(settings, "motion_gate", 0.0)
    monkeypatch.setattr(settings, "escalate_p", 0.1)
    monkeypatch.setattr(settings, "incident_review", False)

    async def fake_probe_duration(_path):
        return 30.0

    async def fake_motion_profile(_path, _fps):
        return [MotionSample(t=0, changed=1, fg=0, mad=0)]

    async def fake_context_size(_model):
        return None

    async def fake_interpret(_path, window, _keyframes, **kwargs):
        captured = kwargs["captured_frames"]
        captured.update(_captured())
        if kwargs.get("think"):
            return _report()
        kwargs["stats"]["durum_p"] = 0.5
        return WindowReport(window_start=window[0], window_end=window[1], summary="Sakin.")

    validated_reports: list[WindowReport] = []

    def fake_postprocess(**kwargs):
        validated_reports.append(kwargs["report"])
        assert set(kwargs["captured_frames"]) == {"f_000"}
        return None

    monkeypatch.setattr(runner.ingest, "probe_duration", fake_probe_duration)
    monkeypatch.setattr(runner.ingest, "motion_profile", fake_motion_profile)
    monkeypatch.setattr(runner.ingest, "prefetch_frames", lambda *_args: None)
    monkeypatch.setattr(runner, "context_size", fake_context_size)
    monkeypatch.setattr(runner, "interpret_window", fake_interpret)
    monkeypatch.setattr(runner, "postprocess_finalized_report", fake_postprocess)
    monkeypatch.setattr(runner.windowing, "select_keyframes", lambda *_args: [12.5])

    class FakeManager:
        async def broadcast(self, _event) -> None:
            return None

    session.clear()
    await runner.run_video(FakeManager(), "clip.mp4", "escalation-run")

    assert len(validated_reports) == 1
    assert validated_reports[0].events
    assert session.current() is not None
    assert session.current().reports == validated_reports
    session.clear()
