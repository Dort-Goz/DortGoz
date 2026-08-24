

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
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
from dortgoz.services.dfine_evaluation import (
    DfineDetectorEvaluationPlan,
    build_dfine_test_command,
    execute_dfine_detector_evaluation,
    load_dfine_evaluation_plan,
    normalize_dfine_evaluation_log,
    parse_dfine_coco_metrics,
    prepare_dfine_detector_evaluation,
    write_dfine_evaluation_plan,
)
from dortgoz.services.dfine_training import ProcessOutcome
from dortgoz.services.evaluation_report import EvaluationReportError
from dortgoz.services.execution_coordinator import ExecutionCoordinator

CODE_REVISION = "a" * 40


def _manifest() -> OfflineDatasetManifest:
    entries = [
        DatasetVideoRecord(
            dataset_video_id="fixture/test",
            source_ref="videos/test.mp4",
            source_label="critical",
            split=DatasetSplit.TEST,
            file_size_bytes=10,
            file_sha256="b" * 64,
            allowed_uses=[DatasetUse.BENCHMARK],
        )
    ]
    return OfflineDatasetManifest(
        dataset_id="evaluation-fixture",
        source_name="Evaluation fixture",
        source_url="https://example.invalid/evaluation",
        citation="Test fixture.",
        license_status=DatasetLicenseStatus.UNVERIFIED,
        license_id=None,
        redistribution_allowed=False,
        training_allowed=False,
        allowed_uses=[DatasetUse.BENCHMARK],
        entries=entries,
        dataset_fingerprint=calculate_dataset_fingerprint(entries),
    )


def _dfine_repository(root: Path) -> tuple[Path, str]:
    repository = root / "D-FINE"
    config = repository / "configs" / "dfine" / "custom"
    config.mkdir(parents=True)
    (repository / "LICENSE").write_text(
        "Apache License\nVersion 2.0, January 2004\n", encoding="utf-8"
    )
    (repository / "train.py").write_text("# fixture\n", encoding="utf-8")
    (config / "dfine_hgnetv2_s_custom.yml").write_text("epochs: 10\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repository,
        check=True,
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repository, revision


def _fixture(
    tmp_path: Path,
    *,
    expected_category_names: list[str] | None = None,
    source_video_sha256: str = "b" * 64,
):
    workspace = tmp_path / "workspace"
    frames = workspace / "media" / "evaluation"
    frames.mkdir(parents=True)
    (frames / "one.jpg").write_bytes(b"frame-one")
    coco = workspace / "evaluation" / "instances_test.json"
    coco.parent.mkdir()
    coco.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "id": 1,
                        "file_name": "one.jpg",
                        "width": 64,
                        "height": 48,
                        "source_video_sha256": source_video_sha256,
                        "source_timestamp_seconds": 4.5,
                    }
                ],
                "annotations": [
                    {
                        "id": 1,
                        "image_id": 1,
                        "category_id": 1,
                        "bbox": [1, 2, 10, 20],
                        "area": 200,
                        "iscrowd": 0,
                    }
                ],
                "categories": [{"id": 1, "name": "person"}],
                "info": {
                    "dataset_fingerprint": _manifest().dataset_fingerprint,
                    "split": "test",
                },
            }
        ),
        encoding="utf-8",
    )
    checkpoint = workspace / "runs" / "job-1" / "best.pth"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"candidate")
    dfine, dfine_revision = _dfine_repository(tmp_path)
    candidate = ModelVersion(
        model_version_id="candidate-1",
        training_job_id="job-1",
        architecture=DfineArchitecture.SMALL,
        checkpoint_ref="runs/job-1/best.pth",
        checkpoint_sha256=sha256_file(checkpoint),
        dataset_fingerprint="c" * 64,
        export_fingerprint="d" * 64,
        dfine_repository_revision=dfine_revision,
    )
    plan = prepare_dfine_detector_evaluation(
        candidate=candidate,
        test_dataset_manifest=_manifest(),
        workspace_root=workspace,
        dfine_repository=dfine,
        coco_annotations=coco,
        frame_root=frames,
        code_revision=CODE_REVISION,
        created_by="operator",
        expected_category_names=expected_category_names or ["person"],
        now=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )
    return workspace, frames, coco, dfine, plan


