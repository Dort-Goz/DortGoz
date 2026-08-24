

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
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
from dortgoz.domain.model_lifecycle import (
    DfineArchitecture,
    DfineDeploymentArtifact,
    DfineTrainingPolicy,
    ModelStage,
    ModelVersion,
    PromotionPolicy,
    TrainingJob,
    TrainingJobStatus,
)
from dortgoz.domain.training import (
    FrameReviewResult,
    TrainingFrameReview,
    TrainingSample,
    TrainingSampleStatus,
    VerifiedBoundingBox,
)
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.repositories.sqlite import SqliteEventRepository
from dortgoz.services.dataset_manifest import sha256_file, write_dataset_manifest
from dortgoz.services.dfine_training import (
    DfineTrainingError,
    DfineTrainingService,
    ProcessOutcome,
)
from dortgoz.services.model_registry import ModelRegistryError, ModelRegistryService
from dortgoz.services.training_selection import TrainingSelectionPolicy


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _manifest() -> OfflineDatasetManifest:
    entries = [
        DatasetVideoRecord(
            dataset_video_id="fixture/train",
            source_ref="videos/train.mp4",
            source_label="fixture",
            split=DatasetSplit.TRAIN,
            file_size_bytes=5,
            file_sha256=_sha(b"train"),
            allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        ),
        DatasetVideoRecord(
            dataset_video_id="fixture/validation",
            source_ref="videos/validation.mp4",
            source_label="fixture",
            split=DatasetSplit.VALIDATION,
            file_size_bytes=10,
            file_sha256=_sha(b"validation"),
            allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        ),
    ]
    return OfflineDatasetManifest(
        dataset_id="approved-fixture",
        source_name="Approved fixture",
        source_url="https://example.invalid/fixture",
        citation="Local fixture.",
        license_status=DatasetLicenseStatus.VERIFIED,
        license_id="Apache-2.0",
        redistribution_allowed=True,
        training_allowed=True,
        allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        entries=entries,
        dataset_fingerprint=calculate_dataset_fingerprint(entries),
    )


def _review(
    manifest: OfflineDatasetManifest,
    *,
    split: DatasetSplit,
    frame_ref: str,
    frame: bytes,
) -> TrainingFrameReview:
    train = split == DatasetSplit.TRAIN
    return TrainingFrameReview(
        annotation_id=f"annotation-{split.value}",
        dataset_id=manifest.dataset_id,
        dataset_fingerprint=manifest.dataset_fingerprint,
        dataset_video_id="fixture/train" if train else "fixture/validation",
        source_video_ref="videos/train.mp4" if train else "videos/validation.mp4",
        frame_ref=frame_ref,
        frame_sha256=_sha(frame),
        frame_size_bytes=len(frame),
        timestamp_seconds=1 if train else 2,
        image_width=64,
        image_height=48,
        split=split,
        review_result=(
            FrameReviewResult.VERIFIED_BOXES
            if train
            else FrameReviewResult.VERIFIED_NO_TARGET_OBJECTS
        ),
        boxes=(
            [VerifiedBoundingBox(category_name="person", x=1, y=2, width=10, height=20)]
            if train
            else []
        ),
        human_verified=True,
        reviewer="operator",
        annotation_tool="CVAT Community",
        reviewed_at=datetime(2026, 8, 16, 10, 0, tzinfo=UTC),
    )


