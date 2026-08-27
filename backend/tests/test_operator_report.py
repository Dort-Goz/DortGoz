from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dortgoz.config import settings
from dortgoz.events import OPERATOR_INCIDENT_PREFIX, Event, IncidentUpdate
from dortgoz.main import app
from dortgoz.services import triage


@pytest.fixture(autouse=True)
def isolated_runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runs_dir", tmp_path)


def _operator_payload(**overrides) -> IncidentUpdate:
    values = dict(
        incident_id=f"{OPERATOR_INCIDENT_PREFIX}test1",
        t=12.0,
        phase="sonuclandi",
        title="Operatör bildirimi",
        anomaly_type="hirsizlik",
        risk="yuksek",
        detail="Kasadan para alan kişi görüldü.",
        olay_baslangic=8.0,
        olay_bitis=16.0,
    )
    values.update(overrides)
    return IncidentUpdate(**values)


def test_report_missed_resolves_without_queue():
    store = triage.TriageStore()
    item = store.report_missed(
        feed="KAM-1", live=False, payload=_operator_payload(),
        event_id=None, run_id="run-1", video="video.mp4", reviewer="tester",
    )
    assert item.verdict == "anomali"
    assert item.source == "operator"
    assert item.model_category == "normal"
    assert item.operator_category == "hirsizlik"
    snapshot = store.snapshot()
    assert snapshot["pending"] == []
    assert [entry["incident_id"] for entry in snapshot["confirmed"]] == [item.incident_id]
    ledger = (settings.runs_dir / "nobet_defteri.jsonl").read_text(encoding="utf-8")
    assert json.loads(ledger.splitlines()[-1])["source"] == "operator"


def test_observe_skips_operator_incidents():
    store = triage.TriageStore()
    store.observe(Event.wrap(_operator_payload(), feed="KAM-1"))
    assert store.snapshot()["pending"] == []


def test_report_missed_requires_note():
    store = triage.TriageStore()
    with pytest.raises(ValueError):
        store.report_missed(
            feed="KAM-1", live=False, payload=_operator_payload(detail="  "),
            event_id=None, reviewer="tester",
        )


def test_report_endpoint_creates_confirmed_record():
    triage.store.clear()
    try:
        with TestClient(app) as client:
            response = client.post("/api/triage/report", json={
                "feed": "KAM-1", "live": False,
                "category": "kavga", "risk": "orta",
                "note": "İki kişi yumruklaşıyor, sistem sessiz kaldı.",
                "reviewer": "operator-test",
                "start": 30.0, "end": 45.0,
            })
            assert response.status_code == 200
            body = response.json()
            assert body["verdict"] == "anomali"
            assert body["source"] == "operator"
            assert body["incident_id"].startswith(OPERATOR_INCIDENT_PREFIX)
            snapshot = client.get("/api/triage").json()
            assert body["key"] in [entry["key"] for entry in snapshot["confirmed"]]
            assert snapshot["pending"] == []
    finally:
        triage.store.clear()


def test_report_endpoint_rejects_reversed_window():
    with TestClient(app) as client:
        response = client.post("/api/triage/report", json={
            "feed": "KAM-1", "live": False,
            "category": "kavga", "risk": "orta",
            "note": "geçersiz pencere",
            "reviewer": "operator-test",
            "start": 45.0, "end": 30.0,
        })
        assert response.status_code == 422
