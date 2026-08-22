from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dortgoz import session
from dortgoz.config import settings
from dortgoz.domain.evidence import EvidenceValidationResult
from dortgoz.events import EventEvidenceRef, FrameReference, WindowEvent, WindowReport
from dortgoz.pipeline import candidate_model, runner
from dortgoz.pipeline.ingest import MotionSample
from dortgoz.pipeline.perception import WindowPerception
from dortgoz.services.runtime_metrics import CanonicalRunMetrics
from dortgoz.services.runtime_postprocess import (
    RuntimeEventValidation,
    RuntimeValidationStatus,
    RuntimeWindowValidation,
)
from dortgoz.ws import replay_jsonl


@pytest.fixture(autouse=True)
def _clear_session():
    session.clear()
    yield
    session.clear()


class FakeManager:
    def __init__(self) -> None:
        self.payloads = []

    async def broadcast(self, event) -> None:
        self.payloads.append(event.payload)


def _configure_runner(monkeypatch, tmp_path: Path, *, incident_review: bool) -> Path:
    media_dir = tmp_path / "media"
    runs_dir = tmp_path / "runs"
    media_dir.mkdir()
    video = media_dir / "clip.mp4"
    video.write_bytes(b"fixture")
    monkeypatch.setattr(settings, "media_dir", media_dir)
    monkeypatch.setattr(settings, "runs_dir", runs_dir)
    monkeypatch.setattr(settings, "dynamic_windows", False)
    monkeypatch.setattr(settings, "motion_gate_adaptive", False)
    monkeypatch.setattr(settings, "motion_gate", 0.0)
    monkeypatch.setattr(settings, "candidate_adaptive_threshold", False)
    monkeypatch.setattr(settings, "escalate_p", 0.0)
    monkeypatch.setattr(settings, "incident_review", incident_review)
    monkeypatch.setattr(settings, "keyframes_per_window", 1)
    monkeypatch.setattr(runner.ingest, "prefetch_frames", lambda *_args: None)

    async def fake_context_size(_model):
        return None

    async def fake_thumbnail(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runner, "context_size", fake_context_size)
    monkeypatch.setattr(runner, "save_thumbnail", fake_thumbnail)
    monkeypatch.setattr(
        runner.windowing,
        "select_keyframes",
        lambda _profile, start, _end, _count: [start + 5.0],
    )
    return video


def _report(window: tuple[float, float]) -> WindowReport:
    timestamp = window[0] + 5.0
    return WindowReport(
        window_start=window[0],
        window_end=window[1],
        anomaly_type="hirsizlik",
        summary="Kişi raftaki nesneyi alıyor.",
        events=[
            WindowEvent(
                t=timestamp,
                desc="Kişi raftaki nesneyi alıyor.",
                evidence=[
                    EventEvidenceRef(
                        frame_id="f_000",
                        timestamp=timestamp,
                        claim="Kişinin raftaki nesneyi aldığı görülüyor.",
                    )
                ],
                severity_hint="orta",
                event_type="possible_theft",
            )
        ],
    )


def _validation(
    report: WindowReport,
    status: RuntimeValidationStatus,
    *,
    window_index: int = 0,
) -> RuntimeWindowValidation:
    technical_valid = status in {
        RuntimeValidationStatus.VALIDATED,
        RuntimeValidationStatus.HUMAN_REVIEW,
    }
    return RuntimeWindowValidation(
        artifact_run_id=uuid4(),
        window_index=window_index,
        window_start=report.window_start,
        window_end=report.window_end,
        status=status,
        events=[
            RuntimeEventValidation(
                event_index=0,
                event_type=report.events[0].event_type,
                status=status,
                validation=EvidenceValidationResult(
                    candidate_id=f"metrics-{window_index}",
                    schema_valid=technical_valid,
                    timestamps_valid=technical_valid,
                    evidence_valid=technical_valid,
                    validator_version="fixture",
                ),
            )
        ],
    )


