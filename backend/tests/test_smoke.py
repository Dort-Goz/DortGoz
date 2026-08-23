import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dortgoz.events import Event
from dortgoz.main import app

UI_REPLAY = Path(__file__).parents[1] / "dortgoz" / "fixtures" / "ui_replay_events.jsonl"


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


@pytest.mark.parametrize(
    "url",
    [
        "/api/runs/..%5C..%5Csecret",
        "/api/runs/..%5C..%5Csecret/export",
    ],
)
def test_run_endpoints_reject_windows_path_traversal(url: str) -> None:
    with TestClient(app) as client:
        response = client.get(url)

    assert response.status_code == 404


def test_readiness_separates_local_components():
    with TestClient(app) as client:
        response = client.get("/ready")

    body = response.json()
    expected_status = 200 if all(
        component["ready"]
        for component in body["components"].values()
        if component["required"]
    ) else 503
    assert response.status_code == expected_status
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
    assert "VİDEO ANALİZ EDİLMEDİ" in joined
    assert "arayuz_test_akisi.mp4" not in joined
    assert '"type":"actuator_result"' not in joined
    assert '"status":"pending"' in joined
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
            ws.send_text(json.dumps({"kind": "sync", "from_seq": 0}))
            ws.send_text("{}")  # bozuk operatör frame'i bağlantıyı düşürmemeli
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


def test_import_rejects_corrupt_package():
    """Bozuk zip 500 değil 422 üretmeli — istemci hatası sunucu hatası değildir."""
    with TestClient(app) as client:
        r = client.post(
            "/api/runs/import",
            content=b"bu bir zip degil",
            headers={"content-type": "application/zip"},
        )

    assert r.status_code == 422


def test_import_rejects_oversized_package(monkeypatch):
    """Gövde sınırı aşılırsa 413 döner; paket diske hiç yazılmaz."""
    from dortgoz import main

    monkeypatch.setattr(main, "IMPORT_MAX_BYTES", 16)

    def fail_stage(_: bytes):  # pragma: no cover - çağrılmamalı
        raise AssertionError("sınırı aşan gövde geçici dosyaya yazılmamalı")

    monkeypatch.setattr(main, "_stage_import_package", fail_stage)
    with TestClient(app) as client:
        r = client.post(
            "/api/runs/import",
            content=b"x" * 512,
            headers={"content-type": "application/zip"},
        )

    assert r.status_code == 413


def test_import_runs_off_the_event_loop(monkeypatch):
    """İçe aktarma bloklayan işi thread'e taşımalı ve geçici dosyayı silmeli."""
    import asyncio

    from dortgoz.services import analysis_package

    on_event_loop: list[bool] = []
    staged: list[Path] = []

    def fake_import(path):
        staged.append(Path(path))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            on_event_loop.append(False)
        else:
            on_event_loop.append(True)
        raise ValueError("paket doğrulanamadı")

    monkeypatch.setattr(analysis_package, "import_analysis", fake_import)
    with TestClient(app) as client:
        r = client.post(
            "/api/runs/import",
            content=b"PK bozuk",
            headers={"content-type": "application/zip"},
        )

    assert r.status_code == 422
    assert on_event_loop == [False], "bloklayan içe aktarma event loop'ta koşmamalı"
    assert not staged[0].exists(), "geçici paket dosyası silinmeli"


def test_lifespan_runs_startup_reconciliation(monkeypatch):
    """Uzlaştırma `on_event` yerine lifespan'de de açılışta bir kez koşmalı."""
    from dortgoz import main

    calls: list[int] = []
    monkeypatch.setattr(
        main, "_reconcile_persistent_work_on_startup", lambda: calls.append(1)
    )
    with TestClient(app):
        pass

    assert calls == [1]


def test_lifespan_stops_live_cctv_on_shutdown(monkeypatch):
    """Kapanışta etkin canlı kip durdurulmalı — ffmpeg çekicileri kalmasın."""
    from dortgoz import main

    stopped: list[int] = []

    class FakeLive:
        active = True

        async def stop(self) -> None:
            stopped.append(1)

    monkeypatch.setattr(main, "live_cctv", FakeLive())
    with TestClient(app):
        assert stopped == []

    assert stopped == [1]


def test_triage_decide_maps_repository_error_to_conflict(monkeypatch):
    """Kalıcılık hatası 500 değil 409 olmalı; operatör kararı yeniden verebilir."""
    from dortgoz.repositories.errors import RepositoryConflictError
    from dortgoz.services import triage

    def boom(*args, **kwargs):
        raise RepositoryConflictError("review revizyonu çakıştı")

    monkeypatch.setattr(triage.store, "decide", boom)
    with TestClient(app) as client:
        r = client.post(
            "/api/triage/decide",
            json={
                "key": "kamera-1:olay-1",
                "verdict": "anomali",
                "category": "kavga",
                "risk_level": "yuksek",
                "start_time": 1.0,
                "peak_time": 2.0,
                "end_time": 3.0,
                "intervention_required": True,
            },
        )

    assert r.status_code == 409
