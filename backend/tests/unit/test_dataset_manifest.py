

from __future__ import annotations

import hashlib
import json
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
from dortgoz.services.dataset_manifest import (
    build_ucf_crime_manifest,
    load_dataset_manifest,
    verify_training_sources,
    write_dataset_manifest,
)


def _annotation(path: Path, *, video_id: str, source_ref: str, split: str) -> None:
    path.write_text(
        json.dumps(
            {
                "video_id": video_id,
                "source_ref": source_ref,
                "split": split,
                "intervals": [],
            }
        ),
        encoding="utf-8",
    )


def test_ucf_index_is_hash_stable_and_benchmark_only(tmp_path: Path) -> None:
    dataset_root = tmp_path / "UCF_Crimes"
    videos = dataset_root / "Videos"
    assault = videos / "Assault" / "Assault001_x264.mp4"
    normal = videos / "Testing_Normal_Videos_Anomaly" / "Normal001_x264.mp4"
    assault.parent.mkdir(parents=True)
    normal.parent.mkdir(parents=True)
    assault.write_bytes(b"assault-fixture")
    normal.write_bytes(b"normal-fixture")
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    _annotation(
        annotations / "assault.json",
        video_id="Assault001_x264",
        source_ref="Assault/Assault001_x264.mp4",
        split="train",
    )
    _annotation(
        annotations / "normal.json",
        video_id="Normal001_x264",
        source_ref="Testing_Normal_Videos_Anomaly/Normal001_x264.mp4",
        split="test",
    )

    first = build_ucf_crime_manifest(dataset_root, annotation_dir=annotations)
    second = build_ucf_crime_manifest(videos, annotation_dir=annotations)
    manifest_path = write_dataset_manifest(tmp_path / "runs" / "manifest.json", first)
    loaded = load_dataset_manifest(manifest_path)

    assert first.dataset_fingerprint == second.dataset_fingerprint
    assert loaded.dataset_fingerprint == first.dataset_fingerprint
    assert loaded.license_status == DatasetLicenseStatus.UNVERIFIED
    assert loaded.training_allowed is False
    assert loaded.redistribution_allowed is False
    assert {item.split for item in loaded.entries} == {
        DatasetSplit.TRAIN,
        DatasetSplit.TEST,
    }
    assert all(item.allowed_uses == [DatasetUse.BENCHMARK] for item in loaded.entries)
    assert str(tmp_path) not in loaded.model_dump_json()

    with pytest.raises(ValueError, match="training için onaylı değil"):
        verify_training_sources(
            loaded,
            videos,
            {"Assault/Assault001_x264.mp4": "train"},
        )


def test_ucf_index_rejects_duplicate_content_across_splits(tmp_path: Path) -> None:
    videos = tmp_path / "Videos"
    first = videos / "Assault" / "first.mp4"
    second = videos / "Assault" / "second.mp4"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"same-video")
    second.write_bytes(b"same-video")
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    _annotation(
        annotations / "first.json",
        video_id="first",
        source_ref="Assault/first.mp4",
        split="train",
    )
    _annotation(
        annotations / "second.json",
        video_id="second",
        source_ref="Assault/second.mp4",
        split="validation",
    )

    with pytest.raises(ValueError, match="birden fazla split"):
        build_ucf_crime_manifest(videos, annotation_dir=annotations)


def test_verified_training_manifest_rehashes_sources(tmp_path: Path) -> None:
    videos = tmp_path / "videos"
    videos.mkdir()
    train = videos / "train.mp4"
    validation = videos / "validation.mp4"
    train.write_bytes(b"train")
    validation.write_bytes(b"valid")
    entries = [
        DatasetVideoRecord(
            dataset_video_id="fixture/train",
            source_ref="train.mp4",
            source_label="fixture",
            split=DatasetSplit.TRAIN,
            file_size_bytes=train.stat().st_size,
            file_sha256=hashlib.sha256(train.read_bytes()).hexdigest(),
            allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        ),
        DatasetVideoRecord(
            dataset_video_id="fixture/validation",
            source_ref="validation.mp4",
            source_label="fixture",
            split=DatasetSplit.VALIDATION,
            file_size_bytes=validation.stat().st_size,
            file_sha256=hashlib.sha256(validation.read_bytes()).hexdigest(),
            allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        ),
    ]
    manifest = OfflineDatasetManifest(
        dataset_id="fixture-approved",
        source_name="Fixture approved dataset",
        source_url="https://example.invalid/fixture",
        citation="Local test fixture.",
        license_status=DatasetLicenseStatus.VERIFIED,
        license_id="Apache-2.0",
        redistribution_allowed=True,
        training_allowed=True,
        allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        entries=entries,
        dataset_fingerprint=calculate_dataset_fingerprint(entries),
    )
    expected = {"train.mp4": "train", "validation.mp4": "validation"}

    verify_training_sources(manifest, videos, expected)
    train.write_bytes(b"TRAIN")

    with pytest.raises(ValueError, match="SHA-256"):
        verify_training_sources(manifest, videos, expected)
