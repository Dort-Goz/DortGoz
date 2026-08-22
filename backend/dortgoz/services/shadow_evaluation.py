"""Run a deployed D-FINE candidate through three isolated canonical shadow passes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import settings
from ..domain.dataset import DatasetSplit, DatasetUse, OfflineDatasetManifest
from ..domain.model_lifecycle import ModelStage, ModelVersion
from ..pipeline import ingest, perception
from ..pipeline.runner import PipelineStopRequested, run_video
from .dataset_manifest import sha256_file
from .dfine_deployment import verify_dfine_deployment
from .dfine_evaluation import inspect_project_revision
from .evaluation_report import EvaluationReportError, ShadowEvaluationRecord
from .execution_coordinator import (
    ExclusiveWorkload,
    ExclusiveWorkloadActive,
    ExecutionCoordinator,
    LiveWorkloadActive,
)


class ShadowCase(BaseModel):
    """Human-labelled video-level expectation for one shadow case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    video_ref: str = Field(min_length=1)
    video_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_critical: bool
    is_normal: bool

    @model_validator(mode="after")
    def case_is_safe_and_labelled(self) -> ShadowCase:
        if not _safe_reference(self.video_ref):
            raise ValueError("shadow video_ref güvenli göreli POSIX yol olmalıdır")
        if self.expected_critical == self.is_normal:
            raise ValueError("shadow case tam olarak kritik veya normal olmalıdır")
        return self


class ShadowCaseManifest(BaseModel):
    """Small human-authored case list; video bytes remain outside Git."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: Literal["1.0.0"] = "1.0.0"
    test_dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[ShadowCase] = Field(min_length=2)
    labelled_by: str = Field(min_length=1, max_length=120)
    labelled_at: datetime

    @model_validator(mode="after")
    def coverage_is_complete(self) -> ShadowCaseManifest:
        if self.labelled_at.utcoffset() is None:
            raise ValueError("labelled_at saat dilimi içermelidir")
        ids = [case.case_id for case in self.cases]
        refs = [case.video_ref for case in self.cases]
        hashes = [case.video_sha256 for case in self.cases]
        if len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
            raise ValueError("shadow case id ve video_ref benzersiz olmalıdır")
        if len(hashes) != len(set(hashes)):
            raise ValueError("aynı video bir shadow manifestinde tekrar kullanılamaz")
        if not any(case.expected_critical for case in self.cases):
            raise ValueError("shadow manifest en az bir kritik video gerektirir")
        if not any(case.is_normal for case in self.cases):
            raise ValueError("shadow manifest en az bir normal video gerektirir")
        return self


class ShadowEvaluationPlan(BaseModel):
    """Immutable candidate, videos, code, and three repetition identities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_version: Literal["1.0.0"] = "1.0.0"
    plan_id: str = Field(min_length=1)
    model_version_id: str = Field(min_length=1)
    candidate_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    deployment_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    onnx_ref: str = Field(min_length=1)
    onnx_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    media_root_ref: str = Field(min_length=1)
    case_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[ShadowCase] = Field(min_length=2)
    evaluation_run_ids: list[str] = Field(min_length=3)
    created_by: str = Field(min_length=1, max_length=120)
    created_at: datetime
    plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def plan_is_reproducible(self) -> ShadowEvaluationPlan:
        if not _safe_reference(self.onnx_ref) or not _safe_reference(self.media_root_ref):
            raise ValueError("shadow plan yolları güvenli göreli POSIX olmalıdır")
        if self.created_at.utcoffset() is None:
            raise ValueError("shadow plan created_at saat dilimi içermelidir")
        if len(self.evaluation_run_ids) != len(set(self.evaluation_run_ids)):
            raise ValueError("shadow evaluation run kimlikleri benzersiz olmalıdır")
        payload = self.model_dump(mode="json", exclude={"plan_fingerprint"})
        if self.plan_fingerprint != _payload_sha256(payload):
            raise ValueError("shadow plan fingerprint içerikle eşleşmiyor")
        return self


def load_shadow_case_manifest(path: Path) -> ShadowCaseManifest:
    try:
        return ShadowCaseManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationReportError(
            "SHADOW_CASE_MANIFEST_INVALID", f"shadow case manifest okunamadı: {exc}"
        ) from exc


