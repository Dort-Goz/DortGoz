from __future__ import annotations

import json

import pytest

from dortgoz.events import Event, IncidentUpdate, ReviewSample, RunStatus, WindowSignals
from dortgoz.services import triage


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(triage.settings, "runs_dir", tmp_path)
    triage.store.clear()
    yield
    triage.store.clear()


def signals(durum_p: float = 0.42, anomaly: float = 0.7) -> WindowSignals:
    return WindowSignals(
        durum_p=durum_p, anomaly_score=anomaly, interaction_score=0.1,
        fall_score=0.0, fire_smoke_score=0.0, vehicle_conflict_score=0.0,
        tampering_score=0.0, image_quality=0.9,
        changed=0.31, fg=0.22, mad=0.05, screening_model="siglip2-semantic-v1",
    )


def incident(iid: str = "INC-1", risk: str = "orta", sig: WindowSignals | None = None,
             evidence: str | None = None) -> Event:
    return Event.wrap(
        IncidentUpdate(
            incident_id=iid, t=12.0, phase="basladi", title="test olayı",
            anomaly_type="kavga", risk=risk, signals=sig, evidence=evidence,
        ),
        feed="kamera1",
    )


def run_started(run_id: str = "canli-kamera1-0001") -> Event:
    return Event.wrap(
        RunStatus(run_id=run_id, state="processing", video="kamera1.mp4"),
        feed="kamera1",
    )


def ledger_lines(tmp_path) -> list[dict]:
    path = tmp_path / "nobet_defteri.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_identity_is_captured_from_run_status(tmp_path):
    triage.store.observe(run_started())
    triage.store.observe(incident())
    item = triage.store.decide("kamera1:INC-1", "anomali", category="kavga")
    assert item.run_id == "canli-kamera1-0001"
    assert item.video == "kamera1.mp4"


def test_signals_reach_the_ledger_line(tmp_path):
    triage.store.observe(run_started())
    triage.store.observe(incident(sig=signals()))
    triage.store.decide("kamera1:INC-1", "sorun_degil")

    line = ledger_lines(tmp_path)[-1]
    assert line["signals"]["durum_p"] == pytest.approx(0.42)
    assert line["signals"]["anomaly_score"] == pytest.approx(0.7)
    assert line["signals"]["changed"] == pytest.approx(0.31)
    assert line["signals"]["screening_model"] == "siglip2-semantic-v1"


def test_evidence_clip_url_reaches_the_queue_and_the_ledger(tmp_path):
    url = "/media/_evidence/canli-kamera1-0001/30.mp4"
    triage.store.observe(run_started())
    triage.store.observe(incident(evidence=url))

    assert triage.store.snapshot()["pending"][0]["evidence"] == url

    triage.store.decide("kamera1:INC-1", "sorun_degil")
    assert ledger_lines(tmp_path)[-1]["evidence"] == url


def review_sample(sid: str = "S-1", sig: WindowSignals | None = None) -> Event:
    return Event.wrap(
        ReviewSample(sample_id=sid, t=30.0, window_start=30.0, window_end=60.0,
                     summary="olay yok", signals=sig,
                     evidence="/media/_evidence/r/ornek_30.mp4"),
        feed="kamera1",
    )


def test_review_sample_becomes_a_labelable_negative(tmp_path):
    triage.store.observe(run_started())
    triage.store.observe(review_sample(sig=signals(durum_p=0.06)))

    pending = triage.store.snapshot()["pending"]
    assert len(pending) == 1
    assert pending[0]["sample"] is True
    assert pending[0]["evidence"] == "/media/_evidence/r/ornek_30.mp4"

    triage.store.decide("kamera1:ornek:S-1", "sorun_degil")
    line = ledger_lines(tmp_path)[-1]
    assert line["sample"] is True
    assert line["verdict"] == "sorun_degil"
    assert line["signals"]["durum_p"] == pytest.approx(0.06)


def test_sample_dismissals_never_create_suppression_rules(tmp_path):
    triage.store.observe(run_started())
    for n in range(triage.RULE_THRESHOLD + 1):
        triage.store.observe(review_sample(sid=f"S-{n}"))
        triage.store.decide(f"kamera1:ornek:S-{n}", "sorun_degil")

    assert triage.store.rules == {}
    assert triage.store.dismissed_count == 0


