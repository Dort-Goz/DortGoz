from __future__ import annotations

from pathlib import Path

from dortgoz.config import Settings, settings


def test_good_pipeline_is_the_default():

    assert settings.main_model == "llm-fast"
    assert settings.video_model == "vlm"
    assert settings.second_opinion_model == "llm-large"
    assert settings.agent_model == "llm-fast"
    assert settings.onnx_device == "cpu"
    assert Path(settings.candidate_model_manifest).as_posix().endswith(
        "models/semantic/manifest.json"
    )
    assert settings.candidate_screening is True
    assert settings.detector_enabled is True


def test_learned_suppression_stays_off_by_default():

    assert settings.exemplar_suppress is False
    assert settings.exemplar_shadow is True
    assert settings.escalate_shadow is True
    assert settings.shadow_sample_rate == 0.0
    assert settings.category_rules_enabled is False
    assert settings.incident_review_strict is False
    assert settings.escalation_zoom_seconds == 0.0
    assert settings.escalate_low_severity is False


def test_dfine_path_falls_back_when_the_configured_one_is_missing(tmp_path,
                                                                  monkeypatch):
    weights = tmp_path / ".cache" / "dortgoz" / "dfine" / "model.onnx"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"x")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    resolved = Settings(dfine_onnx="/yok/olmayan/model.onnx").dfine_onnx

    assert resolved == str(weights)


def test_existing_dfine_path_is_left_alone(tmp_path):
    weights = tmp_path / "model.onnx"
    weights.write_bytes(b"x")

    assert Settings(dfine_onnx=str(weights)).dfine_onnx == str(weights)


def test_missing_everywhere_keeps_the_configured_value(tmp_path, monkeypatch):

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert Settings(dfine_onnx="/yok/x.onnx").dfine_onnx == "/yok/x.onnx"
