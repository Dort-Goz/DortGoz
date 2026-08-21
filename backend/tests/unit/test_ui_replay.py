import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from dortgoz import main
from dortgoz.config import settings
from dortgoz.events import Event, OperatorMessage, RunStatus


@pytest.mark.asyncio
async def test_failed_ui_replay_is_observed_and_can_retry(caplog):
    async def fail():
        raise RuntimeError("bozuk fixture")

    task = asyncio.create_task(fail())
    main._ui_replay_task = task
    with caplog.at_level(logging.ERROR, logger=main.__name__):
        await asyncio.sleep(0)
        main._observe_ui_replay(task)

    assert main._ui_replay_task is None
    assert "UI replay akışı başarısız oldu" in caplog.text


@pytest.mark.asyncio
async def test_completed_ui_replay_can_start_again():
    task = asyncio.create_task(asyncio.sleep(0))
    main._ui_replay_task = task
    await task
    main._observe_ui_replay(task)

    assert main._ui_replay_task is None


def test_websocket_connection_does_not_autostart_ui_replay(monkeypatch):
    calls = 0

    async def replay_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(settings, "mock", True)
    monkeypatch.setattr(main, "replay_jsonl", replay_once)
    main._ui_replay_task = None

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"kind": "chat", "text": "bir"}))
            ws.receive_json()
            ws.receive_json()

    assert calls == 0
    main._ui_replay_task = None


@pytest.mark.asyncio
async def test_start_replays_selected_video_and_allows_restart(monkeypatch, tmp_path):
    video = tmp_path / "crime.mp4"
    video.write_bytes(b"video")
    transformed = []

    async def replay_once(_manager, _path, _speed, *, transform):
        transformed.append(transform(Event.wrap(
            RunStatus(run_id="fixture-ui-crime", state="processing")
        )))

    monkeypatch.setattr(settings, "mock", True)
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    monkeypatch.setattr(main, "replay_jsonl", replay_once)
    main._ui_replay_task = None
    message = OperatorMessage(kind="start_run", video=video.name, feed="KAM-TEST")

    await main.start_run(message)
    assert main._ui_replay_task is not None
    await main._ui_replay_task
    await asyncio.sleep(0)
    await main.start_run(message)
    assert main._ui_replay_task is not None
    await main._ui_replay_task
    await asyncio.sleep(0)

    assert len(transformed) == 2
    assert all(event.feed == "KAM-TEST" for event in transformed)
    assert all(event.payload.video == video.name for event in transformed)
    assert all(event.payload.run_id.startswith("fixture-ui-crime-") for event in transformed)
    assert transformed[0].payload.run_id != transformed[1].payload.run_id
    assert main._ui_replay_task is None


@pytest.mark.asyncio
async def test_start_rejects_video_outside_media(monkeypatch, tmp_path):
    class CaptureManager:
        def __init__(self):
            self.events = []

        async def broadcast(self, event):
            self.events.append(event)

    capture = CaptureManager()
    monkeypatch.setattr(settings, "mock", True)
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    monkeypatch.setattr(main, "manager", capture)
    main._ui_replay_task = None

    await main.start_run(OperatorMessage(kind="start_run", video="../crime.mp4"))

    assert main._ui_replay_task is None
    assert capture.events[-1].payload.state == "error"
    assert "media/" in capture.events[-1].payload.detail
