import asyncio

import pytest
from fastapi.testclient import TestClient

from dortgoz import main, session
from dortgoz.agent.memory import Incident
from dortgoz.config import settings
from dortgoz.events import OperatorMessage
from dortgoz.services import triage
from dortgoz.services.action_dispatcher import dispatcher


class FakeManager:
    def __init__(self) -> None:
        self.events = []

    async def broadcast(self, event) -> None:
        self.events.append(event)


@pytest.fixture()
def pending_action(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runs_dir", tmp_path / "runs")
    monkeypatch.setattr(settings, "mock", False)
    dispatcher.reset_memory()
    session.clear()
    triage.store.clear()
    ctx = session.start("run-api", "crime.mp4", feed="KAM-1")
    incident = Incident(
        incident_id="inc-api",
        title="Saldırı şüphesi",
        first_seen=5.0,
        last_seen=8.0,
        anomaly_type="saldiri",
        risk="kritik",
        evidence_ts=[5.0, 7.5],
    )
    ctx.ledger.incidents[incident.incident_id] = incident
    request, _ = dispatcher.request(
        "emniyet_bildirimi_hazirla",
        incident.incident_id,
        ctx.feed,
        "kritik olay",
    )
    yield request
    dispatcher.reset_memory()
    session.clear()
    triage.store.clear()


def test_operator_response_resolves_exact_request(pending_action, monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(main, "manager", manager)

    asyncio.run(main.handle_operator_message(OperatorMessage(
        kind="actuator_response",
        request_id=pending_action.request_id,
        approved=True,
        operator="Operatör 1",
    )))

    result = manager.events[-1].payload
    assert result.request_id == pending_action.request_id
    assert result.actuator == "emniyet_bildirimi_hazirla"
    assert result.status == "prepared"
    assert result.delivered is False
    assert result.external_side_effect is False


def test_unknown_operator_response_is_rejected_without_result(pending_action, monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(main, "manager", manager)

    asyncio.run(main.handle_operator_message(OperatorMessage(
        kind="actuator_response",
        request_id="missing",
        approved=True,
    )))

    payload = manager.events[-1].payload
    assert payload.type == "chat_message"
    assert "reddedildi" in payload.text


def test_action_snapshot_and_artifact_routes(pending_action):
    dispatcher.resolve(pending_action.request_id, True, "Operatör 1")

    with TestClient(main.app) as client:
        snapshot = client.get("/api/actions")
        artifact = client.get(f"/api/actions/{pending_action.request_id}/artifact")

    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["requests"][0]["request_id"] == pending_action.request_id
    assert body["results"][0]["delivered"] is False
    assert artifact.status_code == 200
    assert "Dış kuruma iletilmedi" in artifact.text


def test_health_separates_analysis_from_external_delivery(monkeypatch):
    monkeypatch.setattr(settings, "mock", False)
    with TestClient(main.app) as client:
        body = client.get("/health").json()
    assert body["analysis_mode"] == "evren_video_analysis"
    assert body["external_delivery"] is False
