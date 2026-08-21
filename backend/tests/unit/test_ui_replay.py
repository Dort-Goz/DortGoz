import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from dortgoz import main
from dortgoz.config import settings


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


def test_ui_replay_starts_once_per_process(monkeypatch):
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
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"kind": "chat", "text": "iki"}))
            ws.receive_json()

    assert calls == 1
    main._ui_replay_task = None
