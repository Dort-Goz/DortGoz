from __future__ import annotations

from pathlib import Path

import pytest

from dortgoz.services import exemplar_bank as bank
from dortgoz.services.exemplar_bank import Matcher

BENIGN = [bank.Exemplar("run/10", "kamera1", (1.0, 0.0, 0.0))]
NEAR = (0.999, 0.045, 0.0)
FAR = (0.0, 1.0, 0.0)


def _matcher(tmp_path: Path, monkeypatch) -> Matcher:
    m = Matcher(tmp_path, tmp_path / "nobet_defteri.jsonl")
    monkeypatch.setattr(m, "_refresh", lambda: None)
    m._by_feed = {"kamera1": BENIGN}
    return m


def _check(m: Matcher, category="kavga", risk="orta", emb=NEAR,
           enabled=True, shadow=False, threshold=0.97):
    return m.check("kamera1", category, risk, emb,
                   threshold=threshold, enabled=enabled, shadow=shadow)


def test_close_precedent_suppresses_when_enabled(tmp_path, monkeypatch):
    m = _check(_matcher(tmp_path, monkeypatch))

    assert m.suppress is True
    assert m.precedent is not None and m.precedent.key == "run/10"
    assert m.similarity > 0.97


def test_distant_detection_is_not_suppressed(tmp_path, monkeypatch):
    m = _check(_matcher(tmp_path, monkeypatch), emb=FAR)

    assert m.suppress is False
    assert "en yak\u0131n emsal" in m.reason


@pytest.mark.parametrize("category", sorted(bank.HARD_FLOOR_CATEGORIES))
def test_hard_floor_categories_are_never_suppressed(tmp_path, monkeypatch, category):
    m = _check(_matcher(tmp_path, monkeypatch), category=category)

    assert m.suppress is False
    assert "sert taban" in m.reason


def test_critical_risk_is_never_suppressed(tmp_path, monkeypatch):
    m = _check(_matcher(tmp_path, monkeypatch), risk="kritik")

    assert m.suppress is False
    assert "sert taban" in m.reason


def test_shadow_mode_matches_but_never_suppresses(tmp_path, monkeypatch):
    m = _check(_matcher(tmp_path, monkeypatch), shadow=True)

    assert m.suppress is False
    assert m.shadow is True
    assert m.similarity > 0.97


def test_disabled_short_circuits(tmp_path, monkeypatch):
    m = _check(_matcher(tmp_path, monkeypatch), enabled=False)

    assert m.suppress is False
    assert m.reason == "kapal\u0131"


def test_camera_without_exemplars_is_not_suppressed(tmp_path, monkeypatch):
    m = Matcher(tmp_path, tmp_path / "yok.jsonl")
    monkeypatch.setattr(m, "_refresh", lambda: None)
    m._by_feed = {"baska": BENIGN}

    out = m.check("kamera1", "kavga", "orta", NEAR,
                  threshold=0.97, enabled=True, shadow=False)

    assert out.suppress is False
    assert "emsali yok" in out.reason


def test_missing_embedding_is_not_suppressed(tmp_path, monkeypatch):
    m = _check(_matcher(tmp_path, monkeypatch), emb=None)

    assert m.suppress is False
    assert m.reason == "g\u00f6mme yok"
