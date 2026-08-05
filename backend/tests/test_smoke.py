"""Duman testi: uygulama ayağa kalkıyor, sözleşme tutarlı, mock akış çalışıyor."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from dortgoz.events import Event
from dortgoz.main import app

MOCK = Path(__file__).parents[1] / "dortgoz" / "mock" / "sample_events.jsonl"


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_mock_events_validate_against_contract():
    """Mock akıştaki her satır Event şemasına uymalı — sözleşme bozulursa burada kırılır."""
    lines = [l for l in MOCK.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    assert len(lines) >= 10
    for line in lines:
        raw = json.loads(line)
        raw.pop("delay", None)
        Event.model_validate(raw)


def test_websocket_chat_roundtrip(monkeypatch):
    """Mock modda: operatör chat mesajı yankı + ajan yanıtı üretmeli."""
    from dortgoz.config import settings
    monkeypatch.setattr(settings, "mock", True)
    monkeypatch.setattr(settings, "mock_speed", 1000.0)  # replay'i hızlandır
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
    """Deney paneli verisi mock modda da (model sunucusu'sız) çalışmalı."""
    from dortgoz.config import settings
    monkeypatch.setattr(settings, "mock", True)
    with TestClient(app) as client:
        r = client.get("/api/interpret_config")
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["default_model"] in cfg["models"]
        assert "{start}" in cfg["task_prompt"] and "{end}" in cfg["task_prompt"]
        assert len(cfg["system_prompt"]) > 50
