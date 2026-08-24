

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ..domain.model_lifecycle import TrainingJob, TrainingJobStatus
from ..repositories.errors import RepositoryConflictError
from ..repositories.protocols import EventRepository
from .execution_coordinator import ExclusiveWorkload, ExecutionCoordinator


@dataclass(frozen=True, slots=True)
class StartupReconciliationReport:
    boot_id: str
    training_scanned: int
    training_interrupted: int
    training_active_skipped: int
    training_conflicts: int
    analysis_interrupted: int = 0
    shadow_interrupted: int = 0


class StartupReconciliationService:


    def __init__(
        self,
        repository: EventRepository,
        execution_coordinator: ExecutionCoordinator,
        *,
        boot_id: str,
    ) -> None:
        if re.fullmatch(r"[0-9a-f]{32}", boot_id) is None:
            raise ValueError("boot_id 32 karakter küçük harf hex olmalıdır")
        self.repository = repository
        self.execution_coordinator = execution_coordinator
        self.boot_id = boot_id

    def reconcile(self, *, now: datetime | None = None) -> StartupReconciliationReport:
        reconciled_at = now or datetime.now(UTC)
        if reconciled_at.utcoffset() is None:
            raise ValueError("uzlaştırma zamanı saat dilimi içermelidir")
        training = self._reconcile_training_jobs(reconciled_at)
        return StartupReconciliationReport(
            boot_id=self.boot_id,
            training_scanned=training.scanned,
            training_interrupted=training.interrupted,
            training_active_skipped=training.active_skipped,
            training_conflicts=training.conflicts,
        )

    def _reconcile_training_jobs(self, reconciled_at: datetime) -> _TrainingResult:
        running = [
            job
            for job in self.repository.list_training_jobs()
            if job.status == TrainingJobStatus.RUNNING
        ]
        if not running:
            return _TrainingResult()

        owner = self.execution_coordinator.active_exclusive()
        if owner is not None and owner.workload == ExclusiveWorkload.TRAINING:


            return _TrainingResult(scanned=len(running), active_skipped=len(running))

        result = _TrainingResult(scanned=len(running))
        for job in running:
            interrupted = _interrupted_job(job, reconciled_at, self.boot_id)
            try:
                self.repository.update_training_job(interrupted)
            except RepositoryConflictError:


                current = self.repository.get_training_job(job.job_id)
                if current is not None and current.status == TrainingJobStatus.RUNNING:
                    raise
                result.conflicts += 1
            else:
                result.interrupted += 1
        return result


@dataclass(slots=True)
class _TrainingResult:
    scanned: int = 0
    interrupted: int = 0
    active_skipped: int = 0
    conflicts: int = 0


def _interrupted_job(job: TrainingJob, now: datetime, reconciler_boot_id: str) -> TrainingJob:
    assert job.started_at is not None
    finished_at = max(now, job.started_at)
    elapsed_seconds = min(
        (finished_at - job.started_at).total_seconds(),
        float(job.max_gpu_minutes * 60),
    )
    owner = job.worker_boot_id or "legacy-unknown"
    return TrainingJob.model_validate(
        {
            **job.model_dump(),
            "status": TrainingJobStatus.INTERRUPTED,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed_seconds,
            "error_code": "BACKEND_RESTARTED",
            "error_message": (
                "Başlangıç uzlaştırması canlı eğitim lease'i bulamadı; "
                f"worker_boot_id={owner}, reconciler_boot_id={reconciler_boot_id}"
            ),
            "updated_at": finished_at,
            "revision": job.revision + 1,
        }
    )


__all__ = ["StartupReconciliationReport", "StartupReconciliationService"]
