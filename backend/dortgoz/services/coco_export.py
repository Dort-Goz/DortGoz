"""Fail-closed COCO export for human-verified D-FINE training frames."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..domain.dataset import DatasetLicenseStatus, DatasetSplit, DatasetUse, OfflineDatasetManifest
from ..domain.training import TrainingFrameReview, TrainingSample, TrainingSampleStatus
from .dataset_manifest import sha256_file
from .training_selection import TrainingSelectionReport


@dataclass(frozen=True)
class CocoExportResult:
    output_dir: Path
    train_annotations: Path
    validation_annotations: Path
    export_manifest: Path
    export_fingerprint: str
    frame_count: int
    box_count: int


def load_training_frame_reviews(annotation_dir: Path) -> list[TrainingFrameReview]:
    root = annotation_dir.resolve()
    if not root.is_dir():
        raise ValueError(f"D-FINE annotation dizini bulunamadı: {annotation_dir}")
    reviews: list[TrainingFrameReview] = []
    for path in sorted(root.glob("*.json")):
        if path.name == "schema.json":
            continue
        try:
            reviews.append(
                TrainingFrameReview.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise ValueError(f"D-FINE annotation okunamadı: {path}: {exc}") from exc
    if not reviews:
        raise ValueError("insan doğrulamalı D-FINE annotation JSON bulunamadı")
    return reviews


def training_reviews_from_samples(
    samples: list[TrainingSample], dataset_manifest: OfflineDatasetManifest
) -> list[TrainingFrameReview]:
    reviews = [
        sample.frame_review
        for sample in samples
        if sample.status == TrainingSampleStatus.VERIFIED
        and sample.dataset_id == dataset_manifest.dataset_id
        and sample.dataset_fingerprint == dataset_manifest.dataset_fingerprint
        and sample.frame_review is not None
    ]
    if not reviews:
        raise ValueError("dataset için etkin ve doğrulanmış training sample bulunamadı")
    return reviews


def export_verified_frames_to_coco(
    *,
    dataset_manifest: OfflineDatasetManifest,
    reviews: list[TrainingFrameReview],
    frame_root: Path,
    output_dir: Path,
    selection_report: TrainingSelectionReport | None = None,
) -> CocoExportResult:
    """Validate provenance and emit COCO JSON without copying any image or video."""

    ensure_training_manifest_allowed(dataset_manifest)
    root = frame_root.resolve()
    if not root.is_dir():
        raise ValueError(f"frame root bulunamadı: {frame_root}")
    if not reviews:
        raise ValueError("COCO aktarımı için doğrulanmış kare bulunamadı")
    if selection_report is not None:
        if (
            selection_report.dataset_id != dataset_manifest.dataset_id
            or selection_report.dataset_fingerprint
            != dataset_manifest.dataset_fingerprint
        ):
            raise ValueError("seçim raporu dataset manifestiyle eşleşmiyor")
        review_ids = {review.annotation_id for review in reviews}
        selected_ids = set(selection_report.selected_sample_ids)
        if review_ids != selected_ids:
            raise ValueError("COCO review listesi seçim raporuyla eşleşmiyor")

    entries_by_id = {entry.dataset_video_id: entry for entry in dataset_manifest.entries}
    annotation_ids: set[str] = set()
    frame_hashes: dict[str, TrainingFrameReview] = {}
    validated: list[TrainingFrameReview] = []
    for review in reviews:
        if review.annotation_id in annotation_ids:
            raise ValueError(f"annotation_id tekrar ediyor: {review.annotation_id}")
        annotation_ids.add(review.annotation_id)
        if review.dataset_id != dataset_manifest.dataset_id:
            raise ValueError(f"annotation dataset_id eşleşmiyor: {review.annotation_id}")
        if review.dataset_fingerprint != dataset_manifest.dataset_fingerprint:
            raise ValueError(f"annotation dataset fingerprint eşleşmiyor: {review.annotation_id}")
        entry = entries_by_id.get(review.dataset_video_id)
        if entry is None:
            raise ValueError(
                f"annotation videosu dataset manifestinde yok: {review.dataset_video_id}"
            )
        if entry.source_ref != review.source_video_ref or entry.split != review.split:
            raise ValueError(
                f"annotation video kaynağı veya split eşleşmiyor: {review.annotation_id}"
            )
        if entry.split == DatasetSplit.TEST or DatasetUse.TRAINING not in entry.allowed_uses:
            raise ValueError(
                f"annotation videosu training kullanımına açık değil: {review.annotation_id}"
            )
        previous = frame_hashes.get(review.frame_sha256)
        if previous is not None:
            raise ValueError(
                "aynı kare içeriği birden fazla annotation içinde bulunamaz: "
                f"{previous.annotation_id}, {review.annotation_id}"
            )
        frame_hashes[review.frame_sha256] = review
        frame_path = _resolve_frame(root, review.frame_ref)
        if frame_path.stat().st_size != review.frame_size_bytes:
            raise ValueError(f"kare boyutu değişti: {review.frame_ref}")
        if sha256_file(frame_path) != review.frame_sha256:
            raise ValueError(f"kare SHA-256 değeri değişti: {review.frame_ref}")
        validated.append(review)

    split_counts = Counter(review.split for review in validated)
    for split in (DatasetSplit.TRAIN, DatasetSplit.VALIDATION):
        if split_counts[split] == 0:
            raise ValueError("D-FINE aktarımı ayrı train ve validation kareleri gerektirir")
    categories = sorted({box.category_name for review in validated for box in review.boxes})
    if not categories:
        raise ValueError("D-FINE aktarımı en az bir doğrulanmış hedef sınıfı gerektirir")
    category_ids = {name: index for index, name in enumerate(categories, 1)}

    payloads: dict[DatasetSplit, dict[str, Any]] = {}
    for split in (DatasetSplit.TRAIN, DatasetSplit.VALIDATION):
        split_reviews = sorted(
            (review for review in validated if review.split == split),
            key=lambda review: (
                review.source_video_ref,
                review.timestamp_seconds,
                review.frame_ref,
                review.annotation_id,
            ),
        )
        payloads[split] = _build_coco_payload(
            dataset_manifest.dataset_id,
            split,
            split_reviews,
            category_ids,
        )

    target = output_dir.resolve()
    annotation_target = target / "annotations"
    annotation_target.mkdir(parents=True, exist_ok=True)
    train_target = annotation_target / "instances_train.json"
    validation_target = annotation_target / "instances_validation.json"
    train_bytes = _json_bytes(payloads[DatasetSplit.TRAIN])
    validation_bytes = _json_bytes(payloads[DatasetSplit.VALIDATION])
    train_sha = hashlib.sha256(train_bytes).hexdigest()
    validation_sha = hashlib.sha256(validation_bytes).hexdigest()
    review_fingerprints = sorted(_review_fingerprint(review) for review in validated)
    fingerprint_payload = {
        "export_version": "1.0.0",
        "dataset_id": dataset_manifest.dataset_id,
        "dataset_fingerprint": dataset_manifest.dataset_fingerprint,
        "review_fingerprints": review_fingerprints,
        "coco_sha256": {
            "train": train_sha,
            "validation": validation_sha,
        },
        "categories": categories,
    }
    if selection_report is not None:
        fingerprint_payload["selection"] = {
            "policy_version": selection_report.policy_version,
            "policy_fingerprint": selection_report.policy_fingerprint,
            "selection_fingerprint": selection_report.selection_fingerprint,
        }
    export_fingerprint = _payload_sha256(fingerprint_payload)
    box_count = sum(len(review.boxes) for review in validated)
    export_manifest = {
        **fingerprint_payload,
        "export_fingerprint": export_fingerprint,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_license": dataset_manifest.license_id,
        "media_copied": False,
        "image_root_required": True,
        "counts": {
            "frames": len(validated),
            "boxes": box_count,
            "train_frames": split_counts[DatasetSplit.TRAIN],
            "validation_frames": split_counts[DatasetSplit.VALIDATION],
        },
    }
    manifest_target = target / "export_manifest.json"
    _atomic_write(train_target, train_bytes)
    _atomic_write(validation_target, validation_bytes)
    _atomic_write(manifest_target, _json_bytes(export_manifest))
    return CocoExportResult(
        output_dir=target,
        train_annotations=train_target,
        validation_annotations=validation_target,
        export_manifest=manifest_target,
        export_fingerprint=export_fingerprint,
        frame_count=len(validated),
        box_count=box_count,
    )


def ensure_training_manifest_allowed(manifest: OfflineDatasetManifest) -> None:
    if (
        not manifest.training_allowed
        or DatasetUse.TRAINING not in manifest.allowed_uses
        or manifest.license_status != DatasetLicenseStatus.VERIFIED
        or manifest.license_id not in {"Apache-2.0", "MIT"}
    ):
        raise ValueError(
            f"dataset D-FINE training için onaylı değil: {manifest.dataset_id} "
            f"(license_status={manifest.license_status.value}, license_id={manifest.license_id})"
        )


def _resolve_frame(root: Path, frame_ref: str) -> Path:
    path = root.joinpath(*frame_ref.split("/")).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise ValueError(f"kare bulunamadı veya güvensiz: {frame_ref}")
    return path


def _build_coco_payload(
    dataset_id: str,
    split: DatasetSplit,
    reviews: list[TrainingFrameReview],
    category_ids: dict[str, int],
) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    annotation_id = 1
    for image_id, review in enumerate(reviews, 1):
        images.append(
            {
                "id": image_id,
                "file_name": review.frame_ref,
                "width": review.image_width,
                "height": review.image_height,
            }
        )
        for box in review.boxes:
            annotations.append(
                {
                    "id": annotation_id,
                    "image_id": image_id,
                    "category_id": category_ids[box.category_name],
                    "bbox": [box.x, box.y, box.width, box.height],
                    "area": box.width * box.height,
                    "iscrowd": int(box.is_crowd),
                }
            )
            annotation_id += 1
    return {
        "info": {
            "description": f"Dortgoz human-verified D-FINE {split.value} export",
            "version": "1.0.0",
        },
        "licenses": [],
        "images": images,
        "annotations": annotations,
        "categories": [
            {"id": category_id, "name": name, "supercategory": "dortgoz"}
            for name, category_id in category_ids.items()
        ],
    }


def _review_fingerprint(review: TrainingFrameReview) -> str:
    return _payload_sha256(review.model_dump(mode="json"))


def _payload_sha256(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
