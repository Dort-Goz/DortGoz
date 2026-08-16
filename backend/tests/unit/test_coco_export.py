"""Human review and fail-closed COCO export tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from dortgoz.domain.dataset import (
    DatasetLicenseStatus,
    DatasetSplit,
    DatasetUse,
    DatasetVideoRecord,
    OfflineDatasetManifest,
    calculate_dataset_fingerprint,
)
from dortgoz.domain.training import (
    FrameReviewResult,
    TrainingFrameReview,
    VerifiedBoundingBox,
)
from dortgoz.services.coco_export import (
    export_verified_frames_to_coco,
    load_training_frame_reviews,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _manifest(*, training_allowed: bool = True) -> OfflineDatasetManifest:
    uses = (
        [DatasetUse.TRAINING, DatasetUse.EVALUATION] if training_allowed else [DatasetUse.BENCHMARK]
    )
    entries = [
        DatasetVideoRecord(
            dataset_video_id="fixture/train",
            source_ref="videos/train.mp4",
            source_label="fixture",
            split=DatasetSplit.TRAIN,
            file_size_bytes=10,
            file_sha256=_sha(b"train-video"),
            allowed_uses=uses,
        ),
        DatasetVideoRecord(
            dataset_video_id="fixture/validation",
            source_ref="videos/validation.mp4",
            source_label="fixture",
            split=DatasetSplit.VALIDATION,
            file_size_bytes=16,
            file_sha256=_sha(b"validation-video"),
            allowed_uses=uses,
        ),
    ]
    return OfflineDatasetManifest(
        dataset_id="fixture-approved" if training_allowed else "ucf-crime",
        source_name="Approved fixture" if training_allowed else "UCF-Crime",
        source_url="https://example.invalid/dataset",
        citation="Local test fixture.",
        license_status=(
            DatasetLicenseStatus.VERIFIED if training_allowed else DatasetLicenseStatus.UNVERIFIED
        ),
        license_id="Apache-2.0" if training_allowed else None,
        redistribution_allowed=training_allowed,
        training_allowed=training_allowed,
        allowed_uses=uses,
        entries=entries,
        dataset_fingerprint=calculate_dataset_fingerprint(entries),
    )


def _review(
    manifest: OfflineDatasetManifest,
    *,
    annotation_id: str,
    split: DatasetSplit,
    frame_ref: str,
    frame_payload: bytes,
    boxes: list[VerifiedBoundingBox],
) -> TrainingFrameReview:
    is_train = split == DatasetSplit.TRAIN
    return TrainingFrameReview(
        annotation_id=annotation_id,
        dataset_id=manifest.dataset_id,
        dataset_fingerprint=manifest.dataset_fingerprint,
        dataset_video_id="fixture/train" if is_train else "fixture/validation",
        source_video_ref="videos/train.mp4" if is_train else "videos/validation.mp4",
        frame_ref=frame_ref,
        frame_sha256=_sha(frame_payload),
        frame_size_bytes=len(frame_payload),
        timestamp_seconds=2.5 if is_train else 7.5,
        image_width=640,
        image_height=480,
        split=split,
        review_result=(
            FrameReviewResult.VERIFIED_BOXES
            if boxes
            else FrameReviewResult.VERIFIED_NO_TARGET_OBJECTS
        ),
        boxes=boxes,
        human_verified=True,
        reviewer="operator-1",
        annotation_tool="CVAT Community",
        reviewed_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )


def test_export_writes_deterministic_coco_without_copying_media(tmp_path: Path) -> None:
    manifest = _manifest()
    frame_root = tmp_path / "frames"
    train_payload = b"fake-train-jpeg"
    validation_payload = b"fake-validation-jpeg"
    train_frame = frame_root / "train" / "frame-001.jpg"
    validation_frame = frame_root / "validation" / "frame-002.jpg"
    train_frame.parent.mkdir(parents=True)
    validation_frame.parent.mkdir(parents=True)
    train_frame.write_bytes(train_payload)
    validation_frame.write_bytes(validation_payload)
    reviews = [
        _review(
            manifest,
            annotation_id="ann-train",
            split=DatasetSplit.TRAIN,
            frame_ref="train/frame-001.jpg",
            frame_payload=train_payload,
            boxes=[VerifiedBoundingBox(category_name="person", x=10, y=20, width=30, height=40)],
        ),
        _review(
            manifest,
            annotation_id="ann-validation",
            split=DatasetSplit.VALIDATION,
            frame_ref="validation/frame-002.jpg",
            frame_payload=validation_payload,
            boxes=[],
        ),
    ]

    first = export_verified_frames_to_coco(
        dataset_manifest=manifest,
        reviews=reviews,
        frame_root=frame_root,
        output_dir=tmp_path / "coco-first",
    )
    second = export_verified_frames_to_coco(
        dataset_manifest=manifest,
        reviews=list(reversed(reviews)),
        frame_root=frame_root,
        output_dir=tmp_path / "coco-second",
    )

    train = json.loads(first.train_annotations.read_text(encoding="utf-8"))
    validation = json.loads(first.validation_annotations.read_text(encoding="utf-8"))
    export_manifest = json.loads(first.export_manifest.read_text(encoding="utf-8"))
    assert train["images"] == [
        {"id": 1, "file_name": "train/frame-001.jpg", "width": 640, "height": 480}
    ]
    assert train["annotations"] == [
        {
            "id": 1,
            "image_id": 1,
            "category_id": 1,
            "bbox": [10.0, 20.0, 30.0, 40.0],
            "area": 1200.0,
            "iscrowd": 0,
        }
    ]
    assert train["categories"] == [{"id": 1, "name": "person", "supercategory": "dortgoz"}]
    assert validation["annotations"] == []
    assert first.export_fingerprint == second.export_fingerprint
    assert first.train_annotations.read_bytes() == second.train_annotations.read_bytes()
    assert first.frame_count == 2 and first.box_count == 1
    assert export_manifest["media_copied"] is False
    assert export_manifest["counts"] == {
        "frames": 2,
        "boxes": 1,
        "train_frames": 1,
        "validation_frames": 1,
    }
    assert str(tmp_path) not in first.export_manifest.read_text(encoding="utf-8")
    assert not (first.output_dir / "train" / "frame-001.jpg").exists()


def test_loader_reads_one_strict_review_per_json(tmp_path: Path) -> None:
    manifest = _manifest()
    annotation_dir = tmp_path / "annotations"
    annotation_dir.mkdir()
    review = _review(
        manifest,
        annotation_id="ann-1",
        split=DatasetSplit.TRAIN,
        frame_ref="train/frame.jpg",
        frame_payload=b"frame",
        boxes=[VerifiedBoundingBox(category_name="person", x=0, y=0, width=10, height=10)],
    )
    (annotation_dir / "schema.json").write_text("{}", encoding="utf-8")
    (annotation_dir / "ann-1.json").write_text(review.model_dump_json(), encoding="utf-8")

    assert load_training_frame_reviews(annotation_dir) == [review]


def test_ucf_benchmark_manifest_is_rejected_for_dfine_training(tmp_path: Path) -> None:
    manifest = _manifest(training_allowed=False)

    with pytest.raises(ValueError, match="D-FINE training için onaylı değil"):
        export_verified_frames_to_coco(
            dataset_manifest=manifest,
            reviews=[],
            frame_root=tmp_path,
            output_dir=tmp_path / "coco",
        )


def test_export_rejects_changed_frame_and_duplicate_content(tmp_path: Path) -> None:
    manifest = _manifest()
    frame_root = tmp_path / "frames"
    (frame_root / "train").mkdir(parents=True)
    (frame_root / "validation").mkdir(parents=True)
    train_payload = b"same-frame"
    (frame_root / "train" / "a.jpg").write_bytes(b"other-data")
    (frame_root / "validation" / "b.jpg").write_bytes(train_payload)
    train_review = _review(
        manifest,
        annotation_id="ann-train",
        split=DatasetSplit.TRAIN,
        frame_ref="train/a.jpg",
        frame_payload=train_payload,
        boxes=[VerifiedBoundingBox(category_name="person", x=0, y=0, width=10, height=10)],
    )
    validation_review = _review(
        manifest,
        annotation_id="ann-validation",
        split=DatasetSplit.VALIDATION,
        frame_ref="validation/b.jpg",
        frame_payload=train_payload,
        boxes=[],
    )

    with pytest.raises(ValueError, match="SHA-256"):
        export_verified_frames_to_coco(
            dataset_manifest=manifest,
            reviews=[train_review],
            frame_root=frame_root,
            output_dir=tmp_path / "changed-output",
        )

    (frame_root / "train" / "a.jpg").write_bytes(train_payload)
    with pytest.raises(ValueError, match="aynı kare içeriği"):
        export_verified_frames_to_coco(
            dataset_manifest=manifest,
            reviews=[train_review, validation_review],
            frame_root=frame_root,
            output_dir=tmp_path / "duplicate-output",
        )


def test_review_rejects_unverified_or_out_of_bounds_boxes() -> None:
    manifest = _manifest()
    payload = b"frame"
    base = {
        "annotation_id": "ann",
        "dataset_id": manifest.dataset_id,
        "dataset_fingerprint": manifest.dataset_fingerprint,
        "dataset_video_id": "fixture/train",
        "source_video_ref": "videos/train.mp4",
        "frame_ref": "train/frame.jpg",
        "frame_sha256": _sha(payload),
        "frame_size_bytes": len(payload),
        "timestamp_seconds": 1,
        "image_width": 100,
        "image_height": 100,
        "split": "train",
        "review_result": "verified_boxes",
        "boxes": [{"category_name": "person", "x": 90, "y": 0, "width": 20, "height": 10}],
        "reviewer": "operator-1",
        "annotation_tool": "CVAT Community",
        "reviewed_at": "2026-08-16T12:00:00Z",
    }

    with pytest.raises(ValidationError, match="human_verified"):
        TrainingFrameReview.model_validate({**base, "human_verified": False})
    with pytest.raises(ValidationError, match="görüntü sınırları"):
        TrainingFrameReview.model_validate({**base, "human_verified": True})
