

from __future__ import annotations

import hashlib
import json
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
from dortgoz.domain.training import (
    FrameReviewResult,
    TrainingFrameReview,
    TrainingSample,
    TrainingSampleStatus,
    VerifiedBoundingBox,
)
from dortgoz.services.coco_export import export_verified_frames_to_coco
from dortgoz.services.training_selection import (
    SelectionExclusion,
    TrainingSelectionError,
    TrainingSelectionPolicy,
    select_training_samples,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _manifest() -> OfflineDatasetManifest:
    entries = [
        DatasetVideoRecord(
            dataset_video_id=f"fixture/{split.value}/{name}",
            source_ref=f"videos/{split.value}/{name}.mp4",
            source_label="fixture",
            split=split,
            file_size_bytes=100 + index,
            file_sha256=_sha(f"video-{split.value}-{name}".encode()),
            allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        )
        for index, (split, name) in enumerate(
            [
                (DatasetSplit.TRAIN, "a"),
                (DatasetSplit.TRAIN, "b"),
                (DatasetSplit.TRAIN, "c"),
                (DatasetSplit.VALIDATION, "v1"),
                (DatasetSplit.VALIDATION, "v2"),
            ]
        )
    ]
    return OfflineDatasetManifest(
        dataset_id="approved-fixture",
        source_name="Approved fixture",
        source_url="https://example.invalid/fixture",
        citation="Test fixture.",
        license_status=DatasetLicenseStatus.VERIFIED,
        license_id="Apache-2.0",
        redistribution_allowed=True,
        training_allowed=True,
        allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        entries=entries,
        dataset_fingerprint=calculate_dataset_fingerprint(entries),
    )


def _sample(
    manifest: OfflineDatasetManifest,
    *,
    sample_id: str,
    video_name: str,
    split: DatasetSplit,
    event_id: str,
    category: str | None,
    frame_payload: bytes | None = None,
    selection_reason: str = "event_peak",
) -> TrainingSample:
    video_id = f"fixture/{split.value}/{video_name}"
    entry = next(item for item in manifest.entries if item.dataset_video_id == video_id)
    payload = frame_payload or f"frame-{sample_id}".encode()
    frame_ref = f"{split.value}/{sample_id}.jpg"
    boxes = (
        [VerifiedBoundingBox(category_name=category, x=10, y=10, width=20, height=20)]
        if category is not None
        else []
    )
    reviewed_at = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    review = TrainingFrameReview(
        annotation_id=sample_id,
        dataset_id=manifest.dataset_id,
        dataset_fingerprint=manifest.dataset_fingerprint,
        dataset_video_id=video_id,
        source_video_ref=entry.source_ref,
        frame_ref=frame_ref,
        frame_sha256=_sha(payload),
        frame_size_bytes=len(payload),
        timestamp_seconds=5,
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
        reviewer="operator",
        annotation_tool="Dortgoz",
        reviewed_at=reviewed_at,
    )
    return TrainingSample(
        sample_id=sample_id,
        event_id=event_id,
        event_revision=2,
        review_id=f"review-{event_id}",
        approval_id=f"approval-{event_id}",
        video_id=f"video-{video_name}",
        source_video_sha256=entry.file_sha256,
        dataset_id=manifest.dataset_id,
        dataset_fingerprint=manifest.dataset_fingerprint,
        dataset_video_id=video_id,
        source_video_ref=entry.source_ref,
        split=split,
        timestamp_seconds=5,
        selection_reason=selection_reason,
        frame_ref=frame_ref,
        frame_sha256=_sha(payload),
        frame_size_bytes=len(payload),
        image_width=640,
        image_height=480,
        status=TrainingSampleStatus.VERIFIED,
        prepared_by="operator",
        frame_review=review,
        created_at=reviewed_at,
        updated_at=reviewed_at,
        revision=2,
    )


def _policy(**updates: object) -> TrainingSelectionPolicy:
    return TrainingSelectionPolicy.model_validate(
        {
            "minimum_train_samples": 3,
            "maximum_train_samples": 3,
            "minimum_validation_samples": 2,
            "maximum_validation_samples": 2,
            "minimum_train_source_videos": 3,
            "maximum_samples_per_source_video": 2,
            "maximum_samples_per_event": 1,
            "maximum_negative_fraction": 0.5,
            **updates,
        }
    )


def _balanced_samples(manifest: OfflineDatasetManifest) -> list[TrainingSample]:
    return [
        _sample(
            manifest,
            sample_id="person-a-1",
            video_name="a",
            split=DatasetSplit.TRAIN,
            event_id="event-a-1",
            category="person",
        ),
        _sample(
            manifest,
            sample_id="person-a-2",
            video_name="a",
            split=DatasetSplit.TRAIN,
            event_id="event-a-2",
            category="person",
        ),
        _sample(
            manifest,
            sample_id="person-a-3",
            video_name="a",
            split=DatasetSplit.TRAIN,
            event_id="event-a-3",
            category="person",
        ),
        _sample(
            manifest,
            sample_id="weapon-b",
            video_name="b",
            split=DatasetSplit.TRAIN,
            event_id="event-b",
            category="weapon",
        ),
        _sample(
            manifest,
            sample_id="negative-c",
            video_name="c",
            split=DatasetSplit.TRAIN,
            event_id="event-c",
            category=None,
        ),
        _sample(
            manifest,
            sample_id="validation-person",
            video_name="v1",
            split=DatasetSplit.VALIDATION,
            event_id="event-v1",
            category="person",
        ),
        _sample(
            manifest,
            sample_id="validation-negative",
            video_name="v2",
            split=DatasetSplit.VALIDATION,
            event_id="event-v2",
            category=None,
        ),
    ]


def test_selection_is_deterministic_and_prioritizes_diversity() -> None:
    manifest = _manifest()
    samples = _balanced_samples(manifest)
    measured_at = datetime(2026, 8, 16, 13, 0, tzinfo=UTC)

    first = select_training_samples(
        samples=samples,
        dataset_manifest=manifest,
        policy=_policy(),
        created_at=measured_at,
    )
    second = select_training_samples(
        samples=list(reversed(samples)),
        dataset_manifest=manifest,
        policy=_policy(),
        created_at=measured_at,
    )

    train = [
        sample for sample in first.selected_samples if sample.split == DatasetSplit.TRAIN
    ]
    assert {sample.dataset_video_id for sample in train} == {
        "fixture/train/a",
        "fixture/train/b",
        "fixture/train/c",
    }
    assert {sample.sample_id for sample in train} >= {"weapon-b", "negative-c"}
    assert first.report.selected_sample_ids == second.report.selected_sample_ids
    assert first.report.selection_fingerprint == second.report.selection_fingerprint
    assert first.report.counts.selected_train == 3
    assert first.report.counts.selected_validation == 2
    assert first.report.counts.selected_negative == 2


def test_selection_deduplicates_exact_frames_and_limits_negatives() -> None:
    manifest = _manifest()
    samples = _balanced_samples(manifest)
    duplicate = _sample(
        manifest,
        sample_id="weapon-b-copy",
        video_name="b",
        split=DatasetSplit.TRAIN,
        event_id="event-b-copy",
        category="weapon",
        frame_payload=b"shared-weapon-frame",
    )
    original = _sample(
        manifest,
        sample_id="weapon-b-original",
        video_name="b",
        split=DatasetSplit.TRAIN,
        event_id="event-b-original",
        category="weapon",
        frame_payload=b"shared-weapon-frame",
    )
    samples = [sample for sample in samples if sample.sample_id != "weapon-b"]
    samples.extend([duplicate, original])

    result = select_training_samples(
        samples=samples,
        dataset_manifest=manifest,
        policy=_policy(maximum_negative_fraction=0.34),
    )

    duplicate_decisions = [
        item
        for item in result.report.decisions
        if item.exclusion == SelectionExclusion.EXACT_DUPLICATE
    ]
    assert len(duplicate_decisions) == 1
    train_negatives = [
        sample
        for sample in result.selected_samples
        if sample.split == DatasetSplit.TRAIN and not sample.frame_review.boxes
    ]
    assert len(train_negatives) <= 1


def test_selection_rejects_frame_leakage_between_splits() -> None:
    manifest = _manifest()
    shared = b"same-frame-in-two-splits"
    samples = [
        _sample(
            manifest,
            sample_id="train-leak",
            video_name="a",
            split=DatasetSplit.TRAIN,
            event_id="event-train",
            category="person",
            frame_payload=shared,
        ),
        _sample(
            manifest,
            sample_id="validation-leak",
            video_name="v1",
            split=DatasetSplit.VALIDATION,
            event_id="event-validation",
            category="person",
            frame_payload=shared,
        ),
    ]

    with pytest.raises(TrainingSelectionError) as rejected:
        select_training_samples(
            samples=samples,
            dataset_manifest=manifest,
            policy=_policy(),
        )
    assert rejected.value.code == "SELECTION_SPLIT_LEAKAGE"


def test_coco_export_binds_the_selection_fingerprint(tmp_path: Path) -> None:
    manifest = _manifest()
    samples = _balanced_samples(manifest)
    selection = select_training_samples(
        samples=samples,
        dataset_manifest=manifest,
        policy=_policy(),
    )
    frame_root = tmp_path / "frames"
    for sample in selection.selected_samples:
        frame_path = frame_root / sample.frame_ref
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        frame_path.write_bytes(f"frame-{sample.sample_id}".encode())

    result = export_verified_frames_to_coco(
        dataset_manifest=manifest,
        reviews=[sample.frame_review for sample in selection.selected_samples],
        frame_root=frame_root,
        output_dir=tmp_path / "coco",
        selection_report=selection.report,
    )

    export_manifest = json.loads(result.export_manifest.read_text(encoding="utf-8"))
    assert export_manifest["selection"] == {
        "policy_version": selection.report.policy_version,
        "policy_fingerprint": selection.report.policy_fingerprint,
        "selection_fingerprint": selection.report.selection_fingerprint,
    }
    with pytest.raises(ValueError, match="seçim raporuyla eşleşmiyor"):
        export_verified_frames_to_coco(
            dataset_manifest=manifest,
            reviews=[selection.selected_samples[0].frame_review],
            frame_root=frame_root,
            output_dir=tmp_path / "rejected-coco",
            selection_report=selection.report,
        )