def test_calibration_pairs_are_reconstructable(tmp_path):
    triage.store.observe(run_started())
    triage.store.observe(incident("INC-1", sig=signals(durum_p=0.9)))
    triage.store.decide("kamera1:INC-1", "anomali", category="kavga")
    triage.store.observe(incident("INC-2", sig=signals(durum_p=0.1)))
    triage.store.decide("kamera1:INC-2", "sorun_degil")

    pairs = [
        (line["signals"]["durum_p"], line["verdict"])
        for line in ledger_lines(tmp_path)
        if line["signals"].get("durum_p") is not None
    ]
    assert (0.9, "anomali") in pairs
    assert (0.1, "sorun_degil") in pairs


def test_repeat_keeps_the_most_confident_window(tmp_path):
    triage.store.observe(run_started())
    triage.store.observe(incident("INC-1", sig=signals(durum_p=0.2)))
    triage.store.observe(incident("INC-1", sig=signals(durum_p=0.8)))
    triage.store.observe(incident("INC-1", sig=signals(durum_p=0.5)))
    item = triage.store.decide("kamera1:INC-1", "anomali", category="kavga")
    assert item.signals["durum_p"] == pytest.approx(0.8)


def test_config_and_run_meta_are_snapshotted(tmp_path):
    (tmp_path / "canli-kamera1-0001.meta.json").write_text(
        json.dumps({"model": "qwen3.6-35b", "mode": "dengeli", "video": "kamera1.mp4"}),
        encoding="utf-8",
    )
    triage.store.observe(run_started())
    triage.store.observe(incident())
    item = triage.store.decide("kamera1:INC-1", "anomali", category="kavga")
    assert item.config["escalate_p"] == triage.settings.escalate_p
    assert item.run_meta["model"] == "qwen3.6-35b"
    assert item.run_meta["mode"] == "dengeli"
    assert len(item.run_meta["system_prompt_sha"]) == 12
    assert "system_prompt" not in item.run_meta


def test_operator_time_correction_is_recorded(tmp_path):
    triage.store.observe(incident())
    item = triage.store.decide(
        "kamera1:INC-1", "anomali", category="kavga",
        reviewer="bengisu", operator_start=10.0, operator_end=18.5,
    )
    assert (item.operator_start, item.operator_end) == (10.0, 18.5)
    assert item.reviewer == "bengisu"


def test_reversed_operator_times_are_rejected(tmp_path):
    triage.store.observe(incident())
    with pytest.raises(ValueError):
        triage.store.decide(
            "kamera1:INC-1", "anomali", category="kavga",
            operator_start=20.0, operator_end=5.0,
        )


def test_correction_appends_and_supersedes_without_erasing(tmp_path):
    triage.store.observe(incident())
    first = triage.store.decide("kamera1:INC-1", "sorun_degil")
    second = triage.store.revise(
        "kamera1:INC-1", "anomali", category="kavga", reviewer="arda")

    lines = ledger_lines(tmp_path)
    assert len(lines) == 2
    assert lines[0]["verdict"] == "sorun_degil"
    assert lines[1]["verdict"] == "anomali"
    assert second.supersedes == first.decision_id
    assert second.decision_id != first.decision_id


def test_decisions_carry_unique_ids(tmp_path):
    triage.store.observe(incident("INC-1"))
    triage.store.observe(
        Event.wrap(
            IncidentUpdate(
                incident_id="INC-2", t=30.0, phase="basladi", title="ikinci",
                anomaly_type="yangin", risk="orta",
            ),
            feed="kamera2",
        )
    )
    a = triage.store.decide("kamera1:INC-1", "sorun_degil")
    b = triage.store.decide("kamera2:INC-2", "sorun_degil")
    assert a.decision_id and b.decision_id and a.decision_id != b.decision_id