def prepare_shadow_evaluation(
    *,
    candidate: ModelVersion,
    test_dataset_manifest: OfflineDatasetManifest,
    case_manifest: ShadowCaseManifest,
    case_manifest_path: Path,
    workspace_root: Path,
    media_root: Path,
    code_revision: str,
    created_by: str,
    repetitions: int = 3,
    now: datetime | None = None,
) -> ShadowEvaluationPlan:
    """Verify candidate/video provenance and freeze one three-pass plan."""

    if (
        candidate.stage != ModelStage.CANDIDATE
        or candidate.evaluation is not None
        or candidate.deployment is None
    ):
        raise EvaluationReportError(
            "MODEL_NOT_SHADOWABLE",
            "shadow plan deployment taşıyan değerlendirilmemiş candidate gerektirir",
        )
    if repetitions < 3:
        raise EvaluationReportError(
            "EVALUATION_REPETITIONS_MISSING", "shadow plan en az üç tekrar gerektirir"
        )
    if case_manifest.test_dataset_fingerprint != test_dataset_manifest.dataset_fingerprint:
        raise EvaluationReportError(
            "SHADOW_DATASET_MISMATCH", "shadow case manifest test dataset ile eşleşmiyor"
        )
    if not (
        DatasetUse.EVALUATION in test_dataset_manifest.allowed_uses
        or DatasetUse.BENCHMARK in test_dataset_manifest.allowed_uses
    ):
        raise EvaluationReportError(
            "TEST_DATASET_USE_REJECTED",
            "test dataset evaluation veya benchmark kullanımına açık değil",
        )
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise EvaluationReportError("PROJECT_REVISION_INVALID", "proje commit SHA değeri geçersiz")
    workspace = workspace_root.resolve()
    media = _workspace_directory(workspace, media_root)
    verify_dfine_deployment(candidate=candidate, workspace_root=workspace)
    manifest_path = case_manifest_path.resolve()
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise EvaluationReportError(
            "SHADOW_CASE_MANIFEST_INVALID", "shadow case manifest dosyası bulunamadı"
        )
    if load_shadow_case_manifest(manifest_path) != case_manifest:
        raise EvaluationReportError(
            "SHADOW_CASE_MANIFEST_MISMATCH",
            "shadow case manifest dosyası istek içeriği ile eşleşmiyor",
        )
    entries = {entry.source_ref: entry for entry in test_dataset_manifest.entries}
    for case in case_manifest.cases:
        entry = entries.get(case.video_ref)
        if (
            entry is None
            or entry.file_sha256 != case.video_sha256
            or entry.split in {DatasetSplit.TRAIN, DatasetSplit.VALIDATION}
            or not (
                DatasetUse.EVALUATION in entry.allowed_uses
                or DatasetUse.BENCHMARK in entry.allowed_uses
            )
        ):
            raise EvaluationReportError(
                "SHADOW_CASE_DATASET_MISMATCH",
                f"shadow video test dataset provenance ile eşleşmiyor: {case.video_ref}",
            )
        video = media.joinpath(*case.video_ref.split("/")).resolve()
        if not video.is_relative_to(media) or not video.is_file() or video.is_symlink():
            raise EvaluationReportError(
                "SHADOW_VIDEO_MISSING", f"shadow video bulunamadı: {case.video_ref}"
            )
        if sha256_file(video) != case.video_sha256:
            raise EvaluationReportError(
                "SHADOW_VIDEO_CHANGED", f"shadow video SHA-256 değişti: {case.video_ref}"
            )

    deployment = candidate.deployment
    assert deployment is not None
    created_at = now or datetime.now(UTC)
    plan_id = f"dfine-shadow-{uuid4().hex}"
    payload: dict[str, Any] = {
        "plan_version": "1.0.0",
        "plan_id": plan_id,
        "model_version_id": candidate.model_version_id,
        "candidate_checkpoint_sha256": candidate.checkpoint_sha256,
        "deployment_fingerprint": deployment.artifact_fingerprint,
        "onnx_ref": deployment.onnx_ref,
        "onnx_sha256": deployment.onnx_sha256,
        "test_dataset_fingerprint": test_dataset_manifest.dataset_fingerprint,
        "code_revision": code_revision,
        "media_root_ref": media.relative_to(workspace).as_posix(),
        "case_manifest_sha256": sha256_file(manifest_path),
        "cases": case_manifest.cases,
        "evaluation_run_ids": [f"{plan_id}-r{index}" for index in range(1, repetitions + 1)],
        "created_by": created_by,
        "created_at": created_at,
    }
    draft = ShadowEvaluationPlan.model_construct(**payload, plan_fingerprint="0" * 64)
    normalized = draft.model_dump(mode="json", exclude={"plan_fingerprint"})
    return ShadowEvaluationPlan.model_validate(
        {**normalized, "plan_fingerprint": _payload_sha256(normalized)}
    )


