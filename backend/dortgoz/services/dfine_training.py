"""Bounded local D-FINE training planner and worker."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from ..domain.dataset import DatasetSplit
from ..domain.model_lifecycle import (
    DfineArchitecture,
    DfineTrainingPolicy,
    ModelVersion,
    TrainingJob,
    TrainingJobStatus,
)
from ..repositories.protocols import EventRepository
from .coco_export import export_verified_frames_to_coco, training_reviews_from_samples
from .dataset_manifest import load_dataset_manifest, sha256_file
from .execution_coordinator import (
    ExclusiveWorkload,
    ExclusiveWorkloadActive,
    ExecutionCoordinator,
    LiveWorkloadActive,
)
from .training_selection import (
    TrainingSelectionError,
    TrainingSelectionPolicy,
    TrainingSelectionReport,
    select_training_samples,
    write_training_selection_report,
)

_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_CONFIG_BY_ARCHITECTURE = {
    DfineArchitecture.NANO: "configs/dfine/custom/dfine_hgnetv2_n_custom.yml",
    DfineArchitecture.SMALL: "configs/dfine/custom/dfine_hgnetv2_s_custom.yml",
}
_CHECKPOINT_NAMES = ("best_stg2.pth", "best_stg1.pth", "best.pth", "last.pth")


class DfineTrainingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DfineRepositoryInfo:
    root: Path
    revision: str
    config_path: Path


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int
    elapsed_seconds: float
    stop_code: str | None = None


class ProcessRunner(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
        timeout_seconds: float,
        stop_probe: Callable[[], bool],
    ) -> ProcessOutcome: ...


class LocalProcessRunner:
    """Run without a shell and terminate the exact worker process tree on stop."""

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
        timeout_seconds: float,
        stop_probe: Callable[[], bool],
    ) -> ProcessOutcome:
        started = time.monotonic()
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
            )
            stop_code: str | None = None
            while process.poll() is None:
                elapsed = time.monotonic() - started
                if stop_probe():
                    stop_code = "LIVE_ANALYSIS_PREEMPTED"
                    _terminate_process_tree(process)
                    break
                if elapsed >= timeout_seconds:
                    stop_code = "GPU_TIME_BUDGET_EXCEEDED"
                    _terminate_process_tree(process)
                    break
                time.sleep(1)
            exit_code = process.wait()
        return ProcessOutcome(
            exit_code=exit_code,
            elapsed_seconds=time.monotonic() - started,
            stop_code=stop_code,
        )


class DfineTrainingService:
    """Create a reproducible job, then run it under resource supervision."""

    def __init__(
        self,
        repository: EventRepository,
        *,
        workspace_root: Path,
        frame_root: Path,
        runs_root: Path,
        policy: DfineTrainingPolicy,
        selection_policy: TrainingSelectionPolicy | None = None,
        process_runner: ProcessRunner | None = None,
        active_analysis_probe: Callable[[], bool] | None = None,
        cuda_probe: Callable[[Path, int], None] | None = None,
        execution_coordinator: ExecutionCoordinator | None = None,
        worker_boot_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.workspace_root = workspace_root.resolve()
        if frame_root.is_symlink():
            raise ValueError("frame_root symlink olamaz")
        self.frame_root = frame_root.resolve()
        self.runs_root = runs_root.resolve()
        self.policy = policy
        self.selection_policy = selection_policy
        self.process_runner = process_runner or LocalProcessRunner()
        self.active_analysis_probe = active_analysis_probe or (lambda: False)
        self.cuda_probe = cuda_probe or _validate_cuda_runtime
        self.execution_coordinator = execution_coordinator
        self.worker_boot_id = worker_boot_id or uuid4().hex
        if re.fullmatch(r"[0-9a-f]{32}", self.worker_boot_id) is None:
            raise ValueError("worker_boot_id 32 karakter küçük harf hex olmalıdır")
        if not self.runs_root.is_relative_to(self.workspace_root):
            raise ValueError("runs_root workspace içinde olmalıdır")
        if not self.frame_root.is_dir():
            raise ValueError(f"frame_root bulunamadı: {self.frame_root}")

    def plan(
        self,
        *,
        dataset_manifest_path: Path,
        dfine_repository: Path,
        base_checkpoint: Path,
        architecture: DfineArchitecture,
        requested_by: str,
        epochs: int = 10,
        batch_size: int = 2,
        workers: int = 2,
        gpu_index: int = 0,
        max_gpu_minutes: int = 60,
        seed: int = 0,
    ) -> TrainingJob:
        info = inspect_dfine_repository(dfine_repository, architecture)
        checkpoint = _validated_file(base_checkpoint, suffix=".pth")
        checkpoint_sha = sha256_file(checkpoint)
        manifest = load_dataset_manifest(dataset_manifest_path)
        samples = self.repository.list_training_samples()
        selection = None
        if self.selection_policy is not None:
            try:
                selection = select_training_samples(
                    samples=samples,
                    dataset_manifest=manifest,
                    policy=self.selection_policy,
                )
            except TrainingSelectionError as exc:
                raise DfineTrainingError(exc.code, str(exc)) from exc
            samples = selection.selected_samples
        reviews = training_reviews_from_samples(
            samples, manifest
        )
        train_count = sum(review.split == DatasetSplit.TRAIN for review in reviews)
        validation_count = sum(
            review.split == DatasetSplit.VALIDATION for review in reviews
        )
        source_video_count = len({review.dataset_video_id for review in reviews})
        categories = sorted(
            {box.category_name for review in reviews for box in review.boxes}
        )
        self._enforce_plan_policy(
            architecture=architecture,
            frame_count=len(reviews),
            train_count=train_count,
            validation_count=validation_count,
            source_video_count=source_video_count,
            epochs=epochs,
            batch_size=batch_size,
            workers=workers,
            max_gpu_minutes=max_gpu_minutes,
        )

        job_id = f"dfine-job-{uuid4()}"
        output_dir = self.runs_root / "dfine-training" / job_id
        export = export_verified_frames_to_coco(
            dataset_manifest=manifest,
            reviews=reviews,
            frame_root=self.frame_root,
            output_dir=output_dir / "dataset",
            selection_report=selection.report if selection is not None else None,
        )
        if selection is not None:
            try:
                write_training_selection_report(
                    export.output_dir / "selection_report.json", selection.report
                )
            except TrainingSelectionError as exc:
                raise DfineTrainingError(exc.code, str(exc)) from exc
        job = TrainingJob(
            job_id=job_id,
            dataset_id=manifest.dataset_id,
            dataset_fingerprint=manifest.dataset_fingerprint,
            export_fingerprint=export.export_fingerprint,
            export_ref=self._reference(export.output_dir),
            selection_policy_version=(
                selection.report.policy_version if selection is not None else None
            ),
            selection_policy_fingerprint=(
                selection.report.policy_fingerprint if selection is not None else None
            ),
            selection_fingerprint=(
                selection.report.selection_fingerprint if selection is not None else None
            ),
            architecture=architecture,
            category_names=categories,
            verified_frame_count=export.frame_count,
            train_frame_count=train_count,
            validation_frame_count=validation_count,
            source_video_count=source_video_count,
            box_count=export.box_count,
            dfine_repository_revision=info.revision,
            base_checkpoint_sha256=checkpoint_sha,
            seed=seed,
            epochs=epochs,
            batch_size=batch_size,
            workers=workers,
            gpu_index=gpu_index,
            max_gpu_minutes=max_gpu_minutes,
            daily_gpu_minutes=self.policy.maximum_gpu_minutes_per_day,
            requested_by=requested_by,
            output_ref=self._reference(output_dir / "output"),
        )
        return self.repository.create_training_job(job)

    def execute(
        self,
        job_id: str,
        *,
        dfine_repository: Path,
        base_checkpoint: Path,
        python_executable: Path,
    ) -> tuple[TrainingJob, ModelVersion]:
        job = self.repository.get_training_job(job_id)
        if job is None:
            raise DfineTrainingError("TRAINING_JOB_NOT_FOUND", f"iş bulunamadı: {job_id}")
        if job.status != TrainingJobStatus.QUEUED:
            raise DfineTrainingError(
                "TRAINING_JOB_NOT_QUEUED",
                f"iş kuyruğa alınmış durumda değil: {job.status.value}",
            )
        exclusive_lease = None
        if self.execution_coordinator is not None:
            try:
                exclusive_lease = self.execution_coordinator.acquire_exclusive(
                    ExclusiveWorkload.TRAINING,
                    owner_ref=job.job_id,
                    owner_boot_id=self.worker_boot_id,
                )
            except LiveWorkloadActive as exc:
                raise DfineTrainingError("LIVE_ANALYSIS_ACTIVE", str(exc)) from exc
            except ExclusiveWorkloadActive as exc:
                raise DfineTrainingError("EXCLUSIVE_WORKLOAD_ACTIVE", str(exc)) from exc

        def stop_probe() -> bool:
            return self.active_analysis_probe() or bool(
                exclusive_lease is not None and exclusive_lease.stop_requested()
            )

        try:
            return self._execute_under_lease(
                job,
                dfine_repository=dfine_repository,
                base_checkpoint=base_checkpoint,
                python_executable=python_executable,
                stop_probe=stop_probe,
            )
        finally:
            if exclusive_lease is not None:
                exclusive_lease.release()

    def _execute_under_lease(
        self,
        job: TrainingJob,
        *,
        dfine_repository: Path,
        base_checkpoint: Path,
        python_executable: Path,
        stop_probe: Callable[[], bool],
    ) -> tuple[TrainingJob, ModelVersion]:
        if stop_probe():
            raise DfineTrainingError(
                "LIVE_ANALYSIS_ACTIVE",
                "canlı analiz çalışırken D-FINE eğitimi başlatılamaz",
            )
        info = inspect_dfine_repository(dfine_repository, job.architecture)
        if info.revision != job.dfine_repository_revision:
            raise DfineTrainingError(
                "DFINE_REVISION_CHANGED",
                "D-FINE repository commit'i planlanan commit ile eşleşmiyor",
            )
        checkpoint = _validated_file(base_checkpoint, suffix=".pth")
        if sha256_file(checkpoint) != job.base_checkpoint_sha256:
            raise DfineTrainingError(
                "BASE_CHECKPOINT_CHANGED",
                "başlangıç checkpoint SHA-256 değeri değişti",
            )
        python = _validated_file(python_executable)
        self.cuda_probe(python, job.gpu_index)
        export_dir = self._resolve_reference(job.export_ref)
        output_dir = self._resolve_reference(job.output_ref)
        _verify_export(job, export_dir)
        if stop_probe():
            raise DfineTrainingError(
                "LIVE_ANALYSIS_ACTIVE",
                "canlı analiz önceliği eğitim doğrulamasını durdurdu",
            )
        remaining_minutes = self._remaining_daily_gpu_minutes(job)
        if remaining_minutes <= 0:
            raise DfineTrainingError(
                "DAILY_GPU_BUDGET_EXHAUSTED",
                "günlük D-FINE GPU dakika bütçesi doldu",
            )

        now = datetime.now(UTC)
        running = TrainingJob.model_validate(
            {
                **job.model_dump(),
                "status": TrainingJobStatus.RUNNING,
                "worker_boot_id": self.worker_boot_id,
                "started_at": now,
                "updated_at": now,
                "revision": job.revision + 1,
            }
        )
        running = self.repository.update_training_job(running)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_dfine_command(
            job=running,
            python_executable=python,
            repository_info=info,
            base_checkpoint=checkpoint,
            export_dir=export_dir,
            frame_root=self.frame_root,
            output_dir=output_dir,
        )
        env = dict(os.environ)
        env.update(
            {
                "CUDA_VISIBLE_DEVICES": str(job.gpu_index),
                "PYTHONUNBUFFERED": "1",
            }
        )
        budget_seconds = min(job.max_gpu_minutes, remaining_minutes) * 60
        try:
            outcome = self.process_runner.run(
                command,
                cwd=info.root,
                env=env,
                log_path=output_dir / "training.log",
                timeout_seconds=budget_seconds,
                stop_probe=stop_probe,
            )
            if outcome.stop_code is not None:
                stopped = self._terminal_job(
                    running,
                    status=TrainingJobStatus.BUDGET_STOPPED,
                    elapsed_seconds=outcome.elapsed_seconds,
                    error_code=outcome.stop_code,
                    error_message=(
                        "canlı analiz yükü başladığı için eğitim durduruldu"
                        if outcome.stop_code == "LIVE_ANALYSIS_PREEMPTED"
                        else "D-FINE eğitim GPU süre bütçesi doldu"
                    ),
                )
                raise DfineTrainingError(outcome.stop_code, stopped.error_message or "")
            if outcome.exit_code != 0:
                failed = self._terminal_job(
                    running,
                    status=TrainingJobStatus.FAILED,
                    elapsed_seconds=outcome.elapsed_seconds,
                    error_code="DFINE_PROCESS_FAILED",
                    error_message=f"D-FINE süreci {outcome.exit_code} koduyla sonlandı",
                )
                raise DfineTrainingError("DFINE_PROCESS_FAILED", failed.error_message or "")
            checkpoint_result = _select_checkpoint(output_dir)
            checkpoint_ref = self._reference(checkpoint_result)
            checkpoint_sha = sha256_file(checkpoint_result)
            finished_at = datetime.now(UTC)
            succeeded = TrainingJob.model_validate(
                {
                    **running.model_dump(),
                    "status": TrainingJobStatus.SUCCEEDED,
                    "checkpoint_ref": checkpoint_ref,
                    "checkpoint_sha256": checkpoint_sha,
                    "finished_at": finished_at,
                    "elapsed_seconds": outcome.elapsed_seconds,
                    "updated_at": finished_at,
                    "revision": running.revision + 1,
                }
            )
            succeeded = self.repository.update_training_job(succeeded)
            version = self.repository.create_model_version(
                ModelVersion(
                    model_version_id=f"dfine-model-{uuid4()}",
                    training_job_id=succeeded.job_id,
                    architecture=succeeded.architecture,
                    checkpoint_ref=checkpoint_ref,
                    checkpoint_sha256=checkpoint_sha,
                    dataset_fingerprint=succeeded.dataset_fingerprint,
                    export_fingerprint=succeeded.export_fingerprint,
                    dfine_repository_revision=succeeded.dfine_repository_revision,
                )
            )
            return succeeded, version
        except DfineTrainingError as exc:
            current = self.repository.get_training_job(job.job_id)
            if current is not None and current.status == TrainingJobStatus.RUNNING:
                self._terminal_job(
                    current,
                    status=TrainingJobStatus.FAILED,
                    elapsed_seconds=(datetime.now(UTC) - now).total_seconds(),
                    error_code=exc.code,
                    error_message=str(exc),
                )
            raise
        except KeyboardInterrupt as exc:
            self._terminal_job(
                running,
                status=TrainingJobStatus.CANCELLED,
                elapsed_seconds=(datetime.now(UTC) - now).total_seconds(),
                error_code="TRAINING_CANCELLED",
                error_message="D-FINE eğitimi kullanıcı tarafından durduruldu",
            )
            raise DfineTrainingError("TRAINING_CANCELLED", "eğitim durduruldu") from exc
        except Exception as exc:
            current = self.repository.get_training_job(job.job_id)
            if current is not None and current.status == TrainingJobStatus.RUNNING:
                self._terminal_job(
                    current,
                    status=TrainingJobStatus.FAILED,
                    elapsed_seconds=(datetime.now(UTC) - now).total_seconds(),
                    error_code="TRAINING_WORKER_FAILED",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            raise DfineTrainingError(
                "TRAINING_WORKER_FAILED", f"D-FINE worker hatası: {exc}"
            ) from exc

    def _terminal_job(
        self,
        job: TrainingJob,
        *,
        status: TrainingJobStatus,
        elapsed_seconds: float,
        error_code: str,
        error_message: str,
    ) -> TrainingJob:
        now = datetime.now(UTC)
        terminal = TrainingJob.model_validate(
            {
                **job.model_dump(),
                "status": status,
                "finished_at": now,
                "elapsed_seconds": elapsed_seconds,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": now,
                "revision": job.revision + 1,
            }
        )
        return self.repository.update_training_job(terminal)

    def _remaining_daily_gpu_minutes(self, job: TrainingJob) -> int:
        today = datetime.now(UTC).date()
        used_seconds = sum(
            item.elapsed_seconds
            for item in self.repository.list_training_jobs()
            if item.job_id != job.job_id
            and item.started_at is not None
            and item.started_at.date() == today
        )
        return max(0, job.daily_gpu_minutes - int((used_seconds + 59) // 60))

    def _enforce_plan_policy(
        self,
        *,
        architecture: DfineArchitecture,
        frame_count: int,
        train_count: int,
        validation_count: int,
        source_video_count: int,
        epochs: int,
        batch_size: int,
        workers: int,
        max_gpu_minutes: int,
    ) -> None:
        failures: list[str] = []
        if architecture not in self.policy.allowed_architectures:
            failures.append(f"architecture izinli değil: {architecture.value}")
        for actual, minimum, label in (
            (frame_count, self.policy.minimum_verified_frames, "doğrulanmış kare"),
            (train_count, self.policy.minimum_train_frames, "train karesi"),
            (
                validation_count,
                self.policy.minimum_validation_frames,
                "validation karesi",
            ),
            (source_video_count, self.policy.minimum_source_videos, "kaynak video"),
        ):
            if actual < minimum:
                failures.append(f"{label}: {actual} < {minimum}")
        if epochs > self.policy.maximum_epochs:
            failures.append(f"epoch: {epochs} > {self.policy.maximum_epochs}")
        if batch_size > self.policy.maximum_batch_size:
            failures.append(f"batch: {batch_size} > {self.policy.maximum_batch_size}")
        if workers > self.policy.maximum_workers:
            failures.append(f"worker: {workers} > {self.policy.maximum_workers}")
        if max_gpu_minutes > self.policy.maximum_gpu_minutes_per_job:
            failures.append(
                f"GPU dakika: {max_gpu_minutes} > "
                f"{self.policy.maximum_gpu_minutes_per_job}"
            )
        if failures:
            raise DfineTrainingError(
                "TRAINING_POLICY_REJECTED", "; ".join(failures)
            )

    def _reference(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace_root):
            raise DfineTrainingError(
                "UNSAFE_TRAINING_PATH", f"training yolu workspace dışında: {path}"
            )
        return resolved.relative_to(self.workspace_root).as_posix()

    def _resolve_reference(self, reference: str) -> Path:
        resolved = self.workspace_root.joinpath(*reference.split("/")).resolve()
        if not resolved.is_relative_to(self.workspace_root):
            raise DfineTrainingError("UNSAFE_TRAINING_PATH", reference)
        return resolved


def inspect_dfine_repository(
    repository: Path, architecture: DfineArchitecture
) -> DfineRepositoryInfo:
    if repository.is_symlink():
        raise DfineTrainingError(
            "DFINE_REPOSITORY_MISSING", "D-FINE deposu symlink olamaz"
        )
    root = repository.resolve()
    if not root.is_dir():
        raise DfineTrainingError("DFINE_REPOSITORY_MISSING", f"D-FINE deposu yok: {root}")
    license_path = root / "LICENSE"
    if license_path.is_symlink():
        raise DfineTrainingError("DFINE_LICENSE_MISSING", "D-FINE LICENSE symlink olamaz")
    try:
        license_text = license_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DfineTrainingError("DFINE_LICENSE_MISSING", "D-FINE LICENSE okunamadı") from exc
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        raise DfineTrainingError(
            "DFINE_LICENSE_REJECTED", "D-FINE deposu Apache-2.0 olarak doğrulanamadı"
        )
    train_path = root / "train.py"
    config_path = root / _CONFIG_BY_ARCHITECTURE[architecture]
    if (
        not train_path.is_file()
        or train_path.is_symlink()
        or not config_path.is_file()
        or config_path.is_symlink()
    ):
        raise DfineTrainingError(
            "DFINE_LAYOUT_INVALID", "D-FINE train.py veya custom config bulunamadı"
        )
    revision = _git_output(root, "rev-parse", "HEAD")
    if not _REVISION_PATTERN.fullmatch(revision):
        raise DfineTrainingError("DFINE_REVISION_INVALID", "D-FINE commit SHA geçersiz")
    if _git_output(root, "status", "--porcelain"):
        raise DfineTrainingError(
            "DFINE_REPOSITORY_DIRTY", "D-FINE çalışma ağacı temiz olmalıdır"
        )
    return DfineRepositoryInfo(root=root, revision=revision, config_path=config_path)


def build_dfine_command(
    *,
    job: TrainingJob,
    python_executable: Path,
    repository_info: DfineRepositoryInfo,
    base_checkpoint: Path,
    export_dir: Path,
    frame_root: Path,
    output_dir: Path,
) -> list[str]:
    def quote(value: Path) -> str:
        return json.dumps(value.as_posix())

    stage_epoch = max(1, job.epochs - 2)
    return [
        str(python_executable),
        str(repository_info.root / "train.py"),
        "-c",
        str(repository_info.config_path),
        "-d",
        "cuda",
        "--use-amp",
        "--seed",
        str(job.seed),
        "--output-dir",
        str(output_dir),
        "-t",
        str(base_checkpoint),
        "-u",
        f"num_classes={len(job.category_names)}",
        "remap_mscoco_category=False",
        f"epochs={job.epochs}",
        f"train_dataloader.dataset.transforms.policy.epoch={stage_epoch}",
        f"train_dataloader.collate_fn.stop_epoch={stage_epoch}",
        "train_dataloader.collate_fn.base_size_repeat=null",
        f"train_dataloader.total_batch_size={job.batch_size}",
        f"val_dataloader.total_batch_size={job.batch_size}",
        f"train_dataloader.num_workers={job.workers}",
        f"val_dataloader.num_workers={job.workers}",
        f"train_dataloader.dataset.img_folder={quote(frame_root)}",
        f"train_dataloader.dataset.ann_file={quote(export_dir / 'annotations/instances_train.json')}",
        f"val_dataloader.dataset.img_folder={quote(frame_root)}",
        f"val_dataloader.dataset.ann_file={quote(export_dir / 'annotations/instances_validation.json')}",
    ]


def _verify_export(job: TrainingJob, export_dir: Path) -> None:
    manifest_path = export_dir / "export_manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DfineTrainingError("COCO_EXPORT_INVALID", "COCO export manifest okunamadı") from exc
    if (
        payload.get("export_fingerprint") != job.export_fingerprint
        or payload.get("dataset_fingerprint") != job.dataset_fingerprint
        or payload.get("categories") != job.category_names
        or payload.get("counts", {}).get("frames") != job.verified_frame_count
        or payload.get("counts", {}).get("boxes") != job.box_count
    ):
        raise DfineTrainingError(
            "COCO_EXPORT_CHANGED", "COCO export manifest training job ile eşleşmiyor"
        )
    if job.selection_fingerprint is not None and (
        payload.get("selection", {}).get("selection_fingerprint")
        != job.selection_fingerprint
        or payload.get("selection", {}).get("policy_version")
        != job.selection_policy_version
        or payload.get("selection", {}).get("policy_fingerprint")
        != job.selection_policy_fingerprint
    ):
        raise DfineTrainingError(
            "COCO_SELECTION_CHANGED", "COCO seçim kaydı training job ile eşleşmiyor"
        )
    if job.selection_fingerprint is not None:
        selection_path = export_dir / "selection_report.json"
        if selection_path.is_symlink():
            raise DfineTrainingError(
                "COCO_SELECTION_INVALID", "COCO seçim raporu symlink olamaz"
            )
        try:
            selection_report = TrainingSelectionReport.model_validate_json(
                selection_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise DfineTrainingError(
                "COCO_SELECTION_INVALID", "COCO seçim raporu okunamadı"
            ) from exc
        if (
            selection_report.selection_fingerprint != job.selection_fingerprint
            or selection_report.policy_fingerprint
            != job.selection_policy_fingerprint
            or selection_report.policy_version != job.selection_policy_version
        ):
            raise DfineTrainingError(
                "COCO_SELECTION_CHANGED", "COCO seçim raporu training job ile eşleşmiyor"
            )
    for split, filename in (
        ("train", "instances_train.json"),
        ("validation", "instances_validation.json"),
    ):
        path = export_dir / "annotations" / filename
        if not path.is_file() or path.is_symlink():
            raise DfineTrainingError("COCO_EXPORT_INVALID", f"annotation yok: {filename}")
        expected = payload.get("coco_sha256", {}).get(split)
        if not isinstance(expected, str) or sha256_file(path) != expected:
            raise DfineTrainingError("COCO_EXPORT_CHANGED", f"annotation değişti: {filename}")


def _select_checkpoint(output_dir: Path) -> Path:
    for name in _CHECKPOINT_NAMES:
        candidate = output_dir / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve()
    candidates = sorted(
        (
            path
            for path in output_dir.glob("*.pth")
            if path.is_file() and not path.is_symlink()
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        raise DfineTrainingError(
            "DFINE_CHECKPOINT_MISSING", "başarılı süreç checkpoint üretmedi"
        )
    return candidates[0].resolve()


def _validated_file(path: Path, *, suffix: str | None = None) -> Path:
    if path.is_symlink():
        raise DfineTrainingError("LOCAL_FILE_INVALID", f"dosya symlink olamaz: {path}")
    resolved = path.resolve()
    if not resolved.is_file():
        raise DfineTrainingError("LOCAL_FILE_MISSING", f"yerel dosya bulunamadı: {path}")
    if suffix is not None and resolved.suffix.casefold() != suffix:
        raise DfineTrainingError("LOCAL_FILE_INVALID", f"dosya uzantısı {suffix} olmalıdır")
    return resolved


def _validate_cuda_runtime(python_executable: Path, gpu_index: int) -> None:
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
    probe = subprocess.run(
        [
            str(python_executable),
            "-c",
            (
                "import torch,sys;"
                "sys.exit(0 if torch.cuda.is_available() and torch.cuda.device_count()==1 else 1)"
            ),
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        timeout=30,
        check=False,
    )
    if probe.returncode != 0:
        raise DfineTrainingError(
            "CUDA_RUNTIME_UNAVAILABLE",
            "seçilen Python ortamında tek görünür CUDA GPU doğrulanamadı",
        )


def _git_output(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            shell=False,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DfineTrainingError("DFINE_GIT_CHECK_FAILED", f"git kontrolü başarısız: {exc}") from exc
    return result.stdout.strip()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=15,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)


__all__ = [
    "DfineRepositoryInfo",
    "DfineTrainingError",
    "DfineTrainingService",
    "LocalProcessRunner",
    "ProcessOutcome",
    "build_dfine_command",
    "inspect_dfine_repository",
]