def test_queue_overflow_is_logged_instead_of_silently_dropped(tmp_path):
    for n in range(triage.MAX_PENDING + 5):
        triage.store.observe(
            Event.wrap(
                IncidentUpdate(
                    incident_id=f"INC-{n}", t=float(n), phase="basladi",
                    title="t", anomaly_type="kavga", risk="orta",
                ),
                feed=f"kamera{n}",
            )
        )
    expired = [line for line in ledger_lines(tmp_path) if line["verdict"] == "expired"]
    assert len(expired) == 5
    assert triage.store.expired_count == 5
    assert expired[0]["decision_id"]


def test_auto_dismissed_by_rule_still_records_signals(tmp_path):
    triage.store.observe(run_started())
    for n in range(triage.RULE_THRESHOLD):
        triage.store.observe(incident(f"INC-{n}", sig=signals()))
        triage.store.decide(f"kamera1:INC-{n}", "sorun_degil")
    triage.store.observe(incident("INC-99", sig=signals(durum_p=0.33)))

    auto = [line for line in ledger_lines(tmp_path) if "otomatik" in line["note"]]
    assert auto and auto[-1]["signals"]["durum_p"] == pytest.approx(0.33)
    assert auto[-1]["run_id"] == "canli-kamera1-0001"


def test_decide_endpoint_carries_operator_correction_to_the_ledger(tmp_path):
    from fastapi.testclient import TestClient

    from dortgoz.main import app

    triage.store.observe(run_started())
    triage.store.observe(incident(sig=signals()))

    with TestClient(app) as client:
        r = client.post("/api/triage/decide", json={
            "key": "kamera1:INC-1", "verdict": "anomali", "category": "kavga",
            "reviewer": "bengisu", "operator_start": 10.0, "operator_end": 18.5,
        })
    assert r.status_code == 200

    line = ledger_lines(tmp_path)[-1]
    assert line["reviewer"] == "bengisu"
    assert line["operator_start"] == 10.0
    assert line["operator_end"] == 18.5
    assert line["verdict"] == "anomali"


def test_model_bounds_are_kept_next_to_the_operator_correction(tmp_path):
    from dortgoz.events import Event, IncidentUpdate

    triage.store.observe(run_started())
    triage.store.observe(Event.wrap(
        IncidentUpdate(
            incident_id="INC-1", t=12.0, phase="sonuclandi", title="t",
            anomaly_type="kavga", risk="orta",
            olay_baslangic=11.0, olay_bitis=20.0,
        ),
        feed="kamera1",
    ))
    item = triage.store.decide(
        "kamera1:INC-1", "anomali", category="kavga",
        operator_start=9.5, operator_end=19.0)

    assert (item.model_start, item.model_end) == (11.0, 20.0)
    assert (item.operator_start, item.operator_end) == (9.5, 19.0)


def test_prompt_is_referenced_by_hash_not_duplicated(tmp_path):
    long_prompt = "x" * 5000
    (tmp_path / "canli-kamera1-0001.meta.json").write_text(
        json.dumps({"model": "m", "mode": "dengeli", "video": "v.mp4",
                    "system_prompt": long_prompt, "task_prompt": "t"}),
        encoding="utf-8",
    )
    triage.store.observe(run_started())
    triage.store.observe(incident())
    triage.store.decide("kamera1:INC-1", "sorun_degil")

    raw = (tmp_path / "nobet_defteri.jsonl").read_text(encoding="utf-8")
    assert long_prompt not in raw
    assert len(raw) < 3000

    line = ledger_lines(tmp_path)[-1]
    assert len(line["run_meta"]["system_prompt_sha"]) == 12


def test_prompt_change_is_detectable_across_decisions(tmp_path):
    meta = tmp_path / "canli-kamera1-0001.meta.json"
    meta.write_text(json.dumps({"system_prompt": "ilk istem"}), encoding="utf-8")
    triage.store.observe(run_started())
    triage.store.observe(incident("INC-1"))
    a = triage.store.decide("kamera1:INC-1", "sorun_degil")

    meta.write_text(json.dumps({"system_prompt": "degismis istem"}), encoding="utf-8")
    triage.store.observe(incident("INC-2", risk="yuksek"))
    b = triage.store.decide("kamera1:INC-2", "sorun_degil")

    assert a.run_meta["system_prompt_sha"] != b.run_meta["system_prompt_sha"]
