from __future__ import annotations

import json

import pytest

from dortgoz.services import calibration


def test_fit_refuses_when_one_class_is_too_thin():
    pairs = [(0.99, 1)] * 10 + [(0.01, 0)]

    with pytest.raises(calibration.NotEnoughLabels):
        calibration.fit_platt(pairs)


def test_calibration_improves_a_badly_overconfident_signal():
    # model 0.99 diyor ama vakaların yarısı aslında olay değil
    pairs = [(0.99, 1)] * 6 + [(0.99, 0)] * 6 + [(0.01, 0)] * 6
    cal = calibration.calibrate(pairs, now=0.0)

    assert cal.brier_after < cal.brier_before
    assert cal.logloss_after < cal.logloss_before
    # 0.99'luk grup gerçekte ~%50 -> kalibre edilmiş değer aşağı çekilmeli
    assert 0.25 < cal.apply(0.99) < 0.75


def test_calibration_is_monotone_and_bounded():
    pairs = [(0.9, 1)] * 5 + [(0.1, 0)] * 5
    cal = calibration.calibrate(pairs, now=0.0)

    values = [cal.apply(p) for p in (0.001, 0.01, 0.1, 0.5, 0.9, 0.999)]
    assert values == sorted(values)
    assert all(0.0 <= v <= 1.0 for v in values)


def test_nearly_separable_data_does_not_blow_up():
    pairs = [(0.999, 1)] * 12 + [(0.001, 0)] * 5
    cal = calibration.calibrate(pairs, now=0.0)

    assert all(map(lambda v: 0.0 <= v <= 1.0, (cal.apply(0.999), cal.apply(0.001))))
    assert cal.apply(0.999) > cal.apply(0.001)


def test_ledger_reader_takes_the_latest_verdict_per_key(tmp_path):
    path = tmp_path / "nobet_defteri.jsonl"
    rows = [
        {"key": "k1", "verdict": "anomali", "signals": {"durum_p": 0.8}},
        {"key": "k1", "verdict": "sorun_degil", "signals": {"durum_p": 0.8}},
        {"key": "k2", "verdict": "anomali", "signals": {"durum_p": 0.4}},
        {"key": "k3", "verdict": "expired", "signals": {"durum_p": 0.4}},
        {"key": "k4", "verdict": "anomali", "signals": {}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    pairs = calibration.pairs_from_ledger(path)

    assert sorted(pairs) == [(0.4, 1), (0.8, 0)]


def test_round_trip_through_disk(tmp_path):
    pairs = [(0.9, 1)] * 5 + [(0.1, 0)] * 5
    cal = calibration.calibrate(pairs, now=123.0)
    path = tmp_path / "kalibrasyon.json"

    calibration.save(cal, path)

    assert calibration.load(path) == cal
    assert calibration.load(tmp_path / "yok.json") is None