class _SampleRepository(InMemoryEventRepository):
    def __init__(self, reviews: list[TrainingFrameReview]) -> None:
        super().__init__()
        self._fixture_samples = [
            TrainingSample(
                sample_id=review.annotation_id,
                event_id=f"event-{review.split.value}",
                event_revision=2,
                review_id=f"review-{review.split.value}",
                approval_id=f"approval-{review.split.value}",
                video_id=f"video-{review.split.value}",
                source_video_sha256=(
                    _sha(b"train") if review.split == DatasetSplit.TRAIN else _sha(b"validation")
                ),
                status=TrainingSampleStatus.VERIFIED,
                dataset_id=review.dataset_id,
                dataset_fingerprint=review.dataset_fingerprint,
                dataset_video_id=review.dataset_video_id,
                source_video_ref=review.source_video_ref,
                split=review.split,
                timestamp_seconds=review.timestamp_seconds,
                selection_reason="event_peak",
                frame_ref=review.frame_ref,
                frame_sha256=review.frame_sha256,
                frame_size_bytes=review.frame_size_bytes,
                image_width=review.image_width,
                image_height=review.image_height,
                prepared_by="operator",
                frame_review=review,
                created_at=review.reviewed_at,
                updated_at=review.reviewed_at,
                revision=2,
            )
            for review in reviews
        ]

    def list_training_samples(self, event_id: str | None = None) -> list:
        assert event_id is None
        return list(self._fixture_samples)


