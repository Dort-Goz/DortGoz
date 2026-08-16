"""Export one candidate checkpoint to a verified production D-FINE ONNX."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..domain.model_lifecycle import (
    DfineDeploymentArtifact,
    ModelStage,
    ModelVersion,
    TrainingJob,
)
from ..repositories.protocols import EventRepository
from .dataset_manifest import sha256_file
from .dfine_training import (
    DfineTrainingError,
    LocalProcessRunner,
    ProcessOutcome,
    ProcessRunner,
    inspect_dfine_repository,
)
from .evaluation_report import EvaluationReportError


@dataclass(frozen=True)
class DfineOnnxContract:
    input_names: list[str]
    output_names: list[str]


def build_dfine_export_command(
    *,
    python_executable: Path,
    dfine_repository: Path,
    config_path: Path,
    checkpoint_copy: Path,
    category_count: int,
) -> list[str]:
    """Return the official deployment exporter adapter argv without a shell."""

    exporter = dfine_repository / "tools" / "deployment" / "export_onnx.py"
    if not exporter.is_file() or exporter.is_symlink():
        raise EvaluationReportError(
            "DFINE_EXPORTER_MISSING", "D-FINE tools/deployment/export_onnx.py bulunamadı"
        )
    adapter = Path(__file__).resolve().parents[3] / "scripts" / "dfine_export_adapter.py"
    if not adapter.is_file() or adapter.is_symlink():
        raise EvaluationReportError(
            "DFINE_EXPORT_ADAPTER_MISSING", "Dörtgöz D-FINE export adapter'ı bulunamadı"
        )
    if category_count <= 0:
        raise EvaluationReportError(
            "DFINE_CATEGORY_INVALID", "deployment en az bir kategori gerektirir"
        )
    python = python_executable.resolve()
    if not python.is_file() or python.is_symlink():
        raise EvaluationReportError(
            "PYTHON_EXECUTABLE_INVALID", "Python çalıştırıcısı bulunamadı veya güvensiz"
        )
    return [
        str(python),
        str(adapter),
        "--official-exporter",
        str(exporter),
        "-c",
        str(config_path),
        "-r",
        str(checkpoint_copy),
        "--num-classes",
        str(category_count),
    ]


def inspect_deployed_dfine_onnx(path: Path, *, category_count: int) -> DfineOnnxContract:
    """Load the deployed graph and run one contract smoke inference on CPU."""

    import numpy as np
    import onnxruntime as ort

    if category_count <= 0:
        raise EvaluationReportError(
            "DFINE_CATEGORY_INVALID", "deployment en az bir kategori gerektirir"
        )
    model = path.resolve()
    if not model.is_file() or model.is_symlink():
        raise EvaluationReportError(
            "DFINE_ONNX_MISSING", "D-FINE exporter production ONNX üretmedi"
        )
    try:
        session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    except Exception as exc:
        raise EvaluationReportError(
            "DFINE_ONNX_INVALID", f"ONNX Runtime modeli açamadı: {exc}"
        ) from exc
    input_names = [item.name for item in session.get_inputs()]
    output_names = [item.name for item in session.get_outputs()]
    if input_names != ["images", "orig_target_sizes"]:
        raise EvaluationReportError(
            "DFINE_ONNX_INPUT_CONTRACT_INVALID",
            f"beklenmeyen ONNX girdileri: {input_names}",
        )
    if output_names != ["labels", "boxes", "scores"]:
        raise EvaluationReportError(
            "DFINE_ONNX_OUTPUT_CONTRACT_INVALID",
            f"beklenmeyen ONNX çıktıları: {output_names}",
        )
    try:
        labels, boxes, scores = session.run(
            output_names,
            {
                "images": np.zeros((1, 3, 640, 640), dtype=np.float32),
                "orig_target_sizes": np.asarray([[640, 640]], dtype=np.int64),
            },
        )
    except Exception as exc:
        raise EvaluationReportError(
            "DFINE_ONNX_SMOKE_FAILED", f"ONNX smoke inference başarısız: {exc}"
        ) from exc
    if (
        labels.ndim != 2
        or scores.shape != labels.shape
        or boxes.ndim != 3
        or boxes.shape[:2] != labels.shape
        or boxes.shape[2] != 4
        or not np.isfinite(scores).all()
        or not np.isfinite(boxes).all()
        or np.any(scores < 0)
        or np.any(scores > 1)
        or np.any(labels < 0)
        or np.any(labels >= category_count)
    ):
        raise EvaluationReportError(
            "DFINE_ONNX_SMOKE_INVALID", "ONNX smoke çıktısı D-FINE sözleşmesine uymuyor"
        )
    return DfineOnnxContract(input_names=input_names, output_names=output_names)


def execute_dfine_onnx_export(
    *,
    repository: EventRepository,
    candidate: ModelVersion,
    training_job: TrainingJob,
    workspace_root: Path,
    dfine_repository: Path,
    python_executable: Path,
    runs_root: Path,
    registry_root: Path,
    max_minutes: int = 30,
    active_analysis_probe: Callable[[], bool] = lambda: False,
    process_runner: ProcessRunner | None = None,
    onnx_inspector: Callable[..., DfineOnnxContract] = inspect_deployed_dfine_onnx,
    now: datetime | None = None,
) -> tuple[ModelVersion, ProcessOutcome, Path]:
    """Export, smoke-test, fingerprint, and attach a deployment to a candidate."""

    if candidate.stage != ModelStage.CANDIDATE or candidate.evaluation is not None:
        raise EvaluationReportError(
            "MODEL_NOT_DEPLOYABLE",
            "yalnız değerlendirilmemiş candidate model ONNX olarak aktarılabilir",
        )
    if candidate.deployment is not None:
        raise EvaluationReportError(
            "MODEL_ALREADY_DEPLOYED", "candidate zaten production ONNX kaydı taşıyor"
        )
    if (
        training_job.job_id != candidate.training_job_id
        or training_job.checkpoint_sha256 != candidate.checkpoint_sha256
        or training_job.checkpoint_ref != candidate.checkpoint_ref
    ):
        raise EvaluationReportError(
            "TRAINING_JOB_MISMATCH", "candidate training job provenance ile eşleşmiyor"
        )
    if not 1 <= max_minutes <= 1440:
        raise EvaluationReportError(
            "DEPLOYMENT_BUDGET_INVALID", "ONNX export dakika bütçesi geçersiz"
        )
    if active_analysis_probe():
        raise EvaluationReportError(
            "LIVE_ANALYSIS_ACTIVE", "canlı analiz varken ONNX export başlatılamaz"
        )

    workspace = workspace_root.resolve()
    checkpoint = _workspace_file(workspace, candidate.checkpoint_ref)
    if sha256_file(checkpoint) != candidate.checkpoint_sha256:
        raise EvaluationReportError(
            "CANDIDATE_CHECKPOINT_CHANGED", "candidate checkpoint SHA-256 değeri değişti"
        )
    try:
        repo_info = inspect_dfine_repository(dfine_repository, candidate.architecture)
    except DfineTrainingError as exc:
        raise EvaluationReportError(exc.code, str(exc)) from exc
    if repo_info.revision != candidate.dfine_repository_revision:
        raise EvaluationReportError(
            "DFINE_REVISION_MISMATCH", "D-FINE export commit'i eğitim commit'i ile eşleşmiyor"
        )

    runs = _workspace_directory(workspace, runs_root, allow_missing=True)
    registry = _workspace_directory(workspace, registry_root, allow_missing=True)
    run_dir = runs / "dfine-deployments" / f"{uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=False)
    checkpoint_copy = run_dir / "candidate.pth"
    shutil.copyfile(checkpoint, checkpoint_copy)
    if sha256_file(checkpoint_copy) != candidate.checkpoint_sha256:
        raise EvaluationReportError(
            "CHECKPOINT_COPY_INVALID", "export checkpoint kopyası kaynakla eşleşmiyor"
        )
    output_onnx = run_dir / "candidate.onnx"
    log_path = run_dir / "export.log"
    command = build_dfine_export_command(
        python_executable=python_executable,
        dfine_repository=repo_info.root,
        config_path=repo_info.config_path,
        checkpoint_copy=checkpoint_copy,
        category_count=len(training_job.category_names),
    )
    outcome = (process_runner or LocalProcessRunner()).run(
        command,
        cwd=repo_info.root,
        env={**os.environ, "PYTHONHASHSEED": "0"},
        log_path=log_path,
        timeout_seconds=max_minutes * 60,
        stop_probe=active_analysis_probe,
    )
    if outcome.stop_code is not None:
        raise EvaluationReportError(
            outcome.stop_code,
            "D-FINE ONNX export canlı analiz veya süre bütçesi nedeniyle durduruldu",
        )
    if outcome.exit_code != 0:
        raise EvaluationReportError(
            "DFINE_ONNX_EXPORT_FAILED", f"D-FINE ONNX export başarısız; log: {log_path}"
        )
    if not output_onnx.is_file() or output_onnx.is_symlink():
        raise EvaluationReportError("DFINE_ONNX_MISSING", "D-FINE exporter candidate.onnx üretmedi")
    contract = onnx_inspector(output_onnx, category_count=len(training_job.category_names))
    onnx_sha = sha256_file(output_onnx)
    model_key = hashlib.sha256(candidate.model_version_id.encode("utf-8")).hexdigest()[:16]
    target_dir = registry / model_key / onnx_sha[:16]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "model.onnx"
    if target.exists() and (
        target.is_symlink() or not target.is_file() or sha256_file(target) != onnx_sha
    ):
        raise EvaluationReportError(
            "DFINE_ONNX_TARGET_CONFLICT", "deployment hedefinde farklı ONNX bulunuyor"
        )
    if not target.exists():
        temporary = target_dir / ".model.onnx.tmp"
        shutil.copyfile(output_onnx, temporary)
        if sha256_file(temporary) != onnx_sha:
            raise EvaluationReportError(
                "DFINE_ONNX_COPY_INVALID", "deployment ONNX kopyası kaynakla eşleşmiyor"
            )
        temporary.replace(target)

    exported_at = now or datetime.now(UTC)
    artifact_payload = {
        "artifact_version": "1.0.0",
        "onnx_ref": target.relative_to(workspace).as_posix(),
        "onnx_sha256": onnx_sha,
        "source_checkpoint_sha256": candidate.checkpoint_sha256,
        "dfine_repository_revision": repo_info.revision,
        "input_names": contract.input_names,
        "output_names": contract.output_names,
        "input_size": 640,
        "category_names": training_job.category_names,
        "source_log_sha256": sha256_file(log_path),
        "exported_at": exported_at,
    }
    artifact_draft = DfineDeploymentArtifact.model_construct(
        **artifact_payload,
        artifact_fingerprint="0" * 64,
    )
    normalized_payload = artifact_draft.model_dump(mode="json", exclude={"artifact_fingerprint"})
    artifact = DfineDeploymentArtifact.model_validate(
        {
            **normalized_payload,
            "artifact_fingerprint": _payload_sha256(normalized_payload),
        }
    )
    _write_runtime_config(target_dir / "config.json", artifact)
    updated = ModelVersion.model_validate(
        {
            **candidate.model_dump(),
            "deployment": artifact,
            "updated_at": exported_at,
            "revision": candidate.revision + 1,
        }
    )
    saved = repository.update_model_version(updated)
    return saved, outcome, log_path


def verify_dfine_deployment(*, candidate: ModelVersion, workspace_root: Path) -> Path:
    deployment = candidate.deployment
    if deployment is None:
        raise EvaluationReportError(
            "MODEL_DEPLOYMENT_MISSING", "candidate production ONNX kaydı taşımıyor"
        )
    workspace = workspace_root.resolve()
    onnx = _workspace_file(workspace, deployment.onnx_ref)
    if sha256_file(onnx) != deployment.onnx_sha256:
        raise EvaluationReportError(
            "MODEL_ONNX_CHANGED", "candidate production ONNX SHA-256 değeri değişti"
        )
    config = onnx.parent / "config.json"
    if not config.is_file() or config.is_symlink():
        raise EvaluationReportError(
            "MODEL_ONNX_CONFIG_MISSING", "candidate production ONNX config.json dosyası yok"
        )
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationReportError(
            "MODEL_ONNX_CONFIG_INVALID", f"candidate ONNX config okunamadı: {exc}"
        ) from exc
    expected_labels = {str(index): name for index, name in enumerate(deployment.category_names)}
    if (
        payload.get("id2label") != expected_labels
        or payload.get("interest_labels") != deployment.category_names
        or payload.get("onnx_sha256") != deployment.onnx_sha256
        or payload.get("deployment_fingerprint") != deployment.artifact_fingerprint
    ):
        raise EvaluationReportError(
            "MODEL_ONNX_CONFIG_CHANGED", "candidate ONNX config deployment ile eşleşmiyor"
        )
    return onnx


def _write_runtime_config(path: Path, artifact: DfineDeploymentArtifact) -> None:
    payload = {
        "config_version": "1.0.0",
        "id2label": {str(index): name for index, name in enumerate(artifact.category_names)},
        "interest_labels": artifact.category_names,
        "input_contract": artifact.input_names,
        "output_contract": artifact.output_names,
        "onnx_sha256": artifact.onnx_sha256,
        "deployment_fingerprint": artifact.artifact_fingerprint,
    }
    target = path.resolve()
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _workspace_file(workspace: Path, reference: str) -> Path:
    path = workspace.joinpath(*reference.split("/")).resolve()
    if not path.is_relative_to(workspace) or not path.is_file() or path.is_symlink():
        raise EvaluationReportError(
            "DEPLOYMENT_FILE_MISSING", f"deployment dosyası bulunamadı: {reference}"
        )
    return path


def _workspace_directory(workspace: Path, path: Path, *, allow_missing: bool) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(workspace) or path.is_symlink():
        raise EvaluationReportError(
            "UNSAFE_DEPLOYMENT_PATH", f"deployment yolu workspace dışında: {path}"
        )
    if not allow_missing and not resolved.is_dir():
        raise EvaluationReportError(
            "DEPLOYMENT_DIRECTORY_MISSING", f"deployment dizini bulunamadı: {path}"
        )
    return resolved


def _payload_sha256(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "DfineOnnxContract",
    "build_dfine_export_command",
    "execute_dfine_onnx_export",
    "inspect_deployed_dfine_onnx",
    "verify_dfine_deployment",
]