async def execute_shadow_evaluation(
    *,
    plan: ShadowEvaluationPlan,
    candidate: ModelVersion,
    workspace_root: Path,
    runs_root: Path,
    max_minutes: int = 180,
    active_analysis_probe: Callable[[], bool] = lambda: False,
    execution_coordinator: ExecutionCoordinator | None = None,
    run_video_callable: Callable[..., Awaitable[None]] = run_video,
    duration_probe: Callable[[Path], Awaitable[float]] = ingest.probe_duration,
    peak_ram_probe: Callable[[], float] = lambda: _process_peak_ram_mb(),
    revision_probe: Callable[[Path], str] = inspect_project_revision,
) -> list[Path]:
    """Execute every case three times without activating the candidate."""

    if not 1 <= max_minutes <= 1440:
        raise EvaluationReportError(
            "SHADOW_BUDGET_INVALID", "shadow değerlendirme dakika bütçesi geçersiz"
        )
    _validate_candidate_against_plan(candidate, plan)
    workspace = workspace_root.resolve()
    if revision_probe(workspace) != plan.code_revision:
        raise EvaluationReportError(
            "PROJECT_REVISION_MISMATCH", "proje kodu shadow plan commit'i ile eşleşmiyor"
        )
    onnx = verify_dfine_deployment(candidate=candidate, workspace_root=workspace)
    media = workspace.joinpath(*plan.media_root_ref.split("/")).resolve()
    runs = _workspace_output_directory(workspace, runs_root)
    for case in plan.cases:
        video = media.joinpath(*case.video_ref.split("/")).resolve()
        if (
            not video.is_relative_to(media)
            or not video.is_file()
            or video.is_symlink()
            or sha256_file(video) != case.video_sha256
        ):
            raise EvaluationReportError(
                "SHADOW_VIDEO_CHANGED", f"shadow video değişti: {case.video_ref}"
            )
    exclusive_lease = None
    if execution_coordinator is not None:
        try:
            exclusive_lease = execution_coordinator.acquire_exclusive(
                ExclusiveWorkload.SHADOW
            )
        except LiveWorkloadActive as exc:
            raise EvaluationReportError("LIVE_ANALYSIS_ACTIVE", str(exc)) from exc
        except ExclusiveWorkloadActive as exc:
            raise EvaluationReportError("EXCLUSIVE_WORKLOAD_ACTIVE", str(exc)) from exc

    def stop_requested() -> bool:
        return active_analysis_probe() or bool(
            exclusive_lease is not None and exclusive_lease.stop_requested()
        )

    if stop_requested():
        if exclusive_lease is not None:
            exclusive_lease.release()
        raise EvaluationReportError(
            "LIVE_ANALYSIS_ACTIVE", "canlı analiz varken shadow değerlendirme başlatılamaz"
        )

    previous = (
        settings.media_dir,
        settings.runs_dir,
        settings.dfine_onnx,
        settings.detector_enabled,
    )
    settings.media_dir = media
    settings.runs_dir = runs
    settings.dfine_onnx = str(onnx)
    settings.detector_enabled = True
    perception.set_detector_override(onnx)
    deadline = time.monotonic() + max_minutes * 60
    outputs: list[Path] = []
    try:
        for evaluation_run_id in plan.evaluation_run_ids:
            records: list[ShadowEvaluationRecord] = []
            for case in plan.cases:
                if stop_requested():
                    raise EvaluationReportError(
                        "LIVE_ANALYSIS_ACTIVE", "canlı analiz shadow worker'ı durdurdu"
                    )
                if time.monotonic() >= deadline:
                    raise EvaluationReportError(
                        "SHADOW_TIME_BUDGET_EXCEEDED", "shadow süre bütçesi doldu"
                    )
                canonical_run_id = f"{evaluation_run_id}-{case.case_id}"
                video = media.joinpath(*case.video_ref.split("/")).resolve()
                duration = await duration_probe(video)
                await _run_with_guards(
                    run_video_callable,
                    video_ref=case.video_ref,
                    run_id=canonical_run_id,
                    stop_probe=stop_requested,
                    deadline=deadline,
                )
                canonical_artifact = runs / f"{canonical_run_id}.jsonl"
                summary = summarize_canonical_shadow_run(canonical_artifact)
                records.append(
                    ShadowEvaluationRecord(
                        evaluation_run_id=evaluation_run_id,
                        candidate_checkpoint_sha256=plan.candidate_checkpoint_sha256,
                        test_dataset_fingerprint=plan.test_dataset_fingerprint,
                        code_revision=plan.code_revision,
                        shadow_mode=True,
                        measured_at=datetime.now(UTC),
                        expected_critical=case.expected_critical,
                        confirmed_critical=summary["confirmed_critical"],
                        is_normal=case.is_normal,
                        duration_seconds=duration,
                        false_alarm=case.is_normal and summary["has_incident"],
                        latency_ms=summary["dfine_latency_ms"],
                        ram_mb=max(0.001, peak_ram_probe()),
                        vram_mb=0,
                        case_id=case.case_id,
                        source_video_sha256=case.video_sha256,
                        deployment_fingerprint=plan.deployment_fingerprint,
                        onnx_sha256=plan.onnx_sha256,
                        canonical_run_id=canonical_run_id,
                        canonical_artifact_sha256=sha256_file(canonical_artifact),
                        resource_scope="local CPU shadow worker; remote VLM VRAM excluded",
                    )
                )
            output = runs / "dfine-evaluations" / plan.plan_id / f"{evaluation_run_id}.jsonl"
            _atomic_write_records(output, records)
            outputs.append(output)
    finally:
        settings.media_dir, settings.runs_dir, settings.dfine_onnx, settings.detector_enabled = (
            previous
        )
        perception.set_detector_override(None)
        perception.reset_detector_cache()
        if exclusive_lease is not None:
            exclusive_lease.release()
    return outputs


