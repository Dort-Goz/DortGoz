"""Başlangıç uzlaştırması ve eğitim işi CAS güvenliği testleri."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dortgoz.domain.model_lifecycle import (
    DfineArchitecture,
    DfineTrainingPolicy,
    TrainingJob,
    TrainingJobStatus,
)
from dortgoz.repositories.errors import RepositoryConflictError
from dortgoz.repositories.sqlite import SqliteEventRepository
from dortgoz.services.dfine_training import DfineTrainingError, DfineTrainingService
from dortgoz.services.execution_coordinator import ExclusiveWorkload, ExecutionCoordinator
from dortgoz.services.startup_reconciliation import StartupReconciliationService


def _queued_job(job_id: str, *, max_gpu_minutes: int = 1) -> TrainingJob:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return TrainingJob(
        job_id=job_id,
        dataset_id="verified-dataset",
        dataset_fingerprint="a" * 64,
        export_fingerprint="b" * 64,
        export_ref=f"runs/{job_id}/dataset",
        architecture=DfineArchitecture.SMALL,
        category_names=["person"],
        verified_frame_count=2,
        train_frame_count=1,
        validation_frame_count=1,
        source_video_count=2,
        box_count=1,
        dfine_repository_revision="c" * 40,
        base_checkpoint_sha256="d" * 64,
        epochs=1,
        batch_size=1,
        max_gpu_minutes=max_gpu_minutes,
        daily_gpu_minutes=10,
        requested_by="operator",
        output_ref=f"runs/{job_id}/output",
        created_at=created_at,
        updated_at=created_at,
    )


def _running_job(
    repository: SqliteEventRepository,
    job: TrainingJob,
    *,
    started_at: datetime,
    boot_id: str,
) -> TrainingJob:
    return repository.update_training_job(
        TrainingJob.model_validate(
            {
                **job.model_dump(),
                "status": TrainingJobStatus.RUNNING,
                "worker_boot_id": boot_id,
                "started_at": started_at,
                "updated_at": started_at,
                "revision": job.revision + 1,
            }
        )
    )


def _service(
    repository: SqliteEventRepository,
    coordinator: ExecutionCoordinator,
    *,
    boot_id: str,
) -> StartupReconciliationService:
    return StartupReconciliationService(
        repository,
        coordinator,
        boot_id=boot_id,
    )


def test_orphan_training_is_interrupted_once_and_elapsed_is_capped(tmp_path: Path) -> None:
    database = tmp_path / "event.sqlite3"
    repository = SqliteEventRepository(database)
    coordinator = ExecutionCoordinator(database)
    now = datetime.now(UTC)
    queued = repository.create_training_job(_queued_job("orphan-job"))
    running = _running_job(
        repository,
        queued,
        started_at=now - timedelta(days=2),
        boot_id="1" * 32,
    )
    partial = tmp_path / running.output_ref / "last.pth"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial-checkpoint")
    reconciler = _service(repository, coordinator, boot_id="2" * 32)

    first = reconciler.reconcile(now=now)
    saved = repository.get_training_job(running.job_id)
    second = reconciler.reconcile(now=now + timedelta(seconds=1))

    assert first.training_interrupted == 1
    assert second.training_interrupted == 0
    assert saved is not None
    assert saved.status == TrainingJobStatus.INTERRUPTED
    assert saved.elapsed_seconds == 60
    assert saved.error_code == "BACKEND_RESTARTED"
    assert saved.worker_boot_id == "1" * 32
    assert repository.list_model_versions() == []
    with sqlite3.connect(database) as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'training_job_interrupted'"
        ).fetchone()[0]
    assert audit_count == 1


def test_live_training_lease_protects_job_from_second_backend(tmp_path: Path) -> None:
    database = tmp_path / "event.sqlite3"
    repository = SqliteEventRepository(database)
    coordinator = ExecutionCoordinator(database)
    worker_boot_id = "3" * 32
    queued = repository.create_training_job(_queued_job("active-job"))
    lease = coordinator.acquire_exclusive(
        ExclusiveWorkload.TRAINING,
        owner_ref=queued.job_id,
        owner_boot_id=worker_boot_id,
    )
    running = _running_job(
        repository,
        queued,
        started_at=datetime.now(UTC) - timedelta(seconds=5),
        boot_id=worker_boot_id,
    )

    report = _service(repository, coordinator, boot_id="4" * 32).reconcile()

    saved = repository.get_training_job(running.job_id)
    assert report.training_active_skipped == 1
    assert report.training_interrupted == 0
    assert saved is not None and saved.status == TrainingJobStatus.RUNNING
    lease.release()


def test_training_update_uses_sqlite_compare_and_swap(tmp_path: Path) -> None:
    database = tmp_path / "event.sqlite3"
    first_repository = SqliteEventRepository(database)
    queued = first_repository.create_training_job(_queued_job("racing-job"))
    running = _running_job(
        first_repository,
        queued,
        started_at=datetime.now(UTC) - timedelta(seconds=10),
        boot_id="5" * 32,
    )
    stale_repository = SqliteEventRepository(database)
    finished_at = datetime.now(UTC)
    first_repository.update_training_job(
        TrainingJob.model_validate(
            {
                **running.model_dump(),
                "status": TrainingJobStatus.FAILED,
                "finished_at": finished_at,
                "elapsed_seconds": 10,
                "error_code": "TEST_FAILURE",
                "error_message": "eşzamanlı iş terminal oldu",
                "updated_at": finished_at,
                "revision": running.revision + 1,
            }
        )
    )

    report = _service(
        stale_repository,
        ExecutionCoordinator(database),
        boot_id="6" * 32,
    ).reconcile(now=finished_at + timedelta(seconds=1))

    saved = stale_repository.get_training_job(running.job_id)
    assert report.training_conflicts == 1
    assert saved is not None and saved.status == TrainingJobStatus.FAILED
    assert saved.error_code == "TEST_FAILURE"


def test_interrupted_job_is_terminal_and_counts_toward_daily_budget(tmp_path: Path) -> None:
    database = tmp_path / "event.sqlite3"
    repository = SqliteEventRepository(database)
    coordinator = ExecutionCoordinator(database)
    now = datetime.now(UTC)
    queued = repository.create_training_job(_queued_job("budget-job"))
    running = _running_job(
        repository,
        queued,
        started_at=now - timedelta(hours=3),
        boot_id="7" * 32,
    )
    _service(repository, coordinator, boot_id="8" * 32).reconcile(now=now)
    interrupted = repository.get_training_job(running.job_id)
    assert interrupted is not None

    invalid_transition = TrainingJob.model_validate(
        {
            **interrupted.model_dump(),
            "status": TrainingJobStatus.FAILED,
            "error_code": "LATE_FAILURE",
            "error_message": "terminal iş tekrar değiştirilemez",
            "updated_at": now + timedelta(seconds=1),
            "revision": interrupted.revision + 1,
        }
    )
    with pytest.raises(RepositoryConflictError):
        repository.update_training_job(invalid_transition)

    next_job = repository.create_training_job(_queued_job("next-job"))
    frame_root = tmp_path / "media"
    frame_root.mkdir()
    training = DfineTrainingService(
        repository,
        workspace_root=tmp_path,
        frame_root=frame_root,
        runs_root=tmp_path / "runs",
        policy=DfineTrainingPolicy(
            policy_version="test-v1",
            minimum_verified_frames=2,
            minimum_train_frames=1,
            minimum_validation_frames=1,
            minimum_source_videos=2,
            maximum_gpu_minutes_per_job=1,
            maximum_gpu_minutes_per_day=10,
        ),
    )

    assert training._remaining_daily_gpu_minutes(next_job) == 9
    with pytest.raises(DfineTrainingError) as raised:
        training.execute(
            interrupted.job_id,
            dfine_repository=tmp_path / "D-FINE",
            base_checkpoint=tmp_path / "base.pth",
            python_executable=tmp_path / "python",
        )
    assert raised.value.code == "TRAINING_JOB_NOT_QUEUED"
