"""Hash-bound detector and shadow evaluation report tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dortgoz.domain.dataset import (
    DatasetLicenseStatus,
    DatasetSplit,
    DatasetUse,
    DatasetVideoRecord,
    OfflineDatasetManifest,
    calculate_dataset_fingerprint,
)
from dortgoz.domain.model_lifecycle import DfineArchitecture, ModelVersion
from dortgoz.services.dataset_manifest import sha256_file
from dortgoz.services.evaluation_report import (
    EvaluationReportError,
    build_dfine_evaluation_report,
)

CHECKPOINT_SHA = "a" * 64
CODE_REVISION = "b" * 40


def _manifest() -> OfflineDatasetManifest:
    entries = [
        DatasetVideoRecord(
            dataset_video_id="benchmark/test",
            source_ref="test/video.mp4",
            source_label="critical",
            split=DatasetSplit.TEST,
            file_size_bytes=10,
            file_sha256="c" * 64,
            allowed_uses=[DatasetUse.BENCHMARK],
        )
    ]
    return OfflineDatasetManifest(
        dataset_id="benchmark-fixture",
        source_name="Benchmark fixture",
        source_url="https://example.invalid/benchmark",
        citation="Test fixture.",
        license_status=DatasetLicenseStatus.UNVERIFIED,
        license_id=None,
        redistribution_allowed=False,
        training_allowed=False,
        allowed_uses=[DatasetUse.BENCHMARK],
        entries=entries,
        dataset_fingerprint=calculate_dataset_fingerprint(entries),
    )


def _candidate() -> ModelVersion:
    return ModelVersion(
        model_version_id="candidate-1",
        training_job_id="job-1",
        architecture=DfineArchitecture.SMALL,
        checkpoint_ref="runs/job-1/best.pth",
        checkpoint_sha256=CHECKPOINT_SHA,
        dataset_fingerprint="d" * 64,
        export_fingerprint="e" * 64,
        dfine_repository_revision="f" * 40,
    )


def _detector_report(path: Path, manifest: OfflineDatasetManifest, **updates) -> Path:
    payload = {
        "report_version": "1.0.0",
        "candidate_checkpoint_sha256": CHECKPOINT_SHA,
        "test_dataset_fingerprint": manifest.dataset_fingerprint,
        "code_revision": CODE_REVISION,
        "map_50_95": 0.72,
        "map_50": 0.88,
        "measured_at": "2026-08-16T10:00:00Z",
        **updates,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _shadow_artifact(
    path: Path,
    manifest: OfflineDatasetManifest,
    *,
    run_number: int,
    critical_hit: bool,
    false_alarm: bool,
) -> Path:
    measured_at = datetime(2026, 8, 16, 10, 0, tzinfo=UTC) + timedelta(
        minutes=run_number
    )
    common = {
        "evaluation_run_id": f"shadow-run-{run_number}",
        "candidate_checkpoint_sha256": CHECKPOINT_SHA,
        "test_dataset_fingerprint": manifest.dataset_fingerprint,
        "code_revision": CODE_REVISION,
        "shadow_mode": True,
        "measured_at": measured_at.isoformat(),
    }
    rows = [
        {
            **common,
            "expected_critical": True,
            "confirmed_critical": critical_hit,
            "latency_ms": 100 * run_number,
            "ram_mb": 500 + run_number,
            "vram_mb": 1000 + run_number,
        },
        {
            **common,
            "is_normal": True,
            "duration_seconds": 1800,
            "false_alarm": false_alarm,
            "latency_ms": 100 * run_number + 20,
            "ram_mb": 700 + run_number,
            "vram_mb": 1200 + run_number,
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def test_report_combines_three_distinct_shadow_runs_and_detector_metrics(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    detector = _detector_report(tmp_path / "detector.json", manifest)
    artifacts = [
        _shadow_artifact(
            tmp_path / f"shadow-{index}.jsonl",
            manifest,
            run_number=index,
            critical_hit=index != 3,
            false_alarm=index == 2,
        )
        for index in range(1, 4)
    ]
    output = tmp_path / "report.json"

    report = build_dfine_evaluation_report(
        candidate=_candidate(),
        test_dataset_manifest=manifest,
        detector_report_path=detector,
        e2e_artifact_paths=artifacts,
        evaluator="operator",
        output_path=output,
    )

    assert report.map_50_95 == 0.72 and report.map_50 == 0.88
    assert report.critical_recall == pytest.approx(2 / 3)
    assert report.false_alarms_per_hour == pytest.approx(2 / 3)
    assert report.p95_latency_ms == 320
    assert report.peak_memory_mb == 1203
    assert report.repetitions == 3 and report.shadow_passed is True
    assert report.detector_report_sha256 == sha256_file(detector)
    assert report.e2e_artifact_sha256s == [sha256_file(path) for path in artifacts]
    assert json.loads(output.read_text(encoding="utf-8"))["repetitions"] == 3


def test_report_rejects_duplicate_run_or_artifact(tmp_path: Path) -> None:
    manifest = _manifest()
    detector = _detector_report(tmp_path / "detector.json", manifest)
    artifact = _shadow_artifact(
        tmp_path / "shadow.jsonl",
        manifest,
        run_number=1,
        critical_hit=True,
        false_alarm=False,
    )

    with pytest.raises(EvaluationReportError) as rejected:
        build_dfine_evaluation_report(
            candidate=_candidate(),
            test_dataset_manifest=manifest,
            detector_report_path=detector,
            e2e_artifact_paths=[artifact, artifact, artifact],
            evaluator="operator",
        )
    assert rejected.value.code == "DUPLICATE_EVALUATION_ARTIFACT"


def test_report_rejects_wrong_checkpoint_and_incomplete_repetitions(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    detector = _detector_report(
        tmp_path / "detector.json",
        manifest,
        candidate_checkpoint_sha256="0" * 64,
    )
    artifacts = [
        _shadow_artifact(
            tmp_path / f"shadow-{index}.jsonl",
            manifest,
            run_number=index,
            critical_hit=True,
            false_alarm=False,
        )
        for index in range(1, 4)
    ]
    with pytest.raises(EvaluationReportError) as rejected:
        build_dfine_evaluation_report(
            candidate=_candidate(),
            test_dataset_manifest=manifest,
            detector_report_path=detector,
            e2e_artifact_paths=artifacts,
            evaluator="operator",
        )
    assert rejected.value.code == "DETECTOR_CHECKPOINT_MISMATCH"

    with pytest.raises(EvaluationReportError) as repetitions:
        build_dfine_evaluation_report(
            candidate=_candidate(),
            test_dataset_manifest=manifest,
            detector_report_path=detector,
            e2e_artifact_paths=artifacts[:2],
            evaluator="operator",
        )
    assert repetitions.value.code == "EVALUATION_REPETITIONS_MISSING"
