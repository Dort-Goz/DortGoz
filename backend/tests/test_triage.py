"""Anomali nöbet kuyruğu — alım, karar, oturum listesi, bütçeler."""

from __future__ import annotations

import json

import pytest

from dortgoz.config import settings
from dortgoz.events import Event, IncidentUpdate
from dortgoz.services import triage


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runs_dir", tmp_path / "runs")
    s = triage.TriageStore()
    return s


def _incident(feed="KAM-1", incident_id="inc-1", risk="yuksek",
              anomaly_type="kavga", phase="basladi") -> Event:
    return Event.wrap(IncidentUpdate(
        incident_id=incident_id, t=42.0, phase=phase, title="Fiziksel kavga",
        anomaly_type=anomaly_type, risk=risk), feed=feed)


def test_incident_update_lands_in_pending(store):
    store.observe(_incident())
    snap = store.snapshot()
    assert len(snap["pending"]) == 1
    item = snap["pending"][0]
    assert item["feed"] == "KAM-1" and item["model_category"] == "kavga"
    assert snap["confirmed"] == []


def test_lifecycle_update_refreshes_not_duplicates(store):
    store.observe(_incident(phase="basladi", risk="orta"))
    store.observe(_incident(phase="sonuclandi", risk="kritik"))
    snap = store.snapshot()
    assert len(snap["pending"]) == 1
    assert snap["pending"][0]["risk"] == "kritik"
    assert snap["pending"][0]["phase"] == "sonuclandi"


def test_non_incident_events_ignored(store):
    from dortgoz.events import RunStatus
    store.observe(Event.wrap(RunStatus(run_id="r", state="processing")))
    assert store.snapshot()["pending"] == []


def test_confirm_moves_to_session_list_with_operator_category(store):
    store.observe(_incident())
    item = store.decide("KAM-1:inc-1", "anomali", category="hirsizlik",
                        note="Kasadan para alıyor")
    assert item.operator_category == "hirsizlik"   # model 'kavga' demişti — insan düzeltti
    snap = store.snapshot()
    assert snap["pending"] == []
    assert snap["confirmed"][0]["operator_category"] == "hirsizlik"
    assert snap["confirmed"][0]["note"] == "Kasadan para alıyor"


def test_dismiss_counts_and_leaves_session_list_clean(store):
    store.observe(_incident())
    store.decide("KAM-1:inc-1", "sorun_degil")
    snap = store.snapshot()
    assert snap["pending"] == [] and snap["confirmed"] == []
    assert snap["dismissed_count"] == 1


def test_decided_incident_does_not_requeue(store):
    store.observe(_incident())
    store.decide("KAM-1:inc-1", "sorun_degil")
    store.observe(_incident(phase="sonuclandi"))   # geç yaşam döngüsü olayı
    assert store.snapshot()["pending"] == []


def test_confirm_requires_valid_category(store):
    store.observe(_incident())
    with pytest.raises(ValueError):
        store.decide("KAM-1:inc-1", "anomali", category="normal")
    with pytest.raises(KeyError):
        store.decide("KAM-9:yok", "sorun_degil")


def test_decisions_are_logged_to_duty_book(store):
    store.observe(_incident())
    store.decide("KAM-1:inc-1", "anomali", category="kavga")
    log = settings.runs_dir / "nobet_defteri.jsonl"
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["verdict"] == "anomali" and entry["operator_category"] == "kavga"


def test_pending_is_budgeted(store):
    # Farklı kameralardan (tekrar birleştirmeye takılmasın) kuyruk taşırılır
    for i in range(triage.MAX_PENDING + 20):
        store.observe(_incident(feed=f"KAM-{i}", incident_id=f"inc-{i}"))
    assert len(store.snapshot()["pending"]) == triage.MAX_PENDING


# ---- uyarlanma: tekrar birleştirme, bastırma kuralı, istem notu ----

def test_repeat_detection_merges_into_one_card(store):
    store.observe(_incident(incident_id="a", risk="orta"))
    store.observe(_incident(incident_id="b", risk="yuksek"))
    store.observe(_incident(incident_id="c", risk="dusuk"))
    snap = store.snapshot()
    assert len(snap["pending"]) == 1           # kuyruk tekrarla dolmaz
    assert snap["pending"][0]["tekrar"] == 3
    assert snap["pending"][0]["risk"] == "yuksek"   # en ciddisi korunur


def test_three_dismissals_create_suppression_rule(store):
    for i in range(triage.RULE_THRESHOLD):
        store.observe(_incident(incident_id=f"i{i}"))
        store.decide(f"KAM-1:i{i}", "sorun_degil")
    assert ("KAM-1", "kavga") in store.rules
    # kural doğduktan sonra aynı tespit kuyruğa DÜŞMEZ, otomatik elenir
    store.observe(_incident(incident_id="sonraki"))
    snap = store.snapshot()
    assert snap["pending"] == []
    assert snap["auto_dismissed"] == 1
    assert snap["rules"] == [{"feed": "KAM-1", "category": "kavga", "auto_count": 1}]


def test_confirmation_resets_dismissal_counter(store):
    for i in range(triage.RULE_THRESHOLD - 1):
        store.observe(_incident(incident_id=f"i{i}"))
        store.decide(f"KAM-1:i{i}", "sorun_degil")
    store.observe(_incident(incident_id="gercek"))
    store.decide("KAM-1:gercek", "anomali", category="kavga")
    store.observe(_incident(incident_id="tekrar"))
    store.decide("KAM-1:tekrar", "sorun_degil")
    assert ("KAM-1", "kavga") not in store.rules   # sayaç sıfırlandı, kural yok


def test_revoked_rule_requeues_detections(store):
    for i in range(triage.RULE_THRESHOLD):
        store.observe(_incident(incident_id=f"i{i}"))
        store.decide(f"KAM-1:i{i}", "sorun_degil")
    store.revoke_rule("KAM-1", "kavga")
    store.observe(_incident(incident_id="yeni"))
    assert len(store.snapshot()["pending"]) == 1


def test_feed_note_reflects_rules_and_reaches_prompt(store):
    assert store.feed_note("KAM-1") == ""
    for i in range(triage.RULE_THRESHOLD):
        store.observe(_incident(incident_id=f"i{i}", anomaly_type="arac_kazasi"))
        store.decide(f"KAM-1:i{i}", "sorun_degil")
    note = store.feed_note("KAM-1")
    assert "OLAĞAN" in note and "duran/yavaşlayan araçlar" in note
    assert store.feed_note("KAM-2") == ""      # başka kamera etkilenmez
