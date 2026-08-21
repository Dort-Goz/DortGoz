from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from dortgoz.config import Settings
from dortgoz.domain.dataset import (
    DatasetLicenseStatus,
    DatasetSplit,
    DatasetUse,
    DatasetVideoRecord,
    OfflineDatasetManifest,
    calculate_dataset_fingerprint,
)
from dortgoz.pipeline.candidate_model import load_candidate_scorer
from dortgoz.pipeline.ingest import MotionSample
from dortgoz.pipeline.temporal_cnn import (
    TemporalCnnArtifact,
    TemporalCnnCandidateModel,
    TemporalCnnTrainingExample,
    evaluate_temporal_cnn,
    train_temporal_cnn,
)


def profile() -> list[MotionSample]:
    return [
        MotionSample(t=0, changed=0.01, fg=0.01, mad=0.01),
        MotionSample(t=1, changed=0.02, fg=0.02, mad=0.02),
        MotionSample(t=2, changed=0.80, fg=0.75, mad=0.70),
        MotionSample(t=3, changed=0.85, fg=0.80, mad=0.75),
        MotionSample(t=4, changed=0.02, fg=0.02, mad=0.01),
        MotionSample(t=5, changed=0.01, fg=0.01, mad=0.01),
    ]


def test_temporal_cnn_trains_and_scores_positive_interval_higher() -> None:
    examples = [
        TemporalCnnTrainingExample("train-a", profile(), ((2.0, 3.0),)),
        TemporalCnnTrainingExample("train-b", profile(), ((2.0, 3.0),)),
    ]
    model, metrics = train_temporal_cnn(
        examples,
        model_id="temporal-cnn-test-v1",
        epochs=160,
        learning_rate=0.2,
        seed=7,
        artifact_license="Apache-2.0",
    )

    scores = model.score(profile())
    assert scores[2].anomaly_score > scores[0].anomaly_score
    assert scores[3].anomaly_score > 0.5
    assert metrics.recall_at_half == 1.0
    assert evaluate_temporal_cnn(model, examples).false_positive_rate_at_half < 0.5


def test_temporal_cnn_manifest_hash_loads_local_scorer(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "pyproject.toml").write_text("fixture", encoding="utf-8")
    model_dir = tmp_path / "models" / "candidate" / "local" / "fixture"
    model_dir.mkdir(parents=True)
    artifact = TemporalCnnArtifact(
        model_id="temporal-cnn-fixture-v1",
        version="1.0.0",
        kernel_size=3,
        weights=((0.0, 0.0, 0.0, 0.0),) * 3,
        bias=0.2,
        trained_sample_count=12,
        training_seed=1,
        license="MIT",
    )
    artifact_path = model_dir / "temporal-cnn-fixture-v1.json"
    artifact_path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    manifest_path = model_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_id": artifact.model_id,
                "version": artifact.version,
                "model_type": "temporal_cnn",
                "artifact_path": "models/candidate/local/fixture/temporal-cnn-fixture-v1.json",
                "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                "license": "MIT",
                "input_fps": 1.0,
                "feature_schema": ["changed", "fg", "mad", "activity"],
            }
        ),
        encoding="utf-8",
    )

    scorer = load_candidate_scorer(manifest_path)

    assert isinstance(scorer, TemporalCnnCandidateModel)
    assert scorer.model_id == artifact.model_id
    assert scorer.score(profile())[0].anomaly_score > 0.5


def test_temporal_cnn_rejects_unbalanced_or_invalid_artifact() -> None:
    with pytest.raises(ValueError, match="pozitif interval"):
        train_temporal_cnn(
            [TemporalCnnTrainingExample("negative", profile(), ())],
            model_id="temporal-cnn-test-v1",
        )


def test_relative_candidate_manifest_setting_is_repo_root_relative() -> None:
    settings = Settings(candidate_manifest_path=Path("models/candidate/local/model/manifest.json"))

    assert settings.candidate_manifest_path == (
        Path(__file__).parents[3] / "models" / "candidate" / "local" / "model" / "manifest.json"
    ).resolve()
    with pytest.raises(ValueError, match="kernel_size"):
        TemporalCnnArtifact(
            model_id="invalid",
            version="1",
            kernel_size=2,
            weights=((0.0, 0.0, 0.0, 0.0),) * 2,
            bias=0.0,
            trained_sample_count=0,
            training_seed=0,
            license="MIT",
        )


def test_training_cli_writes_local_artifact_from_separated_annotations(
    monkeypatch, tmp_path: Path
) -> None:
    module_name = "train_candidate_test_fixture"
    script_path = Path(__file__).parents[3] / "scripts" / "train_candidate.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    annotation_dir = tmp_path / "annotations"
    video_root = tmp_path / "videos"
    annotation_dir.mkdir()
    video_root.mkdir()
    for name, split in (("train.mp4", "train"), ("validation.mp4", "validation")):
        (video_root / name).write_bytes(f"fixture-{split}".encode())
        (annotation_dir / f"{split}.json").write_text(
            json.dumps(
                {
                    "video_id": split,
                    "source_ref": name,
                    "split": split,
                    "intervals": [{"start_time": 2, "end_time": 3, "label": "candidate"}],
                }
            ),
            encoding="utf-8",
        )
    dataset_entries = [
        DatasetVideoRecord(
            dataset_video_id=f"fixture/{path.stem}",
            source_ref=path.name,
            source_label="fixture",
            split=DatasetSplit.TRAIN if path.name == "train.mp4" else DatasetSplit.VALIDATION,
            file_size_bytes=path.stat().st_size,
            file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        )
        for path in sorted(video_root.glob("*.mp4"))
    ]
    dataset_manifest = OfflineDatasetManifest(
        dataset_id="fixture-approved",
        source_name="Fixture approved dataset",
        source_url="https://example.invalid/fixture",
        citation="Local test fixture.",
        license_status=DatasetLicenseStatus.VERIFIED,
        license_id="Apache-2.0",
        redistribution_allowed=True,
        training_allowed=True,
        allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        entries=dataset_entries,
        dataset_fingerprint=calculate_dataset_fingerprint(dataset_entries),
    )
    dataset_manifest_path = tmp_path / "dataset-manifest.json"
    dataset_manifest_path.write_text(
        dataset_manifest.model_dump_json(indent=2), encoding="utf-8"
    )

    async def fake_motion_profile(_: Path, *, base_fps: float) -> list[MotionSample]:
        assert base_fps == 1.0
        return profile()

    monkeypatch.setattr(module, "motion_profile", fake_motion_profile)
    manifest_path = module.train_and_write_temporal_cnn(
        annotation_dir=annotation_dir,
        video_root=video_root,
        dataset_manifest_path=dataset_manifest_path,
        output_dir=tmp_path / "models" / "candidate" / "local" / "fixture",
        model_id="temporal-cnn-cli-v1",
        version="1.0.0",
        base_fps=1.0,
        kernel_size=3,
        epochs=120,
        learning_rate=0.2,
        l2=0.0001,
        seed=3,
        artifact_license="Apache-2.0",
        min_validation_recall=0.0,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["model_type"] == "temporal_cnn"
    assert manifest["artifact_path"].startswith("models/candidate/local/")
    assert manifest["training_dataset"]["dataset_fingerprint"] == (
        dataset_manifest.dataset_fingerprint
    )
    assert "validation" in manifest["notes"]
