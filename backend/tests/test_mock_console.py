import asyncio

from fastapi.testclient import TestClient

from dortgoz.config import settings
from dortgoz.events import Event, IncidentUpdate
from dortgoz.main import app
from dortgoz.services.mock_console import MockLiveService, mock_chat, placeholder_frame
from dortgoz.ws import ConnectionManager


def test_mock_chat_streams_investigation_answer(monkeypatch):
    monkeypatch.setattr(settings, "mock_speed", 1000.0)
    manager = ConnectionManager()

    async def run():
        await manager.broadcast(Event.wrap(
            IncidentUpdate(
                incident_id="X-1", t=32, phase="basladi",
                title="Zorla giriş şüphesi", anomaly_type="hirsizlik", risk="yuksek",
            ),
            feed="kamera-1",
        ))
        await mock_chat(
            "Olayı aydınlat: Seçili olayda kim, kime veya neye, ne yaptı?",
            manager, dialogue_id="d1", feed="kamera-1",
        )

    asyncio.run(run())
    payloads = [event.payload for event in manager._history]
    steps = [p for p in payloads if p.type == "agent_step"]
    assert steps and all(step.dialogue_id == "d1" for step in steps)
    assert any(p.type == "tool_call" and p.tool == "olayi_aydinlat" for p in payloads)
    chunks = [p for p in payloads if p.type == "chat_message" and p.streaming]
    assert chunks
    text = "".join(chunk.text for chunk in chunks)
    assert "X-1" in text and "gerçek analiz değildir" in text
    assert any(
        p.type == "chat_message" and not p.streaming and p.text == "" and p.role == "agent"
        for p in payloads
    )


def test_mock_chat_without_incident(monkeypatch):
    monkeypatch.setattr(settings, "mock_speed", 1000.0)
    manager = ConnectionManager()
    answer = asyncio.run(mock_chat("durum nedir", manager, dialogue_id="d2"))
    assert "kayıtlı olay yok" in answer.casefold()


def test_mock_live_service_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mock_speed", 500.0)
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    manager = ConnectionManager()
    service = MockLiveService(manager)

    async def run():
        statuses = await service.start()
        assert service.active and statuses
        assert all(status.snapshot for status in statuses)
        await asyncio.sleep(0.3)
        await service.stop()

    asyncio.run(run())
    assert not service.active
    assert service.status() == []
    assert list(tmp_path.glob("canli-mock/*/latest.svg"))
    types = {event.payload.type for event in manager._history}
    assert "incident_update" in types and "run_status" in types


def test_live_endpoints_in_mock(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mock", True)
    monkeypatch.setattr(settings, "mock_speed", 500.0)
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/live/start", json={})
        assert r.status_code == 200 and r.json()
        status = client.get("/api/live/status").json()
        assert status["active"] and status["feeds"]
        assert client.post("/api/live/stop").status_code == 200
        assert client.get("/api/live/status").json()["active"] is False


def test_placeholder_frame_is_svg():
    body = placeholder_frame(12.5)
    assert body.startswith(b"<svg") and b"12.5" in body
