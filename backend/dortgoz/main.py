"""Dörtgöz backend — FastAPI uygulaması.

Tek süreç: REST + WebSocket + statik dosyalar (medya ve derlenmiş frontend).
Mock modda (DORTGOZ_MOCK=1) WS'e bağlanan ilk istemciye örnek senaryo akışı
yeniden oynatılır — GPU/model olmadan uçtan uca arayüz geliştirme.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
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

# Sınır = şartnamedeki 24 kamera senaryosu (+1 pay 5×5 canlı ızgara). Prova
# ölçümü (2026-08-14, 24 akış × 20 dk gerçekçi kayıt): 24/24 tamamlandı, hız
# medyanı 0,85× — sistem yavaşlar ama düşmez, RunStatus.speed dürüst ölçümü
# taşır. Gerçekçi içerikte ~17 akış @1×; olay-yoğun en kötü durumda ~10
# (bench/kapasite_provasi.py). DORTGOZ_MAX_FEEDS ile ayarlanır.
analysis_jobs = CanonicalAnalysisJobService(
    manager,
    runs_dir=settings.runs_dir,
    max_active=settings.max_feeds,
    enabled=lambda: not settings.mock,
    finalize_run=api_runtime.incident_media.finalize_analysis,
)
# REST ve WS bu app composition sınırındaki aynı canonical job instance'ını kullanır;
# router servisi ``app.state`` üzerinden alır ve ``main`` modülünü import etmez.
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


@app.get("/api/runs/{run_id}/export")
async def export_run(run_id: str) -> FileResponse:
    """Analizi taşınabilir pakete (zip) çıkarır: akış + meta + özet + video +
    kanıt kareleri. Paket başka bir Dörtgöz kurulumuna içe alındığında ajan
    sohbeti tam yetenekle çalışır."""
    from .services.analysis_package import export_with_evidence

    try:
        pkg = await export_with_evidence(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="koşu bulunamadı")
    return FileResponse(pkg, filename=pkg.name, media_type="application/zip")


@app.post("/api/runs/import")
async def import_run(request: Request) -> dict:
    """Dışa aktarılmış paketi (zip, ham gövde) geri yükler.

    Gövde `application/zip` olarak POST edilir (multipart bağımlılığı yok).
    Başarıda oturum bağlamı kurulur — sohbet içe alınan analiz üzerinde çalışır.
    """
    import tempfile

    from .services.analysis_package import import_analysis

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="boş paket gövdesi")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        ctx = import_analysis(tmp_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"run_id": ctx.run_id, "video": ctx.video, "verdict": ctx.verdict(),
            "incidents": len(ctx.incidents), "reports": len(ctx.reports)}


# ---- Canlı CCTV kipi (services/live_cctv) ----
from .services.live_cctv import LiveCctvService, load_feeds  # noqa: E402

live_cctv = LiveCctvService(manager)
app.state.live_cctv = live_cctv

# ---- Anomali nöbet kuyruğu (services/triage): insan-döngüde karar katmanı ----
from .services import triage  # noqa: E402
from .services.analysis_projection import RuntimeAnalysisProjection  # noqa: E402

runtime_projection = RuntimeAnalysisProjection(
    api_runtime.repository,
    settings.runs_dir,
    allow_virtual_sources=settings.mock,
)
live_cctv.prepare_run = runtime_projection.register_runtime_source
live_cctv.finalize_run = api_runtime.incident_media.finalize_analysis
# Projection önce çalışır. Böylece nöbet kartı aynı yayın turunda kalıcı event_id
# alır ve operatör kararı JSONL yerine canonical SQLite review'a bağlanır.
triage.store.configure(
    api_runtime.repository,
    api_runtime.events,
    runtime_projection.event_id_for,
)
manager.observers.append(runtime_projection.observe)
manager.observers.append(triage.store.observe)


@app.get("/api/triage")
async def triage_snapshot() -> dict:
    """Nöbet kuyruğu: bekleyen olaylar + bu oturumda doğrulanan anomaliler."""
    return triage.store.snapshot()


@app.post("/api/triage/rules/{proposal_id}/approve")
async def triage_approve_rule(proposal_id: str, body: dict) -> dict:
    """Öneriyi süreli etkinleştir; ayrı operatör kararı zorunludur."""
    try:
        triage.store.approve_rule(
            proposal_id,
            body.get("reviewer", "operator-console"),
            int(body.get("duration_hours", triage.DEFAULT_RULE_HOURS)),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return triage.store.snapshot()


@app.post("/api/triage/rules/{proposal_id}/reject")
async def triage_reject_rule(proposal_id: str, body: dict) -> dict:
    """Kural önerisini uygulatmadan reddet."""
    try:
        triage.store.reject_rule(
            proposal_id, body.get("reviewer", "operator-console")
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return triage.store.snapshot()


@app.post("/api/triage/rules/{proposal_id}/revoke")
async def triage_revoke_rule(proposal_id: str, body: dict) -> dict:
    """Toplanan, önerilen veya etkin kuralı geri al."""
    try:
        triage.store.revoke_rule(
            proposal_id, body.get("reviewer", "operator-console")
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return triage.store.snapshot()


@app.post("/api/triage/decide")
async def triage_decide(body: dict) -> dict:
    """Operatör kararı: {key, verdict: anomali|sorun_degil, category?, note?}."""
    try:
        item = triage.store.decide(
            body.get("key", ""), body.get("verdict", ""),
            category=body.get("category", ""), note=body.get("note", ""),
            reviewer=body.get("reviewer", "operator-console"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except triage.TriagePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    from dataclasses import asdict
    return asdict(item)


@app.post("/api/live/start")
async def live_start(body: dict | None = None) -> list[dict]:
    """Canlı kip: config/live_feeds.json'daki akışları çekmeye + işlemeye başlar."""
    if settings.mock:
        raise HTTPException(status_code=409, detail="mock kipte canlı akış çekilmez")
    try:
        statuses = await live_cctv.start(mode=(body or {}).get("mode", ""))
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return [vars(s) for s in statuses]


