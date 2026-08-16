"""Prepare and normalize reproducible D-FINE detector evaluations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain.dataset import DatasetUse, OfflineDatasetManifest
from ..domain.model_lifecycle import DfineArchitecture, ModelStage, ModelVersion
from .dataset_manifest import sha256_file
from .dfine_training import (
    DfineTrainingError,
    LocalProcessRunner,
    ProcessOutcome,
    ProcessRunner,
    inspect_dfine_repository,
)
from .evaluation_report import DetectorEvaluationArtifact, EvaluationReportError

_MAX_COCO_BYTES = 64 * 1024 * 1024
_MAX_LOG_BYTES = 64 * 1024 * 1024
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_COCO_METRIC_PATTERN = re.compile(
    r"Average Precision\s+\(AP\)\s+@\[\s*IoU="
    r"(?P<iou>0\.50(?::0\.95)?)\s*\|\s*area=\s*all\s*\|"
    r"\s*maxDets=\s*100\s*\]\s*=\s*(?P<value>-?\d+(?:\.\d+)?)"
)


class CocoEvaluationInventory(BaseModel):
    """Immutable identity of the COCO test annotations and their local frames."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    annotations_ref: str = Field(min_length=1)
    annotations_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_root_ref: str = Field(min_length=1)
    frame_inventory_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_count: int = Field(gt=0)
    annotation_count: int = Field(gt=0)
    category_names: list[str] = Field(min_length=1)
    source_video_sha256s: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def references_are_safe(self) -> CocoEvaluationInventory:
        for value in (self.annotations_ref, self.frame_root_ref):
            if not _safe_reference(value):
                raise ValueError("COCO değerlendirme yolu güvenli göreli POSIX yol olmalıdır")
        if len(self.category_names) != len(set(self.category_names)):
            raise ValueError("COCO kategori adları benzersiz olmalıdır")
        if len(self.source_video_sha256s) != len(set(self.source_video_sha256s)):
            raise ValueError("COCO kaynak video SHA-256 değerleri benzersiz olmalıdır")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in self.source_video_sha256s):
            raise ValueError("COCO kaynak video SHA-256 değeri geçersiz")
        return self


