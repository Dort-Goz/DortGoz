from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dortgoz import session
from dortgoz.agent.memory import Ledger
from dortgoz.config import settings
from dortgoz.domain.evidence import ValidationIssue
from dortgoz.events import Event, EventEvidenceRef, FrameReference, WindowEvent, WindowReport
from dortgoz.pipeline import runner
from dortgoz.pipeline.ingest import MotionSample
from dortgoz.services import runtime_postprocess
from dortgoz.services.runtime_postprocess import (
    RuntimeEvidenceScope,
    RuntimeValidationStatus,
    postprocess_finalized_report,
)


def _report(
    *,
    frame_id: str = "f_000",
    timestamp: float = 12.5,
    window_start: float = 0,
    severity: str = "dusuk",
    event_type: str = "possible_theft",
) -> WindowReport:
    return WindowReport(
        window_start=window_start,
        window_end=window_start + 30,
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
                severity_hint=severity,
                event_type=event_type,
            )
        ],
    )


def _captured(timestamp: float = 12.5, payload: bytes | None = None):
    return {
        "f_000": (
            FrameReference(frame_id="f_000", timestamp=timestamp),
            payload or b"\xff\xd8runtime-jpeg\xff\xd9",
        )
    }


def _evidence_root(tmp_path: Path) -> Path:
    return tmp_path / "runs" / "_runtime_evidence"


def _postprocess(
    tmp_path: Path,
    report: WindowReport,
    *,
    scope: RuntimeEvidenceScope | None = None,
    captured_frames=None,
    window_index: int = 0,
):
    return postprocess_finalized_report(
        report=report,
        captured_frames=captured_frames or _captured(report.events[0].evidence[0].timestamp),
        scope=scope or RuntimeEvidenceScope.create("short-public-run"),
        window_index=window_index,
        video_duration=max(30, report.window_end),
        workspace_root=tmp_path,
        evidence_root=_evidence_root(tmp_path),
    )


def _assert_no_ephemeral_artifacts(tmp_path: Path) -> None:
    root = _evidence_root(tmp_path)
    if not root.exists():
        return
    assert not [item for item in root.rglob("*") if item.is_file() or item.is_symlink()]


def test_two_concurrent_runs_use_distinct_artifact_namespaces(tmp_path: Path) -> None:
    scopes = [RuntimeEvidenceScope.create(f"public-{index}") for index in range(2)]

    def execute(scope: RuntimeEvidenceScope):
        return _postprocess(tmp_path, _report(), scope=scope)

    with ThreadPoolExecutor(max_workers=2) as pool:
        sidecars = list(pool.map(execute, scopes))

    assert all(item is not None for item in sidecars)
    assert {item.artifact_run_id for item in sidecars if item is not None} == {
        scope.artifact_run_id for scope in scopes
    }
    assert len({scope.artifact_run_id for scope in scopes}) == 2
    _assert_no_ephemeral_artifacts(tmp_path)


def test_same_short_public_run_id_gets_distinct_internal_ids(tmp_path: Path) -> None:
    first_scope = RuntimeEvidenceScope.create("same-short-run")
    second_scope = RuntimeEvidenceScope.create("same-short-run")

    first = _postprocess(tmp_path, _report(), scope=first_scope)
    second = _postprocess(tmp_path, _report(), scope=second_scope)

    assert first is not None and second is not None
    assert first.artifact_run_id != second.artifact_run_id
    assert first_scope.public_run_id == second_scope.public_run_id
    _assert_no_ephemeral_artifacts(tmp_path)


def test_same_logical_frame_with_different_bytes_never_overwrites(tmp_path: Path) -> None:
    frame = FrameReference(frame_id="f_000", timestamp=12.5)

    class ConflictingFrames:
        def items(self):
            return [
                ("f_000", (frame, b"\xff\xd8first\xff\xd9")),
                ("f_000", (frame, b"\xff\xd8second\xff\xd9")),
            ]

    sidecar = _postprocess(tmp_path, _report(), captured_frames=ConflictingFrames())

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.UNDETERMINED
    assert "RUNTIME_EVIDENCE_IDENTITY_CONFLICT" in {
        issue.code for issue in sidecar.operational_issues
    }
    assert not sidecar.events[0].validation.permits_confirmation
    _assert_no_ephemeral_artifacts(tmp_path)