@app.post("/api/live/stop")
async def live_stop() -> dict:
    await live_cctv.stop()
    return {"status": "durdu"}


@app.get("/api/live/status")
async def live_status() -> dict:
    """Izgaranın nabzı: akış başına durum + gecikme + anlık görüntü URL'si."""
    return {"active": live_cctv.active,
            "feeds": [vars(s) for s in live_cctv.status()]}


@app.get("/api/live/feeds")
async def live_feed_list() -> list[dict]:
    """Yapılandırılmış akış listesi (başlatmadan önizleme)."""
    try:
        return load_feeds()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.on_event("shutdown")
async def _stop_live_on_shutdown() -> None:
    """7/24 temiz kapanış: ffmpeg çekicileri süreçle birlikte ölsün."""
    if live_cctv.active:
        await live_cctv.stop()


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
            mode=msg.mode,
        )
    except AnalysisJobExecutionDisabled:
        # Mock WS bağlantısı fixture'ı zaten oynatıyor. start_run'ın ayrıca gerçek
        # runner/model yolunu açması çift akış ve GPU erişimi üretmemeli. Sessiz
        # dönüş UI'daki başlatma kilidini süresiz bırakıyordu (2026-08-11 bulgu
        # A1) — durum yayını kilidi çözer.
        await manager.broadcast(
            Event.wrap(
                RunStatus(run_id="-", state="idle",
                          detail="Mock kipte gerçek analiz başlatılmaz — kayıt zaten oynuyor."),
                feed=msg.feed,
            )
        )
        return
    except AnalysisJobStartError as exc:
        # `feed` Event.wrap'ın parametresi; broadcast'e geçirilirse TypeError
        # bağlantıyı düşürüyordu (2026-08-11 bulgu BUG-1).
        await manager.broadcast(
            Event.wrap(RunStatus(run_id="-", state="error", detail=str(exc)),
                       feed=msg.feed)
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
