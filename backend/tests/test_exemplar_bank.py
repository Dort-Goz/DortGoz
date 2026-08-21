from __future__ import annotations

import json

import pytest

from dortgoz.services import exemplar_bank as bank


def test_key_is_recoverable_from_the_evidence_url():
    assert bank.key_from_evidence(
        "/media/_evidence/run-1/30.mp4") == "run-1/30"
    assert bank.key_from_evidence(
        "/media/_evidence/run-1/ornek_60.mp4") == "run-1/ornek_60"
    assert bank.key_from_evidence(None) is None
    assert bank.key_from_evidence("/media/_thumbs/run-1/30.jpg") is None


def test_append_skips_empty_embeddings(tmp_path):
    bank.append(tmp_path, "k", "kamera1", None)
    bank.append(tmp_path, "k", "kamera1", [])

    assert bank.load(tmp_path) == {}


def test_round_trip(tmp_path):
    bank.append(tmp_path, "run-1/30", "kamera1", [1.0, 0.0, 0.0])

    loaded = bank.load(tmp_path)

    assert loaded["run-1/30"].feed == "kamera1"
    assert loaded["run-1/30"].embedding == (1.0, 0.0, 0.0)


def test_cosine_is_sane():
    assert bank.cosine((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    assert bank.cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    assert bank.cosine((1.0, 0.0), (1.0, 0.0, 0.0)) == -1.0
    assert bank.cosine((0.0, 0.0), (1.0, 0.0)) == -1.0


def _ledger(tmp_path, rows):
    path = tmp_path / "nobet_defteri.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def test_only_dismissed_detections_become_benign_exemplars(tmp_path):
    bank.append(tmp_path, "run-1/10", "kamera1", [1.0, 0.0])
    bank.append(tmp_path, "run-1/20", "kamera1", [0.0, 1.0])
    bank.append(tmp_path, "run-1/30", "kamera2", [1.0, 1.0])
    ledger = _ledger(tmp_path, [
        {"evidence": "/media/_evidence/run-1/10.mp4",
         "verdict": "sorun_degil", "feed": "kamera1"},
        {"evidence": "/media/_evidence/run-1/20.mp4",
         "verdict": "anomali", "feed": "kamera1"},
        {"evidence": "/media/_evidence/run-1/30.mp4",
         "verdict": "sorun_degil", "feed": "kamera2"},
    ])

    out = bank.benign_exemplars(tmp_path, ledger)

    assert sorted(out) == ["kamera1", "kamera2"]
    assert [e.key for e in out["kamera1"]] == ["run-1/10"]
    assert [e.key for e in out["kamera2"]] == ["run-1/30"]


def test_revised_verdict_supersedes_the_earlier_one(tmp_path):
    bank.append(tmp_path, "run-1/10", "kamera1", [1.0, 0.0])
    ledger = _ledger(tmp_path, [
        {"evidence": "/media/_evidence/run-1/10.mp4",
         "verdict": "sorun_degil", "feed": "kamera1"},
        {"evidence": "/media/_evidence/run-1/10.mp4",
         "verdict": "anomali", "feed": "kamera1"},
    ])

    assert bank.benign_exemplars(tmp_path, ledger) == {}


def test_nearest_finds_the_closest_exemplar(tmp_path):
    exemplars = [
        bank.Exemplar("a", "kamera1", (1.0, 0.0)),
        bank.Exemplar("b", "kamera1", (0.0, 1.0)),
    ]

    sim, hit = bank.nearest((0.9, 0.1), exemplars)

    assert hit is not None and hit.key == "a"
    assert sim > 0.9
    assert bank.nearest((1.0, 0.0), [])[1] is None
