"""Dörtgöz backend — FastAPI uygulaması.

Tek süreç: REST + WebSocket + statik dosyalar (medya ve derlenmiş frontend).
Mock modda (DORTGOZ_MOCK=1) WS'e bağlanan ilk istemciye örnek senaryo akışı
yeniden oynatılır — GPU/model olmadan uçtan uca arayüz geliştirme.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.errors import (
    domain_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from .api.router import router as api_router
from .api.router import runtime as api_runtime
from .config import settings
from .domain.video import VideoIngestError
from .events import ActuatorResult, ChatMessage, Event, OperatorMessage, RunStatus
from .repositories.errors import (
    RepositoryConflictError,
    RepositoryDuplicateError,
    RepositoryError,
    RepositoryNotFoundError,
)
from .services.analysis_job import (
    AnalysisJobExecutionDisabled,
    AnalysisJobStartError,
    CanonicalAnalysisJobService,
)
from .ws import ConnectionManager, replay_jsonl

app = FastAPI(title="Dörtgöz", version="0.1.0")
manager = ConnectionManager()

app.include_router(api_router)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
for _error_type in (
    VideoIngestError,
    RepositoryError,
    RepositoryNotFoundError,
    RepositoryDuplicateError,
    RepositoryConflictError,
):
    app.add_exception_handler(_error_type, domain_exception_handler)
app.add_exception_handler(Exception, domain_exception_handler)

MOCK_EVENTS = Path(__file__).parent / "mock" / "sample_events.jsonl"

# Sınır = şartnamedeki 24 kamera senaryosu. Ölçülen kapasite ~10 kamera @1×
# (5,8 GPU-sn/dk): 24 akışta hız rozetleri 1×'in ALTINI gösterir — bu bilinçli,
# sistem yavaşlar ama düşmez; RunStatus.speed dürüst ölçümü taşır.
MAX_FEEDS = 24
analysis_jobs = CanonicalAnalysisJobService(
    manager,
    runs_dir=settings.runs_dir,
    max_active=MAX_FEEDS,
    enabled=lambda: not settings.mock,
)
# REST migration Patch B'de yapılacak; tek instance şimdiden app composition
# sınırında görünür ki router daha sonra circular import olmadan kullanabilsin.
app.state.analysis_jobs = analysis_jobs


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mock": settings.mock}


@app.get("/ready")
async def readiness() -> JSONResponse:
    """Yerel deployment bağımlılıklarını ayrı ayrı gösteren hazır olma kapısı.

    Bu uç model endpoint'ine ağ isteği yapmaz: air-gapped ortamda yanlışlıkla dış
    egress başlatmak yerine manifest/yapılandırma hazırlığını raporlar. Gerçek
    profil, ilk candidate çağrısında ayrıca dosya hash'ini denetler.
    """

    storage_ready = True
    storage_detail = "ok"
    try:
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        storage_ready = False
        storage_detail = f"{type(exc).__name__}: {exc}"

    event_store_path = settings.event_store_path
    event_store = {
        "ready": True,
        "mode": getattr(api_runtime.repository, "persistence_mode", "memory"),
        "path": str(event_store_path) if event_store_path is not None else None,
    }
    if settings.mock:
        model = {"ready": True, "mode": "mock", "endpoint_checked": False}
    elif settings.vlm_manifest_path is None:
        model = {
            "ready": False,
            "mode": "local_vlm",
            "detail": "DORTGOZ_VLM_MANIFEST_PATH ayarlanmadı",
            "endpoint_checked": False,
        }
    else:
        model = {
            "ready": settings.vlm_manifest_path.is_file(),
            "mode": "local_vlm",
            "manifest_path": str(settings.vlm_manifest_path),
            "endpoint_checked": False,
        }
    components = {
        "storage": {"ready": storage_ready, "detail": storage_detail},
        "event_store": event_store,
        "model": model,
    }
    ready = all(component["ready"] for component in components.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "components": components},
    )


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
            pass  # liste süsleme; varsayılanla devam
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
    return sorted(
        p.name
        for p in settings.media_dir.iterdir()
        if p.suffix.lower() in {".mp4", ".mkv", ".avi", ".mov"}
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    replay_task: asyncio.Task | None = None
    if settings.mock:
        replay_task = asyncio.create_task(replay_jsonl(manager, MOCK_EVENTS, settings.mock_speed))
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
            await manager.broadcast(
                Event.wrap(
                    ChatMessage(
                        role="agent",
                        text=f"(mock) Sorunuz alındı: '{msg.text}'. Gerçek modda bu yanıt "
                        f"ajan grafiğinden gelir.",
                    )
                )
            )
        else:
            from .agent.graph import run_chat  # geç import: mock modda langgraph gerekmez

            await run_chat(msg.text, manager)
    elif msg.kind == "actuator_response":
        await manager.broadcast(
            Event.wrap(
                ActuatorResult(
                    request_id=msg.request_id,
                    actuator="?",
                    approved=msg.approved,
                    detail="Operatör kararı",
                )
            )
        )
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
    jobs: CanonicalAnalysisJobService = app.state.analysis_jobs
    try:
        await jobs.start(
            msg.video,
            model=msg.model,
            system_prompt=msg.system_prompt,
            task_prompt=msg.task_prompt,
            feed=msg.feed,
        )
    except AnalysisJobExecutionDisabled:
        # Mock WS bağlantısı fixture'ı zaten oynatıyor. start_run'ın ayrıca gerçek
        # runner/model yolunu açması çift akış ve GPU erişimi üretmemeli.
        return
    except AnalysisJobStartError as exc:
        await manager.broadcast(
            Event.wrap(RunStatus(run_id="-", state="error", detail=str(exc))),
            feed=msg.feed,
        )


async def stop_run() -> None:
    """TÜM etkin koşuları durdurur — demo kipinde tek 'durdur' hepsini keser."""
    jobs: CanonicalAnalysisJobService = app.state.analysis_jobs
    await jobs.cancel_all()


# Statik servisler — medya ve (varsa) derlenmiş frontend
settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
