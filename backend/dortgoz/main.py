"""Dörtgöz backend — FastAPI uygulaması.

Tek süreç: REST + WebSocket + statik dosyalar (medya ve derlenmiş frontend).
Mock modda (DORTGOZ_MOCK=1) WS'e bağlanan ilk istemciye örnek senaryo akışı
yeniden oynatılır — GPU/model olmadan uçtan uca arayüz geliştirme.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import settings
from .events import ActuatorResult, ChatMessage, Event, OperatorMessage, RunStatus
from .ws import ConnectionManager, replay_jsonl

app = FastAPI(title="Dörtgöz", version="0.1.0")
manager = ConnectionManager()

MOCK_EVENTS = Path(__file__).parent / "mock" / "sample_events.jsonl"

# Akış başına bir koşu görevi; "" = tek-akış varsayılanı. Kuyruk/işçi
# altyapısı yine yok (A4) — çoklu-akış demo kipi yalnız eşzamanlı görevler.
# Üst sınır sunucu kapasitesinden: ~10 kamera @1× (5,8 GPU-sn/dk ölçümü).
_run_tasks: dict[str, asyncio.Task] = {}
MAX_FEEDS = 8


def _active_runs() -> int:
    return sum(1 for t in _run_tasks.values() if not t.done())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mock": settings.mock}


@app.get("/api/runs")
async def list_runs() -> list[str]:
    if not settings.runs_dir.exists():
        return []
    return sorted(p.stem for p in settings.runs_dir.glob("*.jsonl"))


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> list[dict]:
    """Kayıtlı koşunun olay akışı — arayüz yeniden bağlandığında geçmişi çeker."""
    from .pipeline.runner import load_run
    if not (settings.runs_dir / f"{run_id}.jsonl").is_file():
        raise HTTPException(status_code=404, detail="koşu bulunamadı")
    return load_run(run_id)


@app.get("/api/interpret_config")
async def interpret_config() -> dict:
    """Deney paneli verisi: seçilebilir modeller + varsayılan istemler.

    Model listesi model sunucusu `/v1/models`'ten canlı çekilir (erişim kapısı da
    bu yolu açık tutar); sunucuya ulaşılamazsa veya mock moddaysak liste
    varsayılan modelden ibaret kalır — panel yine çalışır.
    """
    from .pipeline.interpret import SYSTEM_TR, TASK_TR

    models = [settings.main_model]
    if not settings.mock:
        try:
            from .agent.llm import main_client
            page = await asyncio.wait_for(main_client().models.list(), timeout=5)
            ids = [m.id for m in page.data]
            if ids:
                models = ([settings.main_model] if settings.main_model not in ids else []) + ids
        except Exception:
            pass    # liste süsleme; varsayılanla devam
    return {
        "default_model": settings.main_model,
        "models": models,
        "system_prompt": SYSTEM_TR,
        "task_prompt": TASK_TR,
    }


@app.get("/api/videos")
async def list_videos() -> list[str]:
    """`/media` altındaki işlenebilir videolar — start_run bunlardan birini alır."""
    if not settings.media_dir.exists():
        return []
    return sorted(p.name for p in settings.media_dir.iterdir()
                  if p.suffix.lower() in {".mp4", ".mkv", ".avi", ".mov"})


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
    elif msg.kind == "start_run":
        await start_run(msg)
    elif msg.kind == "stop_run":
        await stop_run()


async def start_run(msg: OperatorMessage) -> None:
    """Video işleme hattını arka plan görevi olarak başlatır.

    HTTP/WS isteği içinde koşmaz (A4: uzun analiz istek döngüsünü bloklamamalı);
    ilerleme RunStatus, ara sonuçlar WindowReport olarak akar. Deney seçenekleri
    (model/istem override'ları) koşuya aynen taşınır.
    """
    feed = msg.feed
    task = _run_tasks.get(feed)
    if task and not task.done():
        await manager.broadcast(Event.wrap(RunStatus(
            run_id="-", state="error",
            detail=f"bu akışta süren bir koşu var" + (f" ({feed})" if feed else ""),
        ), feed=feed))
        return
    if _active_runs() >= MAX_FEEDS:
        await manager.broadcast(Event.wrap(RunStatus(
            run_id="-", state="error",
            detail=f"akış sınırı: aynı anda en çok {MAX_FEEDS} koşu",
        ), feed=feed))
        return

    from .pipeline.runner import run_video      # geç import: mock modda gerekmez

    run_id = f"{Path(msg.video).stem}-{uuid.uuid4().hex[:6]}"
    _run_tasks[feed] = asyncio.create_task(run_video(
        manager, msg.video, run_id,
        model=msg.model, system_prompt=msg.system_prompt, task_prompt=msg.task_prompt,
        feed=feed,
    ))


async def stop_run() -> None:
    """TÜM etkin koşuları durdurur — demo kipinde tek 'durdur' hepsini keser."""
    for task in list(_run_tasks.values()):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _run_tasks.clear()


# Statik servisler — medya ve (varsa) derlenmiş frontend
settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
