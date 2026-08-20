from __future__ import annotations

import json
from pathlib import Path

import pytest

from dortgoz.domain.candidate import ScreeningSample
from dortgoz.pipeline.candidate_intervals import IntervalConfig, build_candidate_intervals
from dortgoz.pipeline.candidate_model import MotionBaselineModel, load_manifest
from dortgoz.pipeline.feature_cache import FeatureCacheKey, JsonFeatureCache
from dortgoz.pipeline.ingest import MotionSample


def sample(
    timestamp: float,
    *,
    anomaly: float = 0.0,
    interaction: float = 0.0,
    fall: float = 0.0,
) -> ScreeningSample:
    return ScreeningSample(
        timestamp=timestamp,
        anomaly_score=anomaly,
        interaction_score=interaction,
        fall_score=fall,
        source_model="fixture-model",
    )


def test_hysteresis_keeps_short_low_gap_and_closes_after_patience() -> None:
    result = build_candidate_intervals(
        [
            sample(0),
            sample(1, interaction=0.90),
            sample(2, interaction=0.60),
            sample(3, interaction=0.20),
            sample(4, interaction=0.10),
            sample(5, fall=0.92),
            sample(6, fall=0.80),
            sample(7, fall=0.10),
            sample(8, fall=0.10),
        ],
        analysis_id="analysis-07",
        video_id="video-07",
        duration_seconds=10,
        model_id="fixture-model",
        config=IntervalConfig(
            start_threshold=0.65,
            continue_threshold=0.40,
            end_patience=2,
            merge_gap_seconds=0,
        ),
    )

    assert len(result) == 2
    assert result[0].candidate_type.value == "intense_person_interaction"
    assert result[1].candidate_type.value == "possible_fall"
    assert result[0].start_time <= 1 <= result[0].peak_time <= result[0].end_time
    assert result[0].peak_score == 0.9
    assert result[1].peak_score == 0.92


def test_specialist_signal_uses_or_logic_and_empty_is_safe() -> None:
    result = build_candidate_intervals(
        [sample(1, fall=0.9)],
        analysis_id="analysis-07",
        video_id="video-07",
        duration_seconds=4,
        model_id="fixture-model",
    )
    assert len(result) == 1
    assert result[0].candidate_type.value == "possible_fall"
    assert build_candidate_intervals(
        [],
        analysis_id="analysis-07",
        video_id="video-07",
        duration_seconds=4,
        model_id="fixture-model",
    ) == []


def test_interval_rejects_unsorted_or_out_of_range_samples() -> None:
    kwargs = {
        "analysis_id": "analysis-07",
        "video_id": "video-07",
        "duration_seconds": 4,
        "model_id": "fixture-model",
    }
    with pytest.raises(ValueError, match="sıralı"):
        build_candidate_intervals([sample(2), sample(1)], **kwargs)
    with pytest.raises(ValueError, match="dışına"):
        build_candidate_intervals([sample(5)], **kwargs)


def test_motion_baseline_bounds_scores_and_manifest_hash() -> None:
    model = MotionBaselineModel()
    samples = model.score(
        [MotionSample(t=0, changed=0.16, fg=0.2, mad=0.1)]
    )
    assert samples[0].anomaly_score == 0.8
    assert samples[0].interaction_score == 0.8
    assert model.manifest.model_id == model.model_id

    manifest_path = Path(__file__).parents[3] / "models" / "candidate" / "manifest.json"
    loaded = load_manifest(manifest_path)
    assert loaded.model_id == model.model_id
    assert loaded.artifact_sha256 == model.manifest.artifact_sha256


def test_feature_cache_round_trip_is_restart_safe(tmp_path: Path) -> None:
    cache = JsonFeatureCache(tmp_path / "features")
    key = FeatureCacheKey(
        video_hash_sha256="a" * 64,
        model_id="motion-baseline-v1",
        feature_version="motion-v1",
    )
    samples = [sample(1, anomaly=0.8)]
    saved = cache.save(key, samples)
    loaded = cache.load(key)
    assert loaded is not None
    assert loaded.key == key
    assert loaded.samples == samples
    assert cache.path_for(key).is_file()
    assert saved.created_at.tzinfo is not None

    cache.path_for(key).write_text(json.dumps({"key": key.model_dump()}), encoding="utf-8")
    with pytest.raises(ValueError, match="cache okunamadı|Field required"):
        cache.load(key)
