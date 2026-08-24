

from __future__ import annotations

import asyncio

import pytest

from dortgoz.domain.model_lifecycle import (
    DfineArchitecture,
    DfineTrainingPolicy,
    TrainingJob,
    TrainingJobStatus,
)
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.dfine_training import DfineTrainingError, DfineTrainingService
from dortgoz.services.execution_coordinator import (
    ExclusiveWorkload,
    ExclusiveWorkloadActive,
    ExecutionCoordinator,
    LiveWorkloadActive,
)


async def test_live_leases_are_shared_and_block_exclusive(tmp_path) -> None:
    coordinator = ExecutionCoordinator(tmp_path / "event.sqlite3")
    first = await coordinator.acquire_live()
    second = await coordinator.acquire_live()

    with pytest.raises(LiveWorkloadActive):
        coordinator.acquire_exclusive(ExclusiveWorkload.SHADOW)

    await first.release_async()
    await second.release_async()
    exclusive = coordinator.acquire_exclusive(ExclusiveWorkload.SHADOW)
    exclusive.release()


def test_exclusive_lease_rejects_second_shadow(tmp_path) -> None:
    coordinator = ExecutionCoordinator(tmp_path / "event.sqlite3")
    first = coordinator.acquire_exclusive(ExclusiveWorkload.SHADOW)

    with pytest.raises(ExclusiveWorkloadActive):
        coordinator.acquire_exclusive(ExclusiveWorkload.SHADOW)

    first.release()


async def test_live_preempts_exclusive_but_waits_for_teardown(tmp_path) -> None:
    coordinator = ExecutionCoordinator(tmp_path / "event.sqlite3", poll_seconds=0.005)
    exclusive = coordinator.acquire_exclusive(ExclusiveWorkload.TRAINING)

    live_task = asyncio.create_task(coordinator.acquire_live(timeout_seconds=1.0))
    for _ in range(100):
        if exclusive.stop_requested():
            break
        await asyncio.sleep(0.005)

    assert exclusive.stop_requested() is True
    assert live_task.done() is False

    exclusive.release()
    live = await live_task
    await live.release_async()


async def test_training_worker_obeys_live_lease(tmp_path) -> None:
    frame_root = tmp_path / "media"
    frame_root.mkdir()
    repository = InMemoryEventRepository()
    job = repository.create_training_job(
        TrainingJob(
            job_id="queued-job",
            dataset_id="verified-dataset",
            dataset_fingerprint="a" * 64,
            export_fingerprint="b" * 64,
            export_ref="runs/export",
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
            max_gpu_minutes=1,
            daily_gpu_minutes=1,
            requested_by="operator",
            output_ref="runs/output",
        )
    )
    coordinator = ExecutionCoordinator(tmp_path / "event.sqlite3")
    service = DfineTrainingService(
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
            maximum_gpu_minutes_per_day=1,
        ),
        execution_coordinator=coordinator,
    )
    live = await coordinator.acquire_live()

    with pytest.raises(DfineTrainingError) as raised:
        service.execute(
            job.job_id,
            dfine_repository=tmp_path / "D-FINE",
            base_checkpoint=tmp_path / "base.pth",
            python_executable=tmp_path / "python",
        )

    assert raised.value.code == "LIVE_ANALYSIS_ACTIVE"
    assert repository.get_training_job(job.job_id).status == TrainingJobStatus.QUEUED
    await live.release_async()
