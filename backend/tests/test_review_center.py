from __future__ import annotations

from fastapi.testclient import TestClient

from dortgoz import main, session
from dortgoz.agent.memory import Incident
from dortgoz.config import settings
from dortgoz.events import Event, EventEvidenceRef, IncidentUpdate, RunStatus
from dortgoz.services import triage
from dortgoz.services.action_dispatcher import dispatcher


def _confirmed_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "runs_dir", tmp_path / "runs")
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "mock", False)
    settings.media_dir.mkdir(parents=True)
    (settings.media_dir / "crime.mp4").write_bytes(b"video")
    dispatcher.reset_memory()
    session.clear()
    test_store = triage.TriageStore(allow_ledger_only=True)
    monkeypatch.setattr(triage, "store", test_store)
    ctx = session.start("run-review", "crime.mp4", feed="KAM-1")
    incident = Incident(
        incident_id="inc-review",
        title="Hırsızlık şüphesi",
        first_seen=12.0,
        last_seen=14.0,
        anomaly_type="hirsizlik",
        risk="yuksek",
        evidence_ts=[12.0, 14.0],
        evidence_refs=[EventEvidenceRef(
            frame_id="f_001",
            timestamp=12.0,
            claim="Bir kişi ürünü kıyafetinin içine yerleştiriyor.",
        )],
        needs_review=True,
    )
    ctx.ledger.incidents[incident.incident_id] = incident
    triage.store.observe(Event.wrap(
        RunStatus(run_id=ctx.run_id, state="done", video=ctx.video),
        feed=ctx.feed,
    ))
    triage.store.observe(Event.wrap(
        IncidentUpdate(
            incident_id=incident.incident_id,
            t=13.0,
            phase="sonuclandi",
            title=incident.title,
            anomaly_type=incident.anomaly_type,
            risk=incident.risk,
            needs_review=True,
        ),
        feed=ctx.feed,
    ))
    triage.store.decide(
        "KAM-1:inc-review",
        "anomali",
        category="hirsizlik",
        reviewer="Operatör 1",
    )


def test_review_center_serves_only_incident_bound_evidence_frame(tmp_path, monkeypatch):
    _confirmed_review(tmp_path, monkeypatch)
    calls = []

    async def fake_grab_frame(path, timestamp, width=512):
        calls.append((path.name, timestamp, width))
        return b"jpeg-bytes"

    monkeypatch.setattr("dortgoz.pipeline.ingest.grab_frame", fake_grab_frame)
    try:
        with TestClient(main.app) as client:
            valid = client.get("/api/triage/evidence-frame", params={
                "key": "KAM-1:inc-review",
                "timestamp": 12.0,
            })
            unknown = client.get("/api/triage/evidence-frame", params={
                "key": "KAM-1:inc-review",
                "timestamp": 99.0,
            })

        assert valid.status_code == 200
        assert valid.headers["content-type"] == "image/jpeg"
        assert valid.content == b"jpeg-bytes"
        assert calls == [("crime.mp4", 12.0, 480)]
        assert unknown.status_code == 404
    finally:
        dispatcher.reset_memory()
        session.clear()
        triage.store.clear()


def test_review_center_creates_pending_local_draft_request(tmp_path, monkeypatch):
    _confirmed_review(tmp_path, monkeypatch)

    class FakeManager:
        def __init__(self):
            self.events = []

        async def broadcast(self, event):
            self.events.append(event)

    manager = FakeManager()
    monkeypatch.setattr(main, "manager", manager)
    try:
        with TestClient(main.app) as client:
            response = client.post("/api/actions/request", json={
                "action": "emniyet_bildirimi_hazirla",
                "incident_id": "inc-review",
                "feed": "KAM-1",
            })

        assert response.status_code == 200
        body = response.json()
        assert body["created"] is True
        assert body["request"]["status"] == "pending"
        assert manager.events[-1].payload.type == "actuator_request"
        assert dispatcher.snapshot(fixture_only=False)["results"] == []
    finally:
        dispatcher.reset_memory()
        session.clear()
        triage.store.clear()
