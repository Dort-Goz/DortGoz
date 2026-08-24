

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dortgoz.config import settings
from dortgoz.domain.dataset import (
    DatasetLicenseStatus,
    DatasetSplit,
    DatasetUse,
    DatasetVideoRecord,
    OfflineDatasetManifest,
    calculate_dataset_fingerprint,
)
from dortgoz.domain.model_lifecycle import (
    DfineArchitecture,
    DfineDeploymentArtifact,
    ModelVersion,
)
from dortgoz.pipeline import perception
from dortgoz.pipeline.runner import PipelineStopRequested
from dortgoz.services.dataset_manifest import sha256_file
from dortgoz.services.evaluation_report import EvaluationReportError
from dortgoz.services.execution_coordinator import ExecutionCoordinator
from dortgoz.services.shadow_evaluation import (
    ShadowCase,
    ShadowCaseManifest,
    execute_shadow_evaluation,
    prepare_shadow_evaluation,
)

CODE_REVISION = "f" * 40


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    media = workspace / "media"
    media.mkdir(parents=True)
    critical = media / "critical.mp4"
    normal = media / "normal.mp4"
    critical.write_bytes(b"critical-video")
    normal.write_bytes(b"normal-video")
    entries = [
        DatasetVideoRecord(
            dataset_video_id="test/critical",
            source_ref="critical.mp4",
            source_label="critical",
            split=DatasetSplit.TEST,
            file_size_bytes=critical.stat().st_size,
            file_sha256=sha256_file(critical),
            allowed_uses=[DatasetUse.BENCHMARK],
        ),
        DatasetVideoRecord(
            dataset_video_id="test/normal",
            source_ref="normal.mp4",
            source_label="normal",
            split=DatasetSplit.UNASSIGNED,
            file_size_bytes=normal.stat().st_size,
            file_sha256=sha256_file(normal),
            allowed_uses=[DatasetUse.BENCHMARK],
        ),
    ]
    dataset = OfflineDatasetManifest(
        dataset_id="shadow-fixture",
        source_name="Shadow fixture",
        source_url="https://example.invalid/shadow",
        citation="Fixture.",
        license_status=DatasetLicenseStatus.UNVERIFIED,
        license_id=None,
        redistribution_allowed=False,
        training_allowed=False,
        allowed_uses=[DatasetUse.BENCHMARK],
        entries=entries,
        dataset_fingerprint=calculate_dataset_fingerprint(entries),
    )
    checkpoint = workspace / "runs" / "job" / "best.pth"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"checkpoint")
    onnx = workspace / "models" / "candidate" / "model.onnx"
    onnx.parent.mkdir(parents=True)
    onnx.write_bytes(b"onnx")
    exported_at = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    artifact_payload = {
        "artifact_version": "1.0.0",
        "onnx_ref": "models/candidate/model.onnx",
        "onnx_sha256": sha256_file(onnx),
        "source_checkpoint_sha256": sha256_file(checkpoint),
        "dfine_repository_revision": "a" * 40,
        "input_names": ["images", "orig_target_sizes"],
        "output_names": ["labels", "boxes", "scores"],
        "input_size": 640,
        "category_names": ["person"],
        "source_log_sha256": "b" * 64,
        "exported_at": exported_at.isoformat().replace("+00:00", "Z"),
    }
    deployment = DfineDeploymentArtifact(
        **artifact_payload,
        artifact_fingerprint=_fingerprint(artifact_payload),
    )
    candidate = ModelVersion(
        model_version_id="candidate-shadow",
        training_job_id="job",
        architecture=DfineArchitecture.SMALL,
        checkpoint_ref="runs/job/best.pth",
        checkpoint_sha256=sha256_file(checkpoint),
        dataset_fingerprint="c" * 64,
        export_fingerprint="d" * 64,
        dfine_repository_revision="a" * 40,
        deployment=deployment,
    )
    (onnx.parent / "config.json").write_text(
        json.dumps(
            {
                "id2label": {"0": "person"},
                "interest_labels": ["person"],
                "onnx_sha256": deployment.onnx_sha256,
                "deployment_fingerprint": deployment.artifact_fingerprint,
            }
        ),
        encoding="utf-8",
    )
    cases = ShadowCaseManifest(
        test_dataset_fingerprint=dataset.dataset_fingerprint,
        cases=[
            ShadowCase(
                case_id="critical",
                video_ref="critical.mp4",
                video_sha256=sha256_file(critical),
                expected_critical=True,
                is_normal=False,
            ),
            ShadowCase(
                case_id="normal",
                video_ref="normal.mp4",
                video_sha256=sha256_file(normal),
                expected_critical=False,
                is_normal=True,
            ),
        ],
        labelled_by="operator",
        labelled_at=exported_at,
    )
    case_path = workspace / "shadow-cases.json"
    case_path.write_text(cases.model_dump_json(indent=2), encoding="utf-8")
    plan = prepare_shadow_evaluation(
        candidate=candidate,
        test_dataset_manifest=dataset,
        case_manifest=cases,
        case_manifest_path=case_path,
        workspace_root=workspace,
        media_root=media,
        code_revision=CODE_REVISION,
        created_by="operator",
        now=exported_at,
    )
    return workspace, candidate, plan