def summarize_canonical_shadow_run(path: Path) -> dict[str, Any]:
    """Read final incident states and canonical runtime metrics from one JSONL."""

    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
        raise EvaluationReportError(
            "SHADOW_CANONICAL_ARTIFACT_INVALID", f"canonical run artifact geçersiz: {path}"
        )
    incidents: dict[str, dict[str, Any]] = {}
    terminal_state: str | None = None
    metrics: dict[str, Any] | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationReportError(
            "SHADOW_CANONICAL_ARTIFACT_INVALID", f"canonical run okunamadı: {exc}"
        ) from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
            payload = envelope["payload"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise EvaluationReportError(
                "SHADOW_CANONICAL_ARTIFACT_INVALID",
                f"canonical run satırı geçersiz: {path}:{line_number}: {exc}",
            ) from exc
        if payload.get("type") == "incident_update":
            incidents[str(payload["incident_id"])] = payload
        elif payload.get("type") == "run_status":
            terminal_state = payload.get("state")
        elif payload.get("type") == "run_metrics":
            metrics = payload
    if terminal_state != "done" or metrics is None or metrics.get("terminal_status") != "completed":
        raise EvaluationReportError(
            "SHADOW_CANONICAL_RUN_FAILED", f"canonical shadow run tamamlanmadı: {path}"
        )
    dfine_calls = metrics.get("dfine_calls")
    dfine_total_ms = metrics.get("dfine_total_ms")
    if (
        not isinstance(dfine_calls, int)
        or isinstance(dfine_calls, bool)
        or dfine_calls <= 0
        or not isinstance(dfine_total_ms, (int, float))
        or isinstance(dfine_total_ms, bool)
        or dfine_total_ms <= 0
    ):
        raise EvaluationReportError(
            "SHADOW_DFINE_METRIC_INVALID",
            f"canonical D-FINE gecikme metriği geçersiz: {path}",
        )
    return {
        "confirmed_critical": any(item.get("risk") == "kritik" for item in incidents.values()),
        "has_incident": bool(incidents),
        "dfine_latency_ms": max(0.001, float(dfine_total_ms) / dfine_calls),
    }


async def _run_with_guards(
    runner: Callable[..., Awaitable[None]],
    *,
    video_ref: str,
    run_id: str,
    stop_probe: Callable[[], bool],
    deadline: float,
) -> None:
    def guarded_stop_probe() -> bool:
        return stop_probe() or time.monotonic() >= deadline

    task = asyncio.create_task(
        runner(_NullManager(), video_ref, run_id, stop_probe=guarded_stop_probe)
    )
    stop_reason: str | None = None
    try:
        while not task.done():
            if time.monotonic() >= deadline:
                stop_reason = "SHADOW_TIME_BUDGET_EXCEEDED"
            elif stop_probe():
                stop_reason = "LIVE_ANALYSIS_PREEMPTED"
            await asyncio.wait({task}, timeout=0.25)
        try:
            await task
        except PipelineStopRequested:
            stop_reason = stop_reason or (
                "SHADOW_TIME_BUDGET_EXCEEDED"
                if time.monotonic() >= deadline
                else "LIVE_ANALYSIS_PREEMPTED"
            )
        if stop_reason == "SHADOW_TIME_BUDGET_EXCEEDED":
            raise EvaluationReportError(
                stop_reason, "shadow süre bütçesi doldu"
            )
        if stop_reason == "LIVE_ANALYSIS_PREEMPTED":
            raise EvaluationReportError(
                stop_reason, "canlı analiz shadow worker'ını güvenli biçimde durdurdu"
            )
    except asyncio.CancelledError:
        # Sert asyncio iptali shield/to_thread işini öldürmez. Global ayarlar ancak
        # canonical runner bütün alt işlerini boşalttıktan sonra geri yüklenebilir.
        await asyncio.shield(task)
        raise
    finally:
        if not task.done():
            await asyncio.shield(task)


class _NullManager:
    async def broadcast(self, _event: object) -> None:
        return None


def _process_peak_ram_mb() -> float:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            raise EvaluationReportError(
                "SHADOW_MEMORY_PROBE_FAILED", "Windows peak RAM ölçümü başarısız"
            )
        return counters.PeakWorkingSetSize / (1024 * 1024)
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def write_shadow_plan(path: Path, plan: ShadowEvaluationPlan) -> Path:
    _atomic_write_text(path, plan.model_dump_json(indent=2) + "\n")
    return path.resolve()


def load_shadow_plan(path: Path) -> ShadowEvaluationPlan:
    try:
        return ShadowEvaluationPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationReportError("SHADOW_PLAN_INVALID", f"shadow plan okunamadı: {exc}") from exc


def _validate_candidate_against_plan(candidate: ModelVersion, plan: ShadowEvaluationPlan) -> None:
    deployment = candidate.deployment
    if (
        candidate.stage != ModelStage.CANDIDATE
        or candidate.evaluation is not None
        or deployment is None
        or candidate.model_version_id != plan.model_version_id
        or candidate.checkpoint_sha256 != plan.candidate_checkpoint_sha256
        or deployment.artifact_fingerprint != plan.deployment_fingerprint
        or deployment.onnx_ref != plan.onnx_ref
        or deployment.onnx_sha256 != plan.onnx_sha256
    ):
        raise EvaluationReportError(
            "SHADOW_CANDIDATE_MISMATCH", "candidate shadow plan provenance ile eşleşmiyor"
        )


def _workspace_directory(workspace: Path, path: Path) -> Path:
    if path.is_symlink():
        raise EvaluationReportError("UNSAFE_SHADOW_PATH", f"symlink reddedildi: {path}")
    resolved = path.resolve()
    if not resolved.is_relative_to(workspace) or not resolved.is_dir():
        raise EvaluationReportError("SHADOW_DIRECTORY_MISSING", f"shadow dizini bulunamadı: {path}")
    return resolved


def _workspace_output_directory(workspace: Path, path: Path) -> Path:
    if path.is_symlink():
        raise EvaluationReportError("UNSAFE_SHADOW_PATH", f"symlink reddedildi: {path}")
    resolved = path.resolve()
    if not resolved.is_relative_to(workspace):
        raise EvaluationReportError(
            "UNSAFE_SHADOW_PATH", f"shadow çıktı yolu workspace dışında: {path}"
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _safe_reference(value: str) -> bool:
    posix = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ".." not in posix.parts
        and value == posix.as_posix()
    )


def _payload_sha256(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _atomic_write_records(path: Path, records: list[ShadowEvaluationRecord]) -> None:
    content = "".join(record.model_dump_json() + "\n" for record in records)
    _atomic_write_text(path, content)


def _atomic_write_text(path: Path, content: str) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".tmp-{uuid4().hex[:8]}"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)


__all__ = [
    "ShadowCase",
    "ShadowCaseManifest",
    "ShadowEvaluationPlan",
    "execute_shadow_evaluation",
    "load_shadow_case_manifest",
    "load_shadow_plan",
    "prepare_shadow_evaluation",
    "summarize_canonical_shadow_run",
    "write_shadow_plan",
]