def test_symlink_escape_fails_before_write(tmp_path: Path) -> None:
    scope = RuntimeEvidenceScope.create("symlink-run")
    root = _evidence_root(tmp_path)
    outside = tmp_path / "outside"
    root.mkdir(parents=True)
    outside.mkdir()
    run_link = root / scope.artifact_run_id.hex
    try:
        run_link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"bu ortam symlink oluşturmaya izin vermiyor: {exc}")
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(run_link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if junction.returncode != 0:
            pytest.skip(f"bu ortam junction oluşturmaya izin vermiyor: {junction.stderr}")

    try:
        sidecar = _postprocess(tmp_path, _report(), scope=scope)

        assert sidecar is not None
        assert sidecar.status == RuntimeValidationStatus.UNDETERMINED
        assert "RUNTIME_EVIDENCE_PATH_UNSAFE" in {
            issue.code for issue in sidecar.operational_issues
        }
        assert list(outside.iterdir()) == []
    finally:
        if run_link.is_symlink():
            run_link.unlink()
        elif run_link.exists():
            run_link.rmdir()


def test_successful_validation_cleans_jpegs_and_keeps_only_digest(tmp_path: Path) -> None:
    payload = b"\xff\xd8runtime-jpeg\xff\xd9"
    sidecar = _postprocess(tmp_path, _report(), captured_frames=_captured(payload=payload))

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.VALIDATED
    evidence = sidecar.events[0].evidence_digests[0]
    assert evidence.hash_sha256 == hashlib.sha256(payload).hexdigest()
    assert "path" not in evidence.model_dump()
    assert sidecar.events[0].validation.evidence_valid
    assert not sidecar.events[0].validation.permits_confirmation
    _assert_no_ephemeral_artifacts(tmp_path)


def test_invalid_evidence_is_cleaned(tmp_path: Path) -> None:
    sidecar = _postprocess(tmp_path, _report(frame_id="f_999"))

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert "UNKNOWN_FRAME_ID" in {
        issue.code for issue in sidecar.events[0].validation.validation_errors
    }
    _assert_no_ephemeral_artifacts(tmp_path)


def test_human_review_evidence_is_cleaned(tmp_path: Path) -> None:
    sidecar = _postprocess(tmp_path, _report(event_type="possible_armed_incident"))

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.HUMAN_REVIEW
    assert not sidecar.events[0].validation.permits_confirmation
    _assert_no_ephemeral_artifacts(tmp_path)


def test_sha_mismatch_is_invalid_and_cleaned(tmp_path: Path, monkeypatch) -> None:
    original_validator = runtime_postprocess.validate_runtime_evidence

    def tampering_validator(**kwargs):
        target = tmp_path / kwargs["keyframes"][0].frame_path
        target.write_bytes(b"tampered")
        return original_validator(**kwargs)

    monkeypatch.setattr(runtime_postprocess, "validate_runtime_evidence", tampering_validator)
    sidecar = _postprocess(tmp_path, _report())

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert "EVIDENCE_FRAME_HASH_MISMATCH" in {
        issue.code for issue in sidecar.events[0].validation.validation_errors
    }
    _assert_no_ephemeral_artifacts(tmp_path)


def test_runtime_timestamp_uses_phase_b_tolerance_and_cleans(tmp_path: Path) -> None:
    sidecar = _postprocess(
        tmp_path,
        _report(timestamp=12.52),
        captured_frames=_captured(timestamp=12.5),
    )

    assert sidecar is not None
    validation = sidecar.events[0].validation
    assert sidecar.status == RuntimeValidationStatus.INVALID_EVIDENCE
    assert not validation.timestamps_valid
    assert "EVIDENCE_TIMESTAMP_MISMATCH" in {issue.code for issue in validation.validation_errors}
    _assert_no_ephemeral_artifacts(tmp_path)


def test_validator_exception_is_undetermined_and_next_window_can_continue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_validator = runtime_postprocess.validate_runtime_evidence
    calls = 0

    def flaky_validator(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("validator fixture failure")
        return original_validator(**kwargs)

    monkeypatch.setattr(runtime_postprocess, "validate_runtime_evidence", flaky_validator)
    first = _postprocess(tmp_path, _report(), window_index=0)
    second = _postprocess(tmp_path, _report(), window_index=1)

    assert first is not None and second is not None
    assert first.status == RuntimeValidationStatus.UNDETERMINED
    assert not first.events[0].validation.permits_confirmation
    assert second.status == RuntimeValidationStatus.VALIDATED
    assert calls == 2
    _assert_no_ephemeral_artifacts(tmp_path)


def test_disk_write_error_is_undetermined_and_fail_closed(tmp_path: Path, monkeypatch) -> None:
    def failing_write(*_args, **_kwargs):
        raise PermissionError("fixture write denied")

    monkeypatch.setattr(runtime_postprocess, "_write_complete_file", failing_write)
    sidecar = _postprocess(tmp_path, _report())

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.UNDETERMINED
    assert not sidecar.events[0].validation.permits_confirmation
    assert "RUNTIME_EVIDENCE_OPERATIONAL_FAILURE" in {
        issue.code for issue in sidecar.operational_issues
    }
    _assert_no_ephemeral_artifacts(tmp_path)


def test_cancellation_cleans_before_propagating(tmp_path: Path, monkeypatch) -> None:
    def cancelled_validator(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime_postprocess, "validate_runtime_evidence", cancelled_validator)

    with pytest.raises(asyncio.CancelledError):
        _postprocess(tmp_path, _report())

    _assert_no_ephemeral_artifacts(tmp_path)


def test_cleanup_warning_does_not_replace_validation_result(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    original_cleanup = runtime_postprocess._cleanup_tracker

    def cleanup_with_warning(tracker):
        issues = original_cleanup(tracker)
        issues.append(
            ValidationIssue(
                code="RUNTIME_EVIDENCE_CLEANUP_FAILED",
                field="ephemeral_evidence",
                message="Fixture cleanup warning.",
                severity="warning",
            )
        )
        return issues

    monkeypatch.setattr(runtime_postprocess, "_cleanup_tracker", cleanup_with_warning)
    with caplog.at_level("WARNING"):
        sidecar = _postprocess(tmp_path, _report())

    assert sidecar is not None
    assert sidecar.status == RuntimeValidationStatus.VALIDATED
    assert sidecar.operational_issues[0].code == "RUNTIME_EVIDENCE_CLEANUP_FAILED"
    assert "runtime_evidence_cleanup_failed" in caplog.text
    _assert_no_ephemeral_artifacts(tmp_path)


def test_normal_branch_has_no_validator_or_file_io(tmp_path: Path, monkeypatch) -> None:
    report = WindowReport(window_start=0, window_end=30, summary="Sahne sakin.")

    def unexpected_validator(**_kwargs):
        raise AssertionError("normal branch validator çağırmamalı")

    monkeypatch.setattr(runtime_postprocess, "validate_runtime_evidence", unexpected_validator)
    sidecar = postprocess_finalized_report(
        report=report,
        captured_frames=_captured(),
        scope=RuntimeEvidenceScope.create("normal-run"),
        window_index=0,
        video_duration=30,
        workspace_root=tmp_path,
        evidence_root=_evidence_root(tmp_path),
    )

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


def test_ledger_input_and_behavior_are_unchanged(tmp_path: Path) -> None:
    report = _report(severity="orta")
    control = report.model_copy(deep=True)
    before = report.model_dump(mode="json")

    sidecar = _postprocess(tmp_path, report)
    actual_updates = Ledger(grace_windows=0).ingest(report)
    control_updates = Ledger(grace_windows=0).ingest(control)

    assert sidecar is not None
    assert report.model_dump(mode="json") == before
    assert len(actual_updates) == len(control_updates) == 1
    assert actual_updates[0].model_dump(exclude={"incident_id"}) == control_updates[0].model_dump(
        exclude={"incident_id"}
    )


def test_sidecar_has_no_confirmation_confidence_bounds_or_durable_path(tmp_path: Path) -> None:
    sidecar = _postprocess(tmp_path, _report())

    assert sidecar is not None
    payload = json.dumps(sidecar.model_dump(mode="json"))
    assert '"confidence"' not in payload
    assert '"start_time"' not in payload
    assert '"end_time"' not in payload
    assert '"confirmed"' not in payload
    assert '"frame_path"' not in payload
    assert not sidecar.events[0].validation.permits_confirmation


@pytest.mark.asyncio
async def test_runner_continues_after_validator_operational_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
    monkeypatch.setattr(settings, "escalate_p", 0.0)
    monkeypatch.setattr(settings, "incident_review", False)

    async def fake_probe_duration(_path):
        return 60.0

    async def fake_motion_profile(_path, _fps):
        return [
            MotionSample(t=0, changed=1, fg=0, mad=0),
            MotionSample(t=30, changed=1, fg=0, mad=0),
        ]

    async def fake_context_size(_model):
        return None

    async def fake_interpret(_path, window, keyframes, **kwargs):
        timestamp = keyframes[0]
        kwargs["captured_frames"].update(_captured(timestamp=timestamp))
        return _report(timestamp=timestamp, window_start=window[0])

    original_validator = runtime_postprocess.validate_runtime_evidence
    validator_calls = 0

    def flaky_validator(**kwargs):
        nonlocal validator_calls
        validator_calls += 1
        if validator_calls == 1:
            raise RuntimeError("first-window validator failure")
        return original_validator(**kwargs)

    monkeypatch.setattr(runner.ingest, "probe_duration", fake_probe_duration)
    monkeypatch.setattr(runner.ingest, "motion_profile", fake_motion_profile)
    monkeypatch.setattr(runner.ingest, "prefetch_frames", lambda *_args: None)
    monkeypatch.setattr(runner, "context_size", fake_context_size)
    monkeypatch.setattr(runner, "interpret_window", fake_interpret)
    monkeypatch.setattr(runtime_postprocess, "validate_runtime_evidence", flaky_validator)
    monkeypatch.setattr(
        runner.windowing,
        "select_keyframes",
        lambda _profile, start, _end, _count: [start + 12.5],
    )

    emitted = []

    class FakeManager:
        async def broadcast(self, event) -> None:
            emitted.append(event.payload)

    session.clear()
    await runner.run_video(FakeManager(), "clip.mp4", "validator-failure-run")

    assert validator_calls == 2
    assert session.current() is not None
    assert len(session.current().reports) == 2
    assert emitted[-1].type == "run_status"
    assert emitted[-1].state == "done"
    _assert_no_ephemeral_artifacts(tmp_path)
    session.clear()


@pytest.mark.asyncio
async def test_escalation_calls_postprocess_only_for_final_report(
    tmp_path: Path, monkeypatch
) -> None:
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
        kwargs["captured_frames"].update(_captured())
        if kwargs.get("model") == settings.second_opinion_model:
            return _report()
        kwargs["stats"]["durum_p"] = 0.5
        return WindowReport(window_start=window[0], window_end=window[1], summary="Sakin.")

    validated_reports: list[WindowReport] = []

    def fake_postprocess(**kwargs):
        validated_reports.append(kwargs["report"])
        assert isinstance(kwargs["scope"], RuntimeEvidenceScope)
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
    await runner.run_video(FakeManager(), "clip.mp4", "same-short-run")

    assert len(validated_reports) == 1
    assert validated_reports[0].events
    session.clear()

    # Canlı kipte ikinci görüş çalışmaz: aynı kurulum olaysız kalmalıdır.
    # Tırmandırma da aynı modeli çağırır; onu kapatıp ikinci görüşü izole ediyoruz.
    monkeypatch.setattr(settings, "escalate_p", 0.0)
    validated_reports.clear()
    await runner.run_video(FakeManager(), "clip.mp4", "canli-run", live=True)
    assert len(validated_reports) == 1
    assert not validated_reports[0].events, "canlı kipte ikinci görüş çağrıldı"

    # Kaçış kapısı açıkken canlıda da çalışır.
    monkeypatch.setattr(settings, "live_second_opinion", True)
    validated_reports.clear()
    await runner.run_video(FakeManager(), "clip.mp4", "canli-acik-run", live=True)
    assert validated_reports[0].events
    assert session.current() is not None
    assert session.current().reports == validated_reports
    assert not hasattr(session.current(), "validation_sidecars")
    session.clear()
