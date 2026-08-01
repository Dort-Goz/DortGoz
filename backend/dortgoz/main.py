"""Dörtgöz backend — FastAPI uygulaması.

Tek süreç: REST + WebSocket + statik dosyalar (medya ve derlenmiş frontend).
Mock modda (DORTGOZ_MOCK=1) WS'e bağlanan ilk istemciye örnek senaryo akışı
yeniden oynatılır — GPU/model olmadan uçtan uca arayüz geliştirme.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import settings
from .events import ActuatorResult, ChatMessage, Event, OperatorMessage
from .ws import ConnectionManager, replay_jsonl

app = FastAPI(title="Dörtgöz", version="0.1.0")
manager = ConnectionManager()

MOCK_EVENTS = Path(__file__).parent / "mock" / "sample_events.jsonl"


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mock": settings.mock}


@app.get("/api/runs")
async def list_runs() -> list[str]:
    if not settings.runs_dir.exists():
        return []
    return sorted(p.stem for p in settings.runs_dir.glob("*.jsonl"))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    replay_task: asyncio.Task | None = None
    if settings.mock:
        replay_task = asyncio.create_task(
            replay_jsonl(manager, MOCK_EVENTS, settings.mock_speed)
        )
    try:
        while True:
            raw = await ws.receive_text()
            msg = OperatorMessage.model_validate_json(raw)
            await handle_operator_message(msg)
    except WebSocketDisconnect:
        manager.disconnect(ws)
    finally:
        if replay_task and not replay_task.done():
            replay_task.cancel()


async def handle_operator_message(msg: OperatorMessage) -> None:
    """Operatör mesajlarını yönlendir.

    Gerçek modda `chat` ajan grafiğine gider (agent.graph.run_chat);
    mock modda basit yankı ile arayüz sözleşmesi doğrulanır.
    """
    if msg.kind == "chat":
        await manager.broadcast(Event.wrap(ChatMessage(role="operator", text=msg.text)))
        if settings.mock:
            await manager.broadcast(Event.wrap(ChatMessage(
                role="agent",
                text=f"(mock) Sorunuz alındı: '{msg.text}'. Gerçek modda bu yanıt "
                     f"ajan grafiğinden gelir.",
            )))
        else:
            from .agent.graph import run_chat  # geç import: mock modda langgraph gerekmez
            await run_chat(msg.text, manager)
    elif msg.kind == "actuator_response":
        await manager.broadcast(Event.wrap(ActuatorResult(
            request_id=msg.request_id,
            actuator="?",
            approved=msg.approved,
            detail="Operatör kararı",
        )))
    # start_run / stop_run: gerçek işleme hattı bağlanınca doldurulacak (pipeline/)


# Statik servisler — medya ve (varsa) derlenmiş frontend
settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