async def test_shadow_worker_writes_three_distinct_complete_artifacts(
    tmp_path: Path,
) -> None:
    workspace, candidate, plan = _fixture(tmp_path)

    async def fake_runner(_manager, video_ref: str, run_id: str, **_kwargs) -> None:
        payloads = []
        if video_ref == "critical.mp4":
            payloads.append(
                {
                    "type": "incident_update",
                    "incident_id": "incident-1",
                    "phase": "sonuclandi",
                    "risk": "kritik",
                }
            )
        payloads.extend(
            [
                {"type": "run_status", "state": "done"},
                {
                    "type": "run_metrics",
                    "terminal_status": "completed",
                    "total_runtime_ms": 125.0,
                    "dfine_calls": 4,
                    "dfine_total_ms": 80.0,
                },
            ]
        )
        path = workspace / "runs" / f"{run_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                json.dumps({"seq": 0, "ts": 1, "feed": "", "payload": payload}) + "\n"
                for payload in payloads
            ),
            encoding="utf-8",
        )

    async def duration_probe(_path: Path) -> float:
        return 60.0

    outputs = await execute_shadow_evaluation(
        plan=plan,
        candidate=candidate,
        workspace_root=workspace,
        runs_root=workspace / "runs",
        run_video_callable=fake_runner,
        duration_probe=duration_probe,
        peak_ram_probe=lambda: 256.0,
        revision_probe=lambda _root: CODE_REVISION,
    )

    assert len(outputs) == 3 and len(set(outputs)) == 3
    run_ids = set()
    for output in outputs:
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert {row["case_id"] for row in rows} == {"critical", "normal"}
        critical = next(row for row in rows if row["case_id"] == "critical")
        normal = next(row for row in rows if row["case_id"] == "normal")
        assert critical["confirmed_critical"] is True
        assert normal["false_alarm"] is False
        assert critical["onnx_sha256"] == candidate.deployment.onnx_sha256
        assert critical["ram_mb"] == 256.0 and critical["vram_mb"] == 0
        run_ids.add(critical["evaluation_run_id"])
    assert len(run_ids) == 3


async def test_live_waits_for_cooperative_shadow_teardown_and_cache_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, candidate, plan = _fixture(tmp_path)
    coordinator = ExecutionCoordinator(workspace / "event.sqlite3", poll_seconds=0.005)
    started = asyncio.Event()
    original_settings = (
        settings.media_dir,
        settings.runs_dir,
        settings.dfine_onnx,
        settings.detector_enabled,
    )
    reset_calls = 0
    original_reset = perception.reset_detector_cache

    def reset_spy() -> None:
        nonlocal reset_calls
        reset_calls += 1
        original_reset()

    monkeypatch.setattr(perception, "reset_detector_cache", reset_spy)

    async def cooperative_runner(
        _manager, _video_ref: str, _run_id: str, *, stop_probe
    ) -> None:
        started.set()

        def wait_for_stop() -> None:
            while not stop_probe():
                time.sleep(0.005)

        await asyncio.to_thread(wait_for_stop)
        raise PipelineStopRequested("fixture preemption")

    async def duration_probe(_path: Path) -> float:
        return 60.0

    shadow_task = asyncio.create_task(
        execute_shadow_evaluation(
            plan=plan,
            candidate=candidate,
            workspace_root=workspace,
            runs_root=workspace / "runs",
            execution_coordinator=coordinator,
            run_video_callable=cooperative_runner,
            duration_probe=duration_probe,
            revision_probe=lambda _root: CODE_REVISION,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    live = await asyncio.wait_for(coordinator.acquire_live(timeout_seconds=1.0), timeout=2.0)
    with pytest.raises(EvaluationReportError) as raised:
        await shadow_task

    assert raised.value.code == "LIVE_ANALYSIS_PREEMPTED"
    assert (
        settings.media_dir,
        settings.runs_dir,
        settings.dfine_onnx,
        settings.detector_enabled,
    ) == original_settings
    assert perception._detector_override is None
    assert reset_calls >= 3
    await live.release_async()