class _EvaluationRunner:
    def __init__(self) -> None:
        self.timeout_seconds = 0.0
        self.cuda_visible_devices = ""

    def run(self, argv: list[str], **kwargs) -> ProcessOutcome:
        assert "--test-only" in argv
        self.timeout_seconds = kwargs["timeout_seconds"]
        self.cuda_visible_devices = kwargs["env"]["CUDA_VISIBLE_DEVICES"]
        kwargs["log_path"].write_text(
            "Average Precision (AP) @[ IoU=0.50:0.95 | area=all | maxDets=100 ] = 0.700\n"
            "Average Precision (AP) @[ IoU=0.50 | area=all | maxDets=100 ] = 0.850\n",
            encoding="utf-8",
        )
        return ProcessOutcome(exit_code=0, elapsed_seconds=12.5)


def test_plan_freezes_checkpoint_coco_frames_and_three_shadow_ids(
    tmp_path: Path,
) -> None:
    workspace, _, _, dfine, plan = _fixture(tmp_path)
    python = tmp_path / "python.exe"
    python.write_bytes(b"fixture")

    command = build_dfine_test_command(
        plan=plan,
        workspace_root=workspace,
        dfine_repository=dfine,
        python_executable=python,
        output_dir=workspace / "runs" / "evaluation" / "detector",
    )

    assert plan.coco.image_count == 1
    assert plan.coco.annotation_count == 1
    assert plan.coco.category_names == ["person"]
    assert len(plan.shadow_run_ids) == 3
    assert len(set(plan.shadow_run_ids)) == 3
    assert "--test-only" in command
    assert command[command.index("-r") + 1].endswith("best.pth")
    assert "num_classes=1" in command
    assert "remap_mscoco_category=False" in command


def test_command_rejects_a_changed_test_frame(tmp_path: Path) -> None:
    workspace, frames, _, dfine, plan = _fixture(tmp_path)
    python = tmp_path / "python.exe"
    python.write_bytes(b"fixture")
    (frames / "one.jpg").write_bytes(b"changed")

    with pytest.raises(EvaluationReportError) as rejected:
        build_dfine_test_command(
            plan=plan,
            workspace_root=workspace,
            dfine_repository=dfine,
            python_executable=python,
            output_dir=workspace / "runs" / "evaluation" / "detector",
        )
    assert rejected.value.code == "COCO_EVALUATION_CHANGED"


def test_plan_rejects_wrong_candidate_categories_and_source_video(tmp_path: Path) -> None:
    with pytest.raises(EvaluationReportError) as category_rejected:
        _fixture(tmp_path / "category", expected_category_names=["weapon"])
    assert category_rejected.value.code == "COCO_CATEGORY_MISMATCH"

    with pytest.raises(EvaluationReportError) as source_rejected:
        _fixture(tmp_path / "source", source_video_sha256="f" * 64)
    assert source_rejected.value.code == "COCO_SOURCE_VIDEO_MISMATCH"


def test_plan_file_rejects_content_tampering(tmp_path: Path) -> None:
    _, _, _, _, plan = _fixture(tmp_path)
    path = write_dfine_evaluation_plan(tmp_path / "plan.json", plan)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["created_by"] = "changed"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationReportError) as rejected:
        load_dfine_evaluation_plan(path)
    assert rejected.value.code == "EVALUATION_PLAN_INVALID"


def test_parser_reads_official_pycocotools_summary_and_ignores_ap75() -> None:
    text = """
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.391
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.612
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.420
"""
    assert parse_dfine_coco_metrics(text) == (0.391, 0.612)