class DfineDetectorEvaluationPlan(BaseModel):
    """Hash-bound inputs for one official D-FINE test-only run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_version: Literal["1.0.0"] = "1.0.0"
    plan_id: str = Field(min_length=1)
    model_version_id: str = Field(min_length=1)
    architecture: DfineArchitecture
    candidate_checkpoint_ref: str = Field(min_length=1)
    candidate_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    dfine_repository_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    dfine_config_ref: str = Field(min_length=1)
    coco: CocoEvaluationInventory
    shadow_run_ids: list[str] = Field(min_length=3)
    created_by: str = Field(min_length=1, max_length=120)
    created_at: datetime
    plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def plan_is_consistent(self) -> DfineDetectorEvaluationPlan:
        if self.created_at.utcoffset() is None:
            raise ValueError("evaluation plan created_at saat dilimi içermelidir")
        if not _safe_reference(self.candidate_checkpoint_ref):
            raise ValueError("candidate checkpoint yolu güvenli göreli POSIX yol olmalıdır")
        if not _safe_reference(self.dfine_config_ref):
            raise ValueError("D-FINE config yolu güvenli göreli POSIX yol olmalıdır")
        if len(self.shadow_run_ids) != len(set(self.shadow_run_ids)):
            raise ValueError("shadow run kimlikleri benzersiz olmalıdır")
        expected = _payload_sha256(self.model_dump(mode="json", exclude={"plan_fingerprint"}))
        if self.plan_fingerprint != expected:
            raise ValueError("evaluation plan fingerprint içerikle eşleşmiyor")
        return self


def inspect_project_revision(workspace_root: Path) -> str:
    """Return a clean, committed project revision."""

    root = workspace_root.resolve()
    revision = _git_output(root, "rev-parse", "HEAD")
    if not _REVISION_PATTERN.fullmatch(revision):
        raise EvaluationReportError("PROJECT_REVISION_INVALID", "proje commit SHA değeri geçersiz")
    if _git_output(root, "status", "--porcelain", "--untracked-files=all"):
        raise EvaluationReportError(
            "PROJECT_REPOSITORY_DIRTY",
            "değerlendirme planı yalnız temiz ve commit edilmiş koddan üretilebilir",
        )
    return revision


def prepare_dfine_detector_evaluation(
    *,
    candidate: ModelVersion,
    test_dataset_manifest: OfflineDatasetManifest,
    workspace_root: Path,
    dfine_repository: Path,
    coco_annotations: Path,
    frame_root: Path,
    code_revision: str,
    created_by: str,
    expected_category_names: list[str],
    shadow_repetitions: int = 3,
    now: datetime | None = None,
) -> DfineDetectorEvaluationPlan:
    """Validate all local inputs and freeze their identities in one plan."""

    if candidate.stage != ModelStage.CANDIDATE or candidate.evaluation is not None:
        raise EvaluationReportError(
            "MODEL_NOT_EVALUATABLE",
            "yalnız değerlendirilmemiş candidate model için plan üretilebilir",
        )
    if not (
        DatasetUse.EVALUATION in test_dataset_manifest.allowed_uses
        or DatasetUse.BENCHMARK in test_dataset_manifest.allowed_uses
    ):
        raise EvaluationReportError(
            "TEST_DATASET_USE_REJECTED",
            "test dataset evaluation veya benchmark kullanımına açık değil",
        )
    if not _REVISION_PATTERN.fullmatch(code_revision):
        raise EvaluationReportError("PROJECT_REVISION_INVALID", "proje commit SHA değeri geçersiz")
    if shadow_repetitions < 3:
        raise EvaluationReportError(
            "EVALUATION_REPETITIONS_MISSING", "en az üç shadow tekrar planlanmalıdır"
        )

    workspace = workspace_root.resolve()
    checkpoint = _resolve_workspace_file(
        workspace, candidate.checkpoint_ref, code="CANDIDATE_CHECKPOINT"
    )
    if sha256_file(checkpoint) != candidate.checkpoint_sha256:
        raise EvaluationReportError(
            "CANDIDATE_CHECKPOINT_CHANGED", "candidate checkpoint SHA-256 değeri değişti"
        )

    try:
        repository_info = inspect_dfine_repository(dfine_repository, candidate.architecture)
    except DfineTrainingError as exc:
        raise EvaluationReportError(exc.code, str(exc)) from exc
    if repository_info.revision != candidate.dfine_repository_revision:
        raise EvaluationReportError(
            "DFINE_REVISION_MISMATCH",
            "değerlendirme D-FINE commit'i eğitim commit'i ile eşleşmiyor",
        )

    inventory = inspect_coco_evaluation_inventory(
        workspace_root=workspace,
        coco_annotations=coco_annotations,
        frame_root=frame_root,
        expected_dataset_fingerprint=test_dataset_manifest.dataset_fingerprint,
        allowed_source_sha256s={
            entry.file_sha256
            for entry in test_dataset_manifest.entries
            if DatasetUse.EVALUATION in entry.allowed_uses
            or DatasetUse.BENCHMARK in entry.allowed_uses
        },
    )
    if inventory.category_names != expected_category_names:
        raise EvaluationReportError(
            "COCO_CATEGORY_MISMATCH",
            "COCO test kategorileri candidate eğitim kategorileri ile eşleşmiyor",
        )
    created_at = now or datetime.now(UTC)
    plan_id = f"dfine-eval-{uuid4().hex}"
    payload: dict[str, Any] = {
        "plan_version": "1.0.0",
        "plan_id": plan_id,
        "model_version_id": candidate.model_version_id,
        "architecture": candidate.architecture,
        "candidate_checkpoint_ref": candidate.checkpoint_ref,
        "candidate_checkpoint_sha256": candidate.checkpoint_sha256,
        "test_dataset_fingerprint": test_dataset_manifest.dataset_fingerprint,
        "code_revision": code_revision,
        "dfine_repository_revision": repository_info.revision,
        "dfine_config_ref": repository_info.config_path.relative_to(
            repository_info.root
        ).as_posix(),
        "coco": inventory,
        "shadow_run_ids": [
            f"{plan_id}-shadow-{index}" for index in range(1, shadow_repetitions + 1)
        ],
        "created_by": created_by,
        "created_at": created_at,
    }
    draft = DfineDetectorEvaluationPlan.model_construct(**payload, plan_fingerprint="0" * 64)
    normalized = draft.model_dump(mode="json", exclude={"plan_fingerprint"})
    return DfineDetectorEvaluationPlan.model_validate(
        {**normalized, "plan_fingerprint": _payload_sha256(normalized)}
    )


def inspect_coco_evaluation_inventory(
    *,
    workspace_root: Path,
    coco_annotations: Path,
    frame_root: Path,
    expected_dataset_fingerprint: str | None = None,
    allowed_source_sha256s: set[str] | None = None,
) -> CocoEvaluationInventory:
    """Validate COCO references and hash every frame used by the test set."""

    workspace = workspace_root.resolve()
    annotations = _workspace_path(workspace, coco_annotations, must_be_file=True)
    frames = _workspace_path(workspace, frame_root, must_be_directory=True)
    if annotations.stat().st_size > _MAX_COCO_BYTES:
        raise EvaluationReportError(
            "COCO_EVALUATION_TOO_LARGE", "COCO annotation dosyası çok büyük"
        )
    try:
        payload = json.loads(annotations.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationReportError(
            "COCO_EVALUATION_INVALID", f"COCO annotation okunamadı: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise EvaluationReportError(
            "COCO_EVALUATION_INVALID", "COCO annotation JSON object olmalıdır"
        )
    info = payload.get("info")
    if expected_dataset_fingerprint is not None and (
        not isinstance(info, dict)
        or info.get("dataset_fingerprint") != expected_dataset_fingerprint
        or info.get("split") != "test"
    ):
        raise EvaluationReportError(
            "COCO_DATASET_MISMATCH",
            "COCO info alanı test dataset fingerprint ve test splitini doğrulamalıdır",
        )
    images = payload.get("images")
    annotations_list = payload.get("annotations")
    categories = payload.get("categories")
    if not all(isinstance(value, list) for value in (images, annotations_list, categories)):
        raise EvaluationReportError(
            "COCO_EVALUATION_INVALID", "COCO images, annotations ve categories listesi gerektirir"
        )
    if not images or not annotations_list or not categories:
        raise EvaluationReportError(
            "COCO_EVALUATION_EMPTY", "COCO test seti kare, kutu ve kategori içermelidir"
        )

    category_ids: set[int] = set()
    category_by_id: dict[int, str] = {}
    for category in categories:
        if not isinstance(category, dict):
            raise EvaluationReportError("COCO_EVALUATION_INVALID", "COCO kategori kaydı geçersiz")
        category_id = category.get("id")
        name = category.get("name")
        if (
            not isinstance(category_id, int)
            or category_id <= 0
            or not isinstance(name, str)
            or not name.strip()
            or category_id in category_ids
            or name in category_by_id.values()
        ):
            raise EvaluationReportError(
                "COCO_EVALUATION_INVALID", "COCO kategori kimliği veya adı geçersiz"
            )
        category_ids.add(category_id)
        category_by_id[category_id] = name
    if sorted(category_ids) != list(range(1, len(category_ids) + 1)):
        raise EvaluationReportError(
            "COCO_EVALUATION_INVALID",
            "COCO kategori kimlikleri 1 ile başlamalı ve sıralı olmalıdır",
        )
    category_names = [category_by_id[index] for index in sorted(category_by_id)]

    image_ids: set[int] = set()
    image_shapes: dict[int, tuple[int, int]] = {}
    frame_names: set[str] = set()
    source_video_hashes: set[str] = set()
    frame_records: list[dict[str, Any]] = []
    for image in images:
        if not isinstance(image, dict):
            raise EvaluationReportError("COCO_EVALUATION_INVALID", "COCO image kaydı geçersiz")
        image_id = image.get("id")
        file_name = image.get("file_name")
        width = image.get("width")
        height = image.get("height")
        source_video_sha256 = image.get("source_video_sha256")
        source_timestamp_seconds = image.get("source_timestamp_seconds")
        if (
            not isinstance(image_id, int)
            or image_id <= 0
            or image_id in image_ids
            or not isinstance(file_name, str)
            or not _safe_reference(file_name)
            or not isinstance(width, int)
            or width <= 0
            or not isinstance(height, int)
            or height <= 0
            or file_name in frame_names
            or not isinstance(source_video_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_video_sha256)
            or not isinstance(source_timestamp_seconds, (int, float))
            or isinstance(source_timestamp_seconds, bool)
            or source_timestamp_seconds < 0
        ):
            raise EvaluationReportError("COCO_EVALUATION_INVALID", "COCO image alanları geçersiz")
        if allowed_source_sha256s is not None and source_video_sha256 not in allowed_source_sha256s:
            raise EvaluationReportError(
                "COCO_SOURCE_VIDEO_MISMATCH",
                f"COCO karesi test datasetinde olmayan video SHA değeri taşıyor: {file_name}",
            )
        frame = frames.joinpath(*file_name.split("/")).resolve()
        if not frame.is_relative_to(frames) or not frame.is_file() or frame.is_symlink():
            raise EvaluationReportError(
                "COCO_FRAME_MISSING", f"COCO test karesi bulunamadı veya güvensiz: {file_name}"
            )
        image_ids.add(image_id)
        frame_names.add(file_name)
        source_video_hashes.add(source_video_sha256)
        image_shapes[image_id] = (width, height)
        frame_records.append(
            {
                "image_id": image_id,
                "file_name": file_name,
                "width": width,
                "height": height,
                "file_size_bytes": frame.stat().st_size,
                "file_sha256": sha256_file(frame),
                "source_video_sha256": source_video_sha256,
                "source_timestamp_seconds": float(source_timestamp_seconds),
            }
        )

    annotation_ids: set[int] = set()
    for annotation in annotations_list:
        if not isinstance(annotation, dict):
            raise EvaluationReportError("COCO_EVALUATION_INVALID", "COCO annotation kaydı geçersiz")
        annotation_id = annotation.get("id")
        image_id = annotation.get("image_id")
        category_id = annotation.get("category_id")
        bbox = annotation.get("bbox")
        if (
            not isinstance(annotation_id, int)
            or annotation_id <= 0
            or annotation_id in annotation_ids
            or image_id not in image_ids
            or category_id not in category_ids
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or any(not isinstance(value, (int, float)) for value in bbox)
        ):
            raise EvaluationReportError(
                "COCO_EVALUATION_INVALID", "COCO bounding box kaydı geçersiz"
            )
        x, y, width, height = (float(value) for value in bbox)
        image_width, image_height = image_shapes[image_id]
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > image_width
            or y + height > image_height
        ):
            raise EvaluationReportError(
                "COCO_EVALUATION_INVALID", "COCO bounding box kare sınırlarını aşıyor"
            )
        annotation_ids.add(annotation_id)

    annotations_ref = annotations.relative_to(workspace).as_posix()
    frame_root_ref = frames.relative_to(workspace).as_posix()
    annotation_sha = sha256_file(annotations)
    return CocoEvaluationInventory(
        annotations_ref=annotations_ref,
        annotations_sha256=annotation_sha,
        frame_root_ref=frame_root_ref,
        frame_inventory_fingerprint=_payload_sha256(
            {
                "annotations_sha256": annotation_sha,
                "frames": sorted(frame_records, key=lambda item: item["file_name"]),
            }
        ),
        image_count=len(images),
        annotation_count=len(annotations_list),
        category_names=category_names,
        source_video_sha256s=sorted(source_video_hashes),
    )


def build_dfine_test_command(
    *,
    plan: DfineDetectorEvaluationPlan,
    workspace_root: Path,
    dfine_repository: Path,
    python_executable: Path,
    output_dir: Path,
    batch_size: int = 2,
    workers: int = 2,
) -> list[str]:
    """Revalidate a plan and return a shell-free official test-only argv."""

    if not 1 <= batch_size <= 128 or not 0 <= workers <= 64:
        raise EvaluationReportError(
            "EVALUATION_RESOURCE_INVALID", "batch size veya worker sayısı geçersiz"
        )
    workspace = workspace_root.resolve()
    checkpoint = _resolve_workspace_file(
        workspace, plan.candidate_checkpoint_ref, code="CANDIDATE_CHECKPOINT"
    )
    if sha256_file(checkpoint) != plan.candidate_checkpoint_sha256:
        raise EvaluationReportError(
            "CANDIDATE_CHECKPOINT_CHANGED", "candidate checkpoint SHA-256 değeri değişti"
        )
    current_inventory = inspect_coco_evaluation_inventory(
        workspace_root=workspace,
        coco_annotations=workspace.joinpath(*plan.coco.annotations_ref.split("/")),
        frame_root=workspace.joinpath(*plan.coco.frame_root_ref.split("/")),
        expected_dataset_fingerprint=plan.test_dataset_fingerprint,
        allowed_source_sha256s=set(plan.coco.source_video_sha256s),
    )
    if current_inventory != plan.coco:
        raise EvaluationReportError(
            "COCO_EVALUATION_CHANGED", "COCO test annotation veya kareleri değişti"
        )
    try:
        repository_info = inspect_dfine_repository(dfine_repository, plan.architecture)
    except DfineTrainingError as exc:
        raise EvaluationReportError(exc.code, str(exc)) from exc
    if repository_info.revision != plan.dfine_repository_revision:
        raise EvaluationReportError(
            "DFINE_REVISION_MISMATCH", "D-FINE deposu planlanan commit'te değil"
        )
    python = python_executable.resolve()
    if not python.is_file() or python.is_symlink():
        raise EvaluationReportError(
            "PYTHON_EXECUTABLE_INVALID", "Python çalıştırıcısı bulunamadı veya güvensiz"
        )
    output = _workspace_path(workspace, output_dir, allow_missing=True)

    def quote(value: Path) -> str:
        return json.dumps(value.as_posix())

    annotations = workspace.joinpath(*plan.coco.annotations_ref.split("/"))
    frames = workspace.joinpath(*plan.coco.frame_root_ref.split("/"))
    return [
        str(python),
        str(repository_info.root / "train.py"),
        "-c",
        str(repository_info.config_path),
        "-d",
        "cuda",
        "--test-only",
        "-r",
        str(checkpoint),
        "--output-dir",
        str(output),
        "-u",
        f"num_classes={len(plan.coco.category_names)}",
        "remap_mscoco_category=False",
        f"val_dataloader.total_batch_size={batch_size}",
        f"val_dataloader.num_workers={workers}",
        f"val_dataloader.dataset.img_folder={quote(frames)}",
        f"val_dataloader.dataset.ann_file={quote(annotations)}",
    ]


def normalize_dfine_evaluation_log(
    *,
    plan: DfineDetectorEvaluationPlan,
    log_path: Path,
    output_path: Path | None = None,
    measured_at: datetime | None = None,
) -> DetectorEvaluationArtifact:
    """Convert official D-FINE/pycocotools output to the canonical artifact."""

    log = log_path.resolve()
    if not log.is_file() or log.is_symlink():
        raise EvaluationReportError(
            "DFINE_EVALUATION_LOG_MISSING", "D-FINE evaluation log dosyası bulunamadı"
        )
    if log.stat().st_size > _MAX_LOG_BYTES:
        raise EvaluationReportError(
            "DFINE_EVALUATION_LOG_TOO_LARGE", "D-FINE evaluation log dosyası çok büyük"
        )
    try:
        text = log.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvaluationReportError(
            "DFINE_EVALUATION_LOG_UNREADABLE", f"D-FINE evaluation log okunamadı: {exc}"
        ) from exc
    map_50_95, map_50 = parse_dfine_coco_metrics(text)
    artifact = DetectorEvaluationArtifact(
        candidate_checkpoint_sha256=plan.candidate_checkpoint_sha256,
        test_dataset_fingerprint=plan.test_dataset_fingerprint,
        code_revision=plan.code_revision,
        map_50_95=map_50_95,
        map_50=map_50,
        measured_at=measured_at or datetime.now(UTC),
        evaluation_plan_fingerprint=plan.plan_fingerprint,
        source_log_sha256=sha256_file(log),
    )
    if output_path is not None:
        _atomic_write_json(output_path, artifact.model_dump_json(indent=2) + "\n")
    return artifact


def execute_dfine_detector_evaluation(
    *,
    plan: DfineDetectorEvaluationPlan,
    workspace_root: Path,
    dfine_repository: Path,
    python_executable: Path,
    runs_root: Path,
    gpu_index: int = 0,
    batch_size: int = 2,
    workers: int = 2,
    max_gpu_minutes: int = 60,
    active_analysis_probe: Callable[[], bool] = lambda: False,
    process_runner: ProcessRunner | None = None,
) -> tuple[DetectorEvaluationArtifact, ProcessOutcome, Path]:
    """Run one bounded local detector evaluation and normalize its output."""

    if not 0 <= gpu_index <= 31 or not 1 <= max_gpu_minutes <= 1440:
        raise EvaluationReportError(
            "EVALUATION_RESOURCE_INVALID", "GPU index veya dakika bütçesi geçersiz"
        )
    if active_analysis_probe():
        raise EvaluationReportError(
            "LIVE_ANALYSIS_ACTIVE",
            "canlı analiz varken candidate değerlendirmesi başlatılamaz",
        )
    workspace = workspace_root.resolve()
    if inspect_project_revision(workspace) != plan.code_revision:
        raise EvaluationReportError(
            "PROJECT_REVISION_MISMATCH", "proje kodu evaluation plan commit'i ile eşleşmiyor"
        )
    run_root = _workspace_path(workspace, runs_root, allow_missing=True)
    run_dir = run_root / "dfine-evaluations" / plan.plan_id
    detector_output = run_dir / "detector"
    log_path = run_dir / "detector.log"
    report_path = run_dir / "detector-report.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    command = build_dfine_test_command(
        plan=plan,
        workspace_root=workspace,
        dfine_repository=dfine_repository,
        python_executable=python_executable,
        output_dir=detector_output,
        batch_size=batch_size,
        workers=workers,
    )
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
    env["PYTHONHASHSEED"] = "0"
    outcome = (process_runner or LocalProcessRunner()).run(
        command,
        cwd=dfine_repository.resolve(),
        env=env,
        log_path=log_path,
        timeout_seconds=max_gpu_minutes * 60,
        stop_probe=active_analysis_probe,
    )
    if outcome.stop_code is not None:
        raise EvaluationReportError(
            outcome.stop_code,
            "D-FINE değerlendirmesi canlı analiz veya GPU bütçesi nedeniyle durduruldu",
        )
    if outcome.exit_code != 0:
        raise EvaluationReportError(
            "DFINE_EVALUATION_FAILED",
            f"D-FINE değerlendirmesi başarısız oldu; log: {log_path}",
        )
    artifact = normalize_dfine_evaluation_log(
        plan=plan,
        log_path=log_path,
        output_path=report_path,
    )
    return artifact, outcome, report_path


def parse_dfine_coco_metrics(text: str) -> tuple[float, float]:
    """Read one unambiguous COCO AP/AP50 pair from official evaluation output."""

    structured_candidates: list[tuple[float, float]] = []
    for line in text.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            values = payload.get("test_coco_eval_bbox")
            if (
                isinstance(values, list)
                and len(values) >= 2
                and all(isinstance(value, (int, float)) for value in values[:2])
            ):
                structured_candidates.append((float(values[0]), float(values[1])))

    summary: dict[str, list[float]] = {"0.50:0.95": [], "0.50": []}
    for match in _COCO_METRIC_PATTERN.finditer(text):
        summary[match.group("iou")].append(float(match.group("value")))
    summary_candidates = list(zip(summary["0.50:0.95"], summary["0.50"], strict=False))
    # D-FINE JSON logs keep more decimal places than pycocotools stdout. Prefer
    # the structured values when both representations are present.
    candidates = structured_candidates or summary_candidates

    valid = {
        (round(map_all, 8), round(map_50, 8))
        for map_all, map_50 in candidates
        if 0 <= map_all <= 1 and 0 <= map_50 <= 1 and map_50 >= map_all
    }
    if not valid:
        raise EvaluationReportError(
            "DFINE_METRICS_MISSING",
            "log içinde geçerli COCO mAP50-95 ve mAP50 çifti bulunamadı",
        )
    if len(valid) != 1:
        raise EvaluationReportError(
            "DFINE_METRICS_AMBIGUOUS",
            "log birden fazla farklı COCO ölçüm çifti içeriyor",
        )
    return next(iter(valid))


def write_dfine_evaluation_plan(path: Path, plan: DfineDetectorEvaluationPlan) -> Path:
    _atomic_write_json(path, plan.model_dump_json(indent=2) + "\n")
    return path.resolve()


def load_dfine_evaluation_plan(path: Path) -> DfineDetectorEvaluationPlan:
    try:
        return DfineDetectorEvaluationPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationReportError(
            "EVALUATION_PLAN_INVALID", f"evaluation plan okunamadı: {exc}"
        ) from exc


def _workspace_path(
    workspace: Path,
    path: Path,
    *,
    must_be_file: bool = False,
    must_be_directory: bool = False,
    allow_missing: bool = False,
) -> Path:
    if path.is_symlink():
        raise EvaluationReportError("UNSAFE_EVALUATION_PATH", f"symlink reddedildi: {path}")
    resolved = path.resolve()
    if not resolved.is_relative_to(workspace):
        raise EvaluationReportError(
            "UNSAFE_EVALUATION_PATH", f"değerlendirme yolu workspace dışında: {path}"
        )
    if must_be_file and not resolved.is_file():
        raise EvaluationReportError("EVALUATION_FILE_MISSING", f"dosya bulunamadı: {path}")
    if must_be_directory and not resolved.is_dir():
        raise EvaluationReportError("EVALUATION_DIRECTORY_MISSING", f"dizin bulunamadı: {path}")
    if not allow_missing and not must_be_file and not must_be_directory and not resolved.exists():
        raise EvaluationReportError("EVALUATION_PATH_MISSING", f"yol bulunamadı: {path}")
    return resolved


def _resolve_workspace_file(workspace: Path, reference: str, *, code: str) -> Path:
    if not _safe_reference(reference):
        raise EvaluationReportError(f"{code}_REF_INVALID", f"güvensiz yol: {reference}")
    path = workspace.joinpath(*reference.split("/")).resolve()
    if not path.is_relative_to(workspace) or not path.is_file() or path.is_symlink():
        raise EvaluationReportError(f"{code}_MISSING", f"dosya bulunamadı: {reference}")
    return path


def _safe_reference(value: str) -> bool:
    posix = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    return (
        bool(value)
        and not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ".." not in posix.parts
        and value == posix.as_posix()
    )


def _payload_sha256(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        raise EvaluationReportError(
            "PROJECT_GIT_CHECK_FAILED", f"proje git kontrolü başarısız: {exc}"
        ) from exc
    return result.stdout.strip()


def _atomic_write_json(path: Path, content: str) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)


__all__ = [
    "CocoEvaluationInventory",
    "DfineDetectorEvaluationPlan",
    "build_dfine_test_command",
    "execute_dfine_detector_evaluation",
    "inspect_coco_evaluation_inventory",
    "inspect_project_revision",
    "load_dfine_evaluation_plan",
    "normalize_dfine_evaluation_log",
    "parse_dfine_coco_metrics",
    "prepare_dfine_detector_evaluation",
    "write_dfine_evaluation_plan",
]