class _SuccessfulRunner:
    def __init__(self) -> None:
        self.argv: list[str] = []
        self.timeout_seconds = 0.0

    def run(self, argv: list[str], **kwargs) -> ProcessOutcome:
        self.argv = argv
        self.timeout_seconds = kwargs["timeout_seconds"]
        output_dir = Path(argv[argv.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "best_stg2.pth").write_bytes(b"trained-checkpoint")
        return ProcessOutcome(exit_code=0, elapsed_seconds=12.5)


def _fake_dfine_repository(root: Path) -> Path:
    repository = root / "D-FINE"
    config = repository / "configs" / "dfine" / "custom"
    config.mkdir(parents=True)
    (repository / "LICENSE").write_text(
        "Apache License\nVersion 2.0, January 2004\n", encoding="utf-8"
    )
    (repository / "train.py").write_text("# fixture\n", encoding="utf-8")
    (config / "dfine_hgnetv2_s_custom.yml").write_text("epoches: 220\n", encoding="utf-8")
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
    return repository


def test_training_plan_and_worker_create_only_a_candidate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    frame_root = workspace / "media"
    runs_root = workspace / "runs"
    frame_root.mkdir(parents=True)
    train_frame = b"train-frame"
    validation_frame = b"validation-frame"
    (frame_root / "train").mkdir()
    (frame_root / "validation").mkdir()
    (frame_root / "train" / "a.jpg").write_bytes(train_frame)
    (frame_root / "validation" / "b.jpg").write_bytes(validation_frame)
    manifest = _manifest()
    manifest_path = write_dataset_manifest(workspace / "manifest.json", manifest)
    reviews = [
        _review(
            manifest,
            split=DatasetSplit.TRAIN,
            frame_ref="train/a.jpg",
            frame=train_frame,
        ),
        _review(
            manifest,
            split=DatasetSplit.VALIDATION,
            frame_ref="validation/b.jpg",
            frame=validation_frame,
        ),
    ]
    repository = _SampleRepository(reviews)
    runner = _SuccessfulRunner()
    dfine_repository = _fake_dfine_repository(tmp_path)
    base_checkpoint = tmp_path / "dfine-s.pth"
    base_checkpoint.write_bytes(b"base-checkpoint")
    policy = DfineTrainingPolicy(
        policy_version="test-v1",
        minimum_verified_frames=2,
        minimum_train_frames=1,
        minimum_validation_frames=1,
        minimum_source_videos=2,
        maximum_epochs=2,
        maximum_batch_size=2,
        maximum_gpu_minutes_per_job=5,
        maximum_gpu_minutes_per_day=5,
    )
    service = DfineTrainingService(
        repository,
        workspace_root=workspace,
        frame_root=frame_root,
        runs_root=runs_root,
        policy=policy,
        selection_policy=TrainingSelectionPolicy(
            minimum_train_samples=1,
            maximum_train_samples=2,
            minimum_validation_samples=1,
            maximum_validation_samples=2,
            minimum_train_source_videos=1,
            maximum_samples_per_source_video=2,
            maximum_samples_per_event=1,
            maximum_negative_fraction=0.5,
        ),
        process_runner=runner,
        cuda_probe=lambda _python, _gpu: None,
    )

    job = service.plan(
        dataset_manifest_path=manifest_path,
        dfine_repository=dfine_repository,
        base_checkpoint=base_checkpoint,
        architecture=DfineArchitecture.SMALL,
        requested_by="operator",
        epochs=2,
        batch_size=2,
        max_gpu_minutes=5,
    )
    finished, candidate = service.execute(
        job.job_id,
        dfine_repository=dfine_repository,
        base_checkpoint=base_checkpoint,
        python_executable=Path(sys.executable),
    )

    assert job.status == TrainingJobStatus.QUEUED
    assert job.selection_policy_version == "dfine-selection-v1"
    assert job.selection_policy_fingerprint is not None
    assert job.selection_fingerprint is not None
    selection_report = workspace / job.export_ref / "selection_report.json"
    assert (
        json.loads(selection_report.read_text(encoding="utf-8"))["selection_fingerprint"]
        == job.selection_fingerprint
    )
    assert finished.status == TrainingJobStatus.SUCCEEDED
    assert finished.elapsed_seconds == 12.5
    assert candidate.stage == ModelStage.CANDIDATE
    assert candidate.evaluation is None
    assert runner.timeout_seconds == 300
    assert "--use-amp" in runner.argv
    assert "epochs=2" in runner.argv
    assert "train_dataloader.collate_fn.stop_epoch=1" in runner.argv
    assert "train_dataloader.collate_fn.base_size_repeat=null" in runner.argv
    assert "train_dataloader.total_batch_size=2" in runner.argv
    assert (workspace / finished.checkpoint_ref).read_bytes() == b"trained-checkpoint"


def _successful_job(
    repository: InMemoryEventRepository,
    workspace: Path,
    *,
    suffix: str,
) -> ModelVersion:
    checkpoint = workspace / "runs" / suffix / "best_stg2.pth"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(f"checkpoint-{suffix}".encode())
    created = TrainingJob(
        job_id=f"job-{suffix}",
        dataset_id="approved",
        dataset_fingerprint="a" * 64,
        export_fingerprint=("b" if suffix == "one" else "c") * 64,
        export_ref=f"runs/{suffix}/dataset",
        architecture=DfineArchitecture.SMALL,
        category_names=["person"],
        verified_frame_count=100,
        train_frame_count=80,
        validation_frame_count=20,
        source_video_count=10,
        box_count=50,
        dfine_repository_revision="d" * 40,
        base_checkpoint_sha256="e" * 64,
        epochs=10,
        batch_size=2,
        max_gpu_minutes=60,
        daily_gpu_minutes=120,
        requested_by="operator",
        output_ref=f"runs/{suffix}",
    )
    repository.create_training_job(created)
    now = datetime.now(UTC)
    running = TrainingJob.model_validate(
        {
            **created.model_dump(),
            "status": TrainingJobStatus.RUNNING,
            "started_at": now,
            "updated_at": now,
            "revision": 2,
        }
    )
    repository.update_training_job(running)
    finished_at = datetime.now(UTC)
    succeeded = TrainingJob.model_validate(
        {
            **running.model_dump(),
            "status": TrainingJobStatus.SUCCEEDED,
            "checkpoint_ref": checkpoint.relative_to(workspace).as_posix(),
            "checkpoint_sha256": sha256_file(checkpoint),
            "finished_at": finished_at,
            "elapsed_seconds": 30,
            "updated_at": finished_at,
            "revision": 3,
        }
    )
    repository.update_training_job(succeeded)
    candidate = repository.create_model_version(
        ModelVersion(
            model_version_id=f"model-{suffix}",
            training_job_id=succeeded.job_id,
            architecture=succeeded.architecture,
            checkpoint_ref=succeeded.checkpoint_ref or "",
            checkpoint_sha256=succeeded.checkpoint_sha256 or "",
            dataset_fingerprint=succeeded.dataset_fingerprint,
            export_fingerprint=succeeded.export_fingerprint,
            dfine_repository_revision=succeeded.dfine_repository_revision,
        )
    )
    onnx = workspace / "models" / "dfine" / "candidates" / suffix / "model.onnx"
    onnx.parent.mkdir(parents=True, exist_ok=True)
    onnx.write_bytes(f"onnx-{suffix}".encode())
    exported_at = datetime.now(UTC)
    artifact_payload = {
        "artifact_version": "1.0.0",
        "onnx_ref": onnx.relative_to(workspace).as_posix(),
        "onnx_sha256": sha256_file(onnx),
        "source_checkpoint_sha256": candidate.checkpoint_sha256,
        "dfine_repository_revision": candidate.dfine_repository_revision,
        "input_names": ["images", "orig_target_sizes"],
        "output_names": ["labels", "boxes", "scores"],
        "input_size": 640,
        "category_names": ["person"],
        "source_log_sha256": "9" * 64,
        "exported_at": exported_at,
    }
    draft = DfineDeploymentArtifact.model_construct(
        **artifact_payload,
        artifact_fingerprint="0" * 64,
    )
    normalized = draft.model_dump(mode="json", exclude={"artifact_fingerprint"})
    deployment = DfineDeploymentArtifact.model_validate(
        {
            **normalized,
            "artifact_fingerprint": _sha(
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ),
        }
    )
    (onnx.parent / "config.json").write_text(
        json.dumps(
            {
                "config_version": "1.0.0",
                "id2label": {"0": "person"},
                "interest_labels": ["person"],
                "input_contract": ["images", "orig_target_sizes"],
                "output_contract": ["labels", "boxes", "scores"],
                "onnx_sha256": deployment.onnx_sha256,
                "deployment_fingerprint": deployment.artifact_fingerprint,
            }
        ),
        encoding="utf-8",
    )
    return repository.update_model_version(
        ModelVersion.model_validate(
            {
                **candidate.model_dump(),
                "deployment": deployment.model_dump(),
                "updated_at": exported_at,
                "revision": candidate.revision + 1,
            }
        )
    )


def _record_good_evaluation(registry: ModelRegistryService, version: ModelVersion) -> ModelVersion:
    return registry.record_evaluation(
        version.model_version_id,
        test_dataset_fingerprint="f" * 64,
        code_revision="1" * 40,
        map_50_95=0.7,
        map_50=0.85,
        critical_recall=0.95,
        false_alarms_per_hour=2.0,
        p95_latency_ms=150,
        peak_memory_mb=1024,
        repetitions=3,
        shadow_passed=True,
        evaluator="tester",
        measured_at=datetime.now(UTC),
        detector_report_sha256="2" * 64,
        e2e_artifact_sha256s=["3" * 64, "4" * 64, "5" * 64],
    )


def _promotion_policy(*, minimum_map: float = 0.5) -> PromotionPolicy:
    return PromotionPolicy(
        policy_version="promotion-test-v1",
        minimum_map_50_95=minimum_map,
        minimum_critical_recall=0.9,
        maximum_false_alarms_per_hour=4,
        maximum_p95_latency_ms=250,
        maximum_peak_memory_mb=4096,
        minimum_repetitions=3,
    )


def test_promotion_is_gated_and_failed_champion_rolls_back(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = InMemoryEventRepository()
    registry = ModelRegistryService(
        repository,
        workspace_root=workspace,
        registry_root=workspace / "models" / "dfine" / "local",
    )
    first = _record_good_evaluation(registry, _successful_job(repository, workspace, suffix="one"))
    champion_one = registry.promote(
        first.model_version_id,
        policy=_promotion_policy(),
        approved_by="operator",
        reason="ilk doğrulanmış sürüm",
    )
    second = _record_good_evaluation(registry, _successful_job(repository, workspace, suffix="two"))

    with pytest.raises(ModelRegistryError, match="terfi kapısından geçemedi") as rejected:
        registry.promote(
            second.model_version_id,
            policy=_promotion_policy(minimum_map=0.99),
            approved_by="operator",
            reason="ölçüm kapısını sınama",
        )
    assert rejected.value.code == "PROMOTION_GATE_REJECTED"
    assert any("mAP50-95" in reason for reason in rejected.value.reasons)

    champion_two = registry.promote(
        second.model_version_id,
        policy=_promotion_policy(),
        approved_by="operator",
        reason="ölçümler geçti",
    )
    (workspace / champion_two.checkpoint_ref).write_bytes(b"corrupted")
    restored = registry.reconcile_active_manifest()

    assert champion_one.stage == ModelStage.CHAMPION
    assert restored is not None
    assert restored.model_version_id == first.model_version_id
    assert restored.stage == ModelStage.CHAMPION
    assert restored.approved_by == "automatic-health-gate"
    assert repository.get_model_version(second.model_version_id).stage == ModelStage.RETIRED
    active = json.loads(
        (workspace / "models" / "dfine" / "local" / "active_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert active["model_version_id"] == first.model_version_id


def test_sqlite_training_job_and_candidate_survive_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = workspace / "event.sqlite3"
    repository = SqliteEventRepository(database)
    candidate = _successful_job(repository, workspace, suffix="one")
    repository.close()

    restarted = SqliteEventRepository(database)
    assert restarted.schema_version == 7
    assert restarted.get_training_job("job-one").status == TrainingJobStatus.SUCCEEDED
    assert restarted.get_model_version(candidate.model_version_id) == candidate
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        actions = {
            row[0]
            for row in connection.execute(
                "SELECT action FROM audit_log WHERE subject_type IN "
                "('training_job', 'model_version')"
            )
        }
    assert {"training_jobs", "model_versions"} <= tables
    assert {
        "training_job_queued",
        "training_job_running",
        "training_job_succeeded",
        "model_candidate_created",
    } <= actions


def test_default_policy_rejects_tiny_feedback_dataset(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    frame_root = workspace / "media"
    frame_root.mkdir(parents=True)
    train_frame = b"train-frame"
    validation_frame = b"validation-frame"
    (frame_root / "train").mkdir()
    (frame_root / "validation").mkdir()
    (frame_root / "train" / "a.jpg").write_bytes(train_frame)
    (frame_root / "validation" / "b.jpg").write_bytes(validation_frame)
    manifest = _manifest()
    repository = _SampleRepository(
        [
            _review(
                manifest,
                split=DatasetSplit.TRAIN,
                frame_ref="train/a.jpg",
                frame=train_frame,
            ),
            _review(
                manifest,
                split=DatasetSplit.VALIDATION,
                frame_ref="validation/b.jpg",
                frame=validation_frame,
            ),
        ]
    )
    dfine_repository = _fake_dfine_repository(tmp_path)
    base_checkpoint = tmp_path / "base.pth"
    base_checkpoint.write_bytes(b"base")
    service = DfineTrainingService(
        repository,
        workspace_root=workspace,
        frame_root=frame_root,
        runs_root=workspace / "runs",
        policy=DfineTrainingPolicy(policy_version="default-test"),
    )

    with pytest.raises(DfineTrainingError, match="doğrulanmış kare") as rejected:
        service.plan(
            dataset_manifest_path=write_dataset_manifest(workspace / "manifest.json", manifest),
            dfine_repository=dfine_repository,
            base_checkpoint=base_checkpoint,
            architecture=DfineArchitecture.SMALL,
            requested_by="operator",
        )
    assert rejected.value.code == "TRAINING_POLICY_REJECTED"