def test_parser_prefers_structured_dfine_metrics_and_rejects_ambiguity() -> None:
    structured = (
        '{"test_coco_eval_bbox": [0.391234, 0.612345]}\n'
        "Average Precision (AP) @[ IoU=0.50:0.95 | area=all | maxDets=100 ] = 0.391\n"
        "Average Precision (AP) @[ IoU=0.50 | area=all | maxDets=100 ] = 0.612\n"
    )
    assert parse_dfine_coco_metrics(structured) == (0.391234, 0.612345)

    with pytest.raises(EvaluationReportError) as rejected:
        parse_dfine_coco_metrics(
            '{"test_coco_eval_bbox": [0.30, 0.50]}\n{"test_coco_eval_bbox": [0.40, 0.60]}\n'
        )
    assert rejected.value.code == "DFINE_METRICS_AMBIGUOUS"


def test_normalizer_binds_metrics_to_plan_and_source_log(tmp_path: Path) -> None:
    _, _, _, _, plan = _fixture(tmp_path)
    log = tmp_path / "evaluation.log"
    log.write_text(
        "Average Precision (AP) @[ IoU=0.50:0.95 | area=all | maxDets=100 ] = 0.700\n"
        "Average Precision (AP) @[ IoU=0.50 | area=all | maxDets=100 ] = 0.850\n",
        encoding="utf-8",
    )
    output = tmp_path / "detector.json"

    artifact = normalize_dfine_evaluation_log(
        plan=plan,
        log_path=log,
        output_path=output,
        measured_at=datetime(2026, 8, 16, 13, 0, tzinfo=UTC),
    )

    assert artifact.map_50_95 == 0.7
    assert artifact.map_50 == 0.85
    assert artifact.evaluation_plan_fingerprint == plan.plan_fingerprint
    assert artifact.source_log_sha256 == sha256_file(log)
    assert json.loads(output.read_text(encoding="utf-8"))["source_log_sha256"] == sha256_file(log)


def test_worker_uses_gpu_budget_and_emits_normalized_report(tmp_path: Path) -> None:
    workspace, _, _, dfine, plan = _fixture(tmp_path)
    python = tmp_path / "python.exe"
    python.write_bytes(b"fixture")
    (workspace / ".gitignore").write_text("runs/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=workspace,
        check=True,
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    normalized = plan.model_dump(mode="json", exclude={"plan_fingerprint"})
    normalized["code_revision"] = revision
    fingerprint = hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    normalized_plan = DfineDetectorEvaluationPlan.model_validate(
        {**normalized, "plan_fingerprint": fingerprint}
    )
    runner = _EvaluationRunner()

    artifact, outcome, report_path = execute_dfine_detector_evaluation(
        plan=normalized_plan,
        workspace_root=workspace,
        dfine_repository=dfine,
        python_executable=python,
        runs_root=workspace / "runs",
        gpu_index=1,
        max_gpu_minutes=5,
        process_runner=runner,
    )

    assert outcome.elapsed_seconds == 12.5
    assert runner.timeout_seconds == 300
    assert runner.cuda_visible_devices == "1"
    assert artifact.map_50_95 == 0.7
    assert report_path.is_file()


async def test_detector_evaluation_worker_obeys_live_lease(tmp_path: Path) -> None:
    workspace, _, _, dfine, plan = _fixture(tmp_path)
    coordinator = ExecutionCoordinator(tmp_path / "event.sqlite3")
    live = await coordinator.acquire_live()

    with pytest.raises(EvaluationReportError) as raised:
        execute_dfine_detector_evaluation(
            plan=plan,
            workspace_root=workspace,
            dfine_repository=dfine,
            python_executable=tmp_path / "python",
            runs_root=workspace / "runs",
            execution_coordinator=coordinator,
        )

    assert raised.value.code == "LIVE_ANALYSIS_ACTIVE"
    await live.release_async()