def _metrics_rows(runs_dir: Path, run_id: str) -> list[dict]:
    rows = [
        json.loads(line)
        for line in (runs_dir / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return [row["payload"] for row in rows if row["payload"]["type"] == "run_metrics"]


@pytest.mark.asyncio
async def test_run_metrics_count_real_skip_rescue_and_two_qwen_calls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_runner(monkeypatch, tmp_path, incident_review=False)
    monkeypatch.setattr(settings, "candidate_screening", True)
    monkeypatch.setattr(settings, "candidate_model_manifest", "fixture-semantic.json")
    monkeypatch.setattr(settings, "detector_enabled", True)

    async def fake_probe_duration(_path):
        return 90.0

    async def fake_motion_profile(_path, _fps):
        return [MotionSample(t=float(t), changed=1, fg=0, mad=0) for t in range(90)]

    class FakeSiglip:
        model_id = "siglip-fixture"

        def score_video(self, _profile, _path):
            return []

    async def fake_scan(_path, start, _end, _samples):
        return WindowPerception(
            counts={"person": 1} if start >= 30 else {},
            stationary_persons=0,
            samples=2,
            rescue_persons=1 if start == 30 else 0,
        )

    async def fake_interpret(_path, window, keyframes, **kwargs):
        timestamp = keyframes[0]
        kwargs["timing"].update({"calls": 1, "total_ms": 7.5})
        kwargs["captured_frames"]["f_000"] = (
            FrameReference(frame_id="f_000", timestamp=timestamp),
            b"jpeg",
        )
        return _report(window)

    statuses = iter([RuntimeValidationStatus.VALIDATED, RuntimeValidationStatus.HUMAN_REVIEW])

    def fake_postprocess(**kwargs):
        return _validation(kwargs["report"], next(statuses), window_index=kwargs["window_index"])

    monkeypatch.setattr(runner.ingest, "probe_duration", fake_probe_duration)
    monkeypatch.setattr(runner.ingest, "motion_profile", fake_motion_profile)
    monkeypatch.setattr(candidate_model, "load_candidate_scorer", lambda _path: FakeSiglip())
    monkeypatch.setattr(
        runner,
        "build_candidate_intervals",
        lambda *_args, **_kwargs: [SimpleNamespace(start_time=60.0, end_time=90.0)],
    )
    monkeypatch.setattr(runner.perception, "scan_window", fake_scan)
    monkeypatch.setattr(runner, "interpret_window", fake_interpret)
    monkeypatch.setattr(runner, "postprocess_finalized_report", fake_postprocess)

    manager = FakeManager()
    await runner.run_video(manager, "clip.mp4", "metrics-main")

    rows = _metrics_rows(settings.runs_dir, "metrics-main")
    assert len(rows) == 1
    metrics = rows[0]
    assert metrics["terminal_status"] == "completed"
    assert metrics["windows_seen"] == metrics["windows_screened"] == 3
    assert metrics["windows_skipped_before_vlm"] == 1
    assert metrics["siglip_calls"] == 1
    assert metrics["dfine_calls"] == 3
    assert metrics["dfine_rescue_count"] == 1
    assert metrics["keyframes_selected_total"] == 2
    assert metrics["qwen_calls"] == 2
    assert metrics["qwen_total_ms"] == 15.0
    assert metrics["second_pass_calls"] == 0
    assert metrics["evidence_validation_count"] == 2
    assert metrics["evidence_valid_count"] == 1
    assert metrics["evidence_human_review_count"] == 1
    assert metrics["incidents_created"] == 1
    assert metrics["incidents_updated"] == 2
    assert all(getattr(payload, "type", "") != "run_metrics" for payload in manager.payloads)


@pytest.mark.asyncio
async def test_second_pass_and_qwen_are_counted_without_duplicate_inference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_runner(monkeypatch, tmp_path, incident_review=True)
    monkeypatch.setattr(settings, "candidate_screening", False)
    monkeypatch.setattr(settings, "detector_enabled", False)
    # Bu test yalnız 2. geçiş sayacını yalıtır; sınıf hakemi ayrı ölçülür.
    monkeypatch.setattr(settings, "adjudicate_confusable", "")

    async def fake_probe_duration(_path):
        return 30.0

    async def fake_motion_profile(_path, _fps):
        return [MotionSample(t=0, changed=1, fg=0, mad=0)]

    async def fake_interpret(_path, window, keyframes, **kwargs):
        timestamp = keyframes[0]
        kwargs["timing"].update({"calls": 1, "total_ms": 4.0})
        kwargs["captured_frames"]["f_000"] = (
            FrameReference(frame_id="f_000", timestamp=timestamp),
            b"first-pass",
        )
        return _report(window)

    async def fake_review(_path, _span, keyframes, _notes, **kwargs):
        timestamp = keyframes[0]
        kwargs["timing"].update({"calls": 1, "total_ms": 6.0})
        kwargs["captured_frames"]["f_000"] = (
            FrameReference(frame_id="f_000", timestamp=timestamp),
            b"second-pass",
        )
        return {
            "event_type": "possible_theft",
            "baslangic": "Kişi rafa yaklaştı.",
            "zirve": "Kişi raftaki nesneyi aldı.",
            "zirve_t": timestamp,
            "sonuc": "Kişi görüntüden çıktı.",
            "evidence": [
                {
                    "frame_id": "f_000",
                    "timestamp": timestamp,
                    "claim": "Kişinin raftaki nesneyi aldığı görülüyor.",
                }
            ],
            "risk": "yuksek",
            "belirsizlikler": [],
        }

    def fake_postprocess(**kwargs):
        return _validation(
            kwargs["report"],
            RuntimeValidationStatus.VALIDATED,
            window_index=kwargs["window_index"],
        )

    monkeypatch.setattr(runner.ingest, "probe_duration", fake_probe_duration)
    monkeypatch.setattr(runner.ingest, "motion_profile", fake_motion_profile)
    monkeypatch.setattr(runner, "interpret_window", fake_interpret)
    monkeypatch.setattr(runner.interpret, "review_incident", fake_review)
    monkeypatch.setattr(runner, "postprocess_finalized_report", fake_postprocess)

    await runner.run_video(FakeManager(), "clip.mp4", "metrics-second-pass")

    metrics = _metrics_rows(settings.runs_dir, "metrics-second-pass")[0]
    assert metrics["qwen_calls"] == 2
    assert metrics["qwen_total_ms"] == 10.0
    assert metrics["second_pass_calls"] == 1
    assert metrics["second_pass_total_ms"] >= 0
    assert metrics["evidence_validation_count"] == 2
    assert metrics["keyframes_selected_total"] == 2


def test_all_evidence_statuses_have_separate_counters() -> None:
    metrics = CanonicalRunMetrics("status-counts")
    report = _report((0.0, 30.0))
    validation = _validation(report, RuntimeValidationStatus.VALIDATED)
    validation = validation.model_copy(
        update={
            "events": [
                _validation(report, status, window_index=index).events[0]
                for index, status in enumerate(RuntimeValidationStatus)
            ]
        }
    )

    metrics.record_validation(validation)

    assert metrics.evidence_validation_count == 4
    assert metrics.evidence_valid_count == 1
    assert metrics.evidence_invalid_count == 1
    assert metrics.evidence_human_review_count == 1
    assert metrics.evidence_undetermined_count == 1


@pytest.mark.asyncio
async def test_failed_run_writes_one_partial_metrics_summary(tmp_path: Path, monkeypatch) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr(settings, "media_dir", media_dir)
    monkeypatch.setattr(settings, "runs_dir", tmp_path / "runs")

    await runner.run_video(FakeManager(), "missing.mp4", "metrics-failed")

    rows = _metrics_rows(settings.runs_dir, "metrics-failed")
    assert len(rows) == 1
    assert rows[0]["terminal_status"] == "failed"
    assert rows[0]["qwen_calls"] == 0


@pytest.mark.asyncio
async def test_cancelled_run_preserves_cancellation_and_metrics(
    tmp_path: Path, monkeypatch
) -> None:
    _configure_runner(monkeypatch, tmp_path, incident_review=False)
    started = asyncio.Event()

    async def blocking_probe(_path):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(runner.ingest, "probe_duration", blocking_probe)
    task = asyncio.create_task(runner.run_video(FakeManager(), "clip.mp4", "metrics-cancel"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    rows = _metrics_rows(settings.runs_dir, "metrics-cancel")
    assert len(rows) == 1
    assert rows[0]["terminal_status"] == "cancelled"


@pytest.mark.asyncio
async def test_fatal_run_is_interrupted_without_swallowing(tmp_path: Path, monkeypatch) -> None:
    _configure_runner(monkeypatch, tmp_path, incident_review=False)

    class FatalTaskExit(BaseException):
        pass

    async def fatal_probe(_path):
        raise FatalTaskExit

    monkeypatch.setattr(runner.ingest, "probe_duration", fatal_probe)

    with pytest.raises(FatalTaskExit):
        await runner.run_video(FakeManager(), "clip.mp4", "metrics-interrupted")

    rows = _metrics_rows(settings.runs_dir, "metrics-interrupted")
    assert len(rows) == 1
    assert rows[0]["terminal_status"] == "interrupted"


@pytest.mark.asyncio
async def test_run_metrics_summary_is_not_replayed_to_ws(tmp_path: Path) -> None:
    path = tmp_path / "metrics-only.jsonl"
    path.write_text(
        json.dumps(
            {
                "seq": 0,
                "ts": 1.0,
                "feed": "",
                "payload": {"type": "run_metrics", "run_id": "metrics-replay"},
            }
        ),
        encoding="utf-8",
    )
    manager = FakeManager()

    await replay_jsonl(manager, path, speed=1000.0)

    assert manager.payloads == []


@pytest.mark.asyncio
async def test_interpret_records_only_the_existing_qwen_call(monkeypatch) -> None:
    async def fake_frame_parts(*_args, **_kwargs):
        return []

    async def fake_create_chat(*_args, **_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"summary":"Sahne sakin.","durum":"olagan"}'),
                    finish_reason="stop",
                )
            ]
        )

    ticks = iter([10.0, 10.025])
    monkeypatch.setattr(runner.interpret, "_frame_parts", fake_frame_parts)
    monkeypatch.setattr(runner.interpret, "main_client", lambda: object())
    monkeypatch.setattr(runner.interpret, "create_chat", fake_create_chat)
    monkeypatch.setattr(
        runner.interpret,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )
    timing: dict[str, float | int] = {}

    await runner.interpret.interpret_window(
        Path("unused.mp4"),
        (0.0, 30.0),
        [],
        timing=timing,
    )

    assert timing == {"calls": 1, "total_ms": pytest.approx(25.0)}


@pytest.mark.asyncio
async def test_interpret_preserves_qwen_timing_when_call_fails(monkeypatch) -> None:
    async def fake_frame_parts(*_args, **_kwargs):
        return []

    async def failing_create_chat(*_args, **_kwargs):
        raise RuntimeError("fixture failure")

    ticks = iter([20.0, 20.004])
    monkeypatch.setattr(runner.interpret, "_frame_parts", fake_frame_parts)
    monkeypatch.setattr(runner.interpret, "main_client", lambda: object())
    monkeypatch.setattr(runner.interpret, "create_chat", failing_create_chat)
    monkeypatch.setattr(
        runner.interpret,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )
    timing: dict[str, float | int] = {}

    with pytest.raises(RuntimeError, match="fixture failure"):
        await runner.interpret.interpret_window(
            Path("unused.mp4"),
            (0.0, 30.0),
            [],
            timing=timing,
        )

    assert timing == {"calls": 1, "total_ms": pytest.approx(4.0)}
