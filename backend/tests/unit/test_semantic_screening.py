import hashlib
import json
from pathlib import Path

import pytest

from dortgoz.domain.candidate import ScreeningSample
from dortgoz.pipeline.ingest import MotionSample
from dortgoz.pipeline.semantic import (
    CausalWelford,
    SemanticArtifact,
    SemanticCandidateModel,
    SemanticPrior,
)


def _artifact(**overrides) -> SemanticArtifact:
    payload = {
        "model_id": "siglip2-semantic-v1",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "onnx_path": "models/semantic/local/siglip2_vision.onnx",
        "onnx_sha256": "0" * 64,
        "anchors_path": "models/semantic/local/siglip2_anchors.npz",
        "anchors_sha256": "0" * 64,
    }
    payload.update(overrides)
    return SemanticArtifact.model_validate(payload)


def test_causal_welford_warmup_then_scores_outlier() -> None:
    base = CausalWelford(None, sd_floor=0.01, warmup=3)
    zs = [base.push(v) for v in (0.1, 0.1, 0.1, 0.1, 0.1, 0.9)]
    assert zs[:3] == [0.0, 0.0, 0.0]
    assert zs[3] == pytest.approx(0.0)
    assert zs[5] > 5.0


def test_causal_welford_prior_seeds_first_sample() -> None:
    prior = SemanticPrior(mu=0.2, sd=0.1, n=8.0)
    base = CausalWelford(prior, sd_floor=0.01, warmup=3)
    assert base.push(0.4) == pytest.approx(2.0, abs=0.15)


def test_causal_welford_is_strictly_past_only() -> None:
    prior = SemanticPrior(mu=0.0, sd=1.0, n=4.0)
    a = CausalWelford(prior, sd_floor=0.01, warmup=0)
    first = a.push(100.0)
    assert first == pytest.approx(100.0, rel=0.05)
    assert a.push(100.0) < first


def test_artifact_rejects_bad_hash_and_weight() -> None:
    with pytest.raises(Exception):
        _artifact(onnx_sha256="xyz")
    with pytest.raises(Exception):
        _artifact(ev_weight=1.5)


def test_score_video_combines_semantic_and_activity(monkeypatch, tmp_path: Path) -> None:
    art = _artifact(
        ev_prior={"mu": 0.03, "sd": 0.02, "n": 8.0},
        act_prior={"mu": 0.15, "sd": 0.2, "n": 8.0},
    )
    model = SemanticCandidateModel(art, onnx_file=tmp_path / "x.onnx",
                                   anchors_file=tmp_path / "a.npz")
    sims = [(t, 0.10 if 20 <= t < 24 else 0.03) for t in range(0, 30, 2)]
    monkeypatch.setattr(model, "_event_sims",
                        lambda video: [(float(t), s, [s, 1.0 - s]) for t, s in sims])
    profile = [MotionSample(t=float(t), changed=0.6 if 20 <= t < 24 else 0.05,
                            fg=0.0, mad=0.0)
               for t in range(30)]
    samples = model.score_video(profile, tmp_path / "video.mp4")
    assert all(isinstance(s, ScreeningSample) for s in samples)
    assert all(s.embedding for s in samples)
    quiet = [s.anomaly_score for s in samples if s.timestamp < 20]
    spike = [s.anomaly_score for s in samples if 20 <= s.timestamp < 24]
    assert max(quiet) < 0.65 < min(spike)
    assert samples[0].source_model == "siglip2-semantic-v1"


def test_adaptive_saturation_shift_is_causal_and_floor_respecting() -> None:
    from dortgoz.pipeline.candidate_intervals import adaptive_saturation_shift

    def mk(scores):
        return [ScreeningSample(timestamp=float(i), anomaly_score=s,
                                image_quality=1.0, source_model="t")
                for i, s in enumerate(scores)]

    quiet = mk([0.3, 0.7, 0.5] * 20)
    out = adaptive_saturation_shift(quiet, start_threshold=0.60, warmup_samples=5)
    assert [s.anomaly_score for s in out] == [s.anomaly_score for s in quiet]

    scores = [0.70, 0.99] + [0.70] * 40
    out = adaptive_saturation_shift(mk(scores), start_threshold=0.60,
                                    saturation=0.95, raised_threshold=0.85,
                                    warmup_samples=5)
    assert out[0].anomaly_score == pytest.approx(0.70)
    assert out[41].anomaly_score == pytest.approx(0.45)
    ev = adaptive_saturation_shift(mk([0.99] + [0.1] * 30 + [0.95]),
                                   start_threshold=0.60, warmup_samples=5)
    assert ev[-1].anomaly_score >= 0.60


def test_score_without_frames_is_refused() -> None:
    model = SemanticCandidateModel(_artifact(), onnx_file=Path("x"), anchors_file=Path("y"))
    with pytest.raises(RuntimeError):
        model.score([])


def test_relative_model_manifest_resolves_to_repo_root(monkeypatch) -> None:
    from dortgoz.config import Settings

    monkeypatch.setenv("DORTGOZ_CANDIDATE_MODEL_MANIFEST", "models/semantic/manifest.json")
    resolved = Settings().candidate_model_manifest
    assert Path(resolved).is_absolute()
    assert Path(resolved).as_posix().endswith("models/semantic/manifest.json")
    monkeypatch.setenv("DORTGOZ_CANDIDATE_MODEL_MANIFEST", "")
    assert Settings().candidate_model_manifest == ""


def test_manifest_loads_semantic_scorer(tmp_path: Path) -> None:
    from dortgoz.pipeline.candidate_model import load_candidate_scorer

    root = tmp_path
    (root / "backend").mkdir()
    (root / "backend" / "pyproject.toml").write_text("x", encoding="utf-8")
    sem = root / "models" / "semantic"
    (sem / "local").mkdir(parents=True)
    onnx = sem / "local" / "siglip2_vision.onnx"
    anchors = sem / "local" / "siglip2_anchors.npz"
    onnx.write_bytes(b"fake-onnx")
    anchors.write_bytes(b"fake-npz")
    artifact = {
        "model_id": "siglip2-semantic-v1",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "onnx_path": "models/semantic/local/siglip2_vision.onnx",
        "onnx_sha256": hashlib.sha256(b"fake-onnx").hexdigest(),
        "anchors_path": "models/semantic/local/siglip2_anchors.npz",
        "anchors_sha256": hashlib.sha256(b"fake-npz").hexdigest(),
    }
    artifact_file = sem / "semantic-v1.json"
    artifact_file.write_text(json.dumps(artifact), encoding="utf-8")
    manifest = {
        "model_id": "siglip2-semantic-v1",
        "version": "1.0.0",
        "model_type": "siglip_semantic",
        "artifact_path": "models/semantic/semantic-v1.json",
        "artifact_sha256": hashlib.sha256(artifact_file.read_bytes()).hexdigest(),
        "license": "Apache-2.0",
        "input_fps": 0.5,
        "feature_schema": ["siglip2_event_sim", "activity"],
    }
    manifest_file = sem / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    scorer = load_candidate_scorer(manifest_file)
    assert isinstance(scorer, SemanticCandidateModel)
    assert scorer.model_id == "siglip2-semantic-v1"
    assert hasattr(scorer, "score_video")
    scorer.verify_artifacts()
    anchors.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="çapa"):
        scorer.verify_artifacts()
