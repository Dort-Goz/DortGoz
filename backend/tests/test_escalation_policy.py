from __future__ import annotations

import pytest

from dortgoz.services import calibration, escalation_policy
from dortgoz.services.escalation_policy import resolve


@pytest.fixture(autouse=True)
def _runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(escalation_policy.settings, "runs_dir", tmp_path)
    monkeypatch.setattr(escalation_policy.settings, "escalate_p", 0.10)
    monkeypatch.setattr(escalation_policy.settings, "escalate_target_p", 0.0)
    monkeypatch.setattr(escalation_policy.settings, "escalate_shadow", True)
    return tmp_path


def _write_cal(tmp_path, a: float, b: float) -> None:
    cal = calibration.Calibration(
        a=a, b=b, n_pos=15, n_neg=5,
        brier_before=0.2, brier_after=0.1,
        logloss_before=0.7, logloss_after=0.3, fitted_at=0.0,
        model_id=escalation_policy.settings.video_model)
    calibration.save(cal, tmp_path / "kalibrasyon.json")


def test_without_a_target_the_static_threshold_is_used(_runs_dir):
    gate = resolve()

    assert gate.value == pytest.approx(0.10)
    assert gate.source == "sabit"
    assert gate.acts


def test_missing_calibration_falls_back_to_static(_runs_dir, monkeypatch):
    monkeypatch.setattr(escalation_policy.settings, "escalate_target_p", 0.5)

    gate = resolve()

    assert gate.value == pytest.approx(0.10)
    assert gate.source == "sabit"
    assert "kalibrasyon dosyas\u0131 yok" in gate.detail


def test_calibration_may_lower_the_threshold(_runs_dir, monkeypatch):
    _write_cal(_runs_dir, a=0.2205, b=0.8930)
    monkeypatch.setattr(escalation_policy.settings, "escalate_target_p", 0.5)

    gate = resolve()

    assert gate.source == "kalibre"
    assert gate.value < 0.10


def test_calibration_may_never_raise_the_threshold(_runs_dir, monkeypatch):

    _write_cal(_runs_dir, a=1.0, b=-5.0)
    monkeypatch.setattr(escalation_policy.settings, "escalate_target_p", 0.5)

    gate = resolve()

    assert gate.value == pytest.approx(0.10)
    assert gate.source == "sabit"
    assert "Y\u00dcKSELT\u0130LMED\u0130" in gate.detail


def test_shadow_mode_reports_but_does_not_act(_runs_dir, monkeypatch):
    _write_cal(_runs_dir, a=0.2205, b=0.8930)
    monkeypatch.setattr(escalation_policy.settings, "escalate_target_p", 0.5)

    assert resolve().acts is False

    monkeypatch.setattr(escalation_policy.settings, "escalate_shadow", False)
    assert resolve().acts is True
