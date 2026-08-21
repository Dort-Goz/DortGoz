import json
from pathlib import Path

from fastapi.testclient import TestClient

from dortgoz.events import Event
from dortgoz.main import app

UI_REPLAY = Path(__file__).parents[1] / "dortgoz" / "fixtures" / "ui_replay_events.jsonl"


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_readiness_separates_local_components():
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["components"]["storage"]["ready"] is True
    assert body["components"]["video_store"]["mode"] == "memory"
    assert body["components"]["model"]["mode"] == "local_vlm"


def test_ui_replay_events_validate_against_contract():
    lines = [
        line
        for line in UI_REPLAY.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(lines) >= 10
    joined = "\n".join(lines)
    assert "forklift" not in joined.casefold()
    assert "RTMPose" not in joined
    assert "MiniCPM" not in joined
    assert "VİDEO ANALİZİ ÇALIŞTIRILMADI" in joined
    assert '"delivered":false' in joined
    for line in lines:
        raw = json.loads(line)
        raw.pop("delay", None)
        Event.model_validate(raw)


def test_websocket_chat_roundtrip(monkeypatch):
    from dortgoz.config import settings
    monkeypatch.setattr(settings, "mock", True)
    monkeypatch.setattr(settings, "mock_speed", 1000.0)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"kind": "chat", "text": "test sorusu"}))
            got_operator_echo = got_agent_reply = False
            for _ in range(60):
                ev = Event.model_validate(ws.receive_json())
                if ev.payload.type == "chat_message":
                    if ev.payload.role == "operator" and "test sorusu" in ev.payload.text:
                        got_operator_echo = True
                    if ev.payload.role == "agent" and got_operator_echo:
                        got_agent_reply = True
                        break
            assert got_operator_echo and got_agent_reply


def test_interpret_config_mock(monkeypatch):
    from dortgoz.config import settings
    monkeypatch.setattr(settings, "mock", True)
    with TestClient(app) as client:
        r = client.get("/api/interpret_config")
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["default_model"] in cfg["models"]
        assert "{start}" in cfg["task_prompt"] and "{end}" in cfg["task_prompt"]
        assert len(cfg["system_prompt"]) > 50


def test_broadcast_survives_a_stalled_client():
    import asyncio

    from dortgoz.events import ChatMessage, Event
    from dortgoz.ws import ConnectionManager

    class Stalled:
        async def send_text(self, _):
            await asyncio.sleep(3600)

    class Good:
        def __init__(self): self.got = []
        async def send_text(self, d): self.got.append(d)

    mgr = ConnectionManager()
    mgr.SEND_TIMEOUT = 0.05
    stalled, good = Stalled(), Good()
    mgr._connections.update({stalled, good})

    async def run():
        await asyncio.wait_for(
            mgr.broadcast(Event.wrap(ChatMessage(role="agent", text="x"))), timeout=5)

    asyncio.run(run())
    assert good.got, "sağlıklı istemci mesajı almalı"
    assert stalled not in mgr._connections, "askıda kalan istemci düşürülmeli"
