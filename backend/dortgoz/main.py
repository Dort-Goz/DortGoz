from __future__ import annotations

import asyncio
import logging
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
from .errors import (
    RepositoryConflictError,
    RepositoryDuplicateError,
    RepositoryError,
    RepositoryNotFoundError,
)
from .events import ChatMessage, Event, OperatorMessage, RunStatus
from .infrastructure import vlm_manifest
from .services.action_dispatcher import dispatcher as action_dispatcher
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

UI_REPLAY_EVENTS = Path(__file__).parent / "fixtures" / "ui_replay_events.jsonl"
_ui_replay_task: asyncio.Task[None] | None = None
LOGGER = logging.getLogger(__name__)


def _observe_ui_replay(task: asyncio.Task[None]) -> None:
    global _ui_replay_task
    try:
        task.result()
    except asyncio.CancelledError:
        _ui_replay_task = None
    except Exception:
        LOGGER.exception("UI replay akışı başarısız oldu")
        _ui_replay_task = None

analysis_jobs = CanonicalAnalysisJobService(
    manager,
    runs_dir=settings.runs_dir,
    max_active=settings.max_feeds,
    enabled=lambda: not settings.mock,
)
app.state.analysis_jobs = analysis_jobs


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mock": settings.mock,
        "analysis_mode": "ui_fixture_replay" if settings.mock else "real_local_analysis",
        "external_delivery": False,
    }


@app.get("/ready")
async def readiness() -> JSONResponse:

    storage_ready = True
    storage_detail = "ok"
    try:
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        storage_ready = False
        storage_detail = f"{type(exc).__name__}: {exc}"

    video_store_path = settings.video_store_path
    video_store = {
        "ready": True,
        "mode": getattr(api_runtime.repository, "persistence_mode", "memory"),
        "path": str(video_store_path) if video_store_path is not None else None,
    }
    if settings.mock:
        model = {
            "ready": True,
            "mode": "ui_fixture_replay",
            "endpoint_checked": False,
        }
    else:
        model = vlm_manifest.readiness(settings.vlm_manifest_path)
    components = {
        "storage": {"ready": storage_ready, "detail": storage_detail},
        "video_store": video_store,
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
    from .pipeline.runner import load_run

    if not (settings.runs_dir / f"{run_id}.jsonl").is_file():
        raise HTTPException(status_code=404, detail="koşu bulunamadı")
    return load_run(run_id)


@app.get("/api/runs/{run_id}/export")
async def export_run(run_id: str) -> FileResponse:
    from .services.analysis_package import export_with_evidence

    try:
        pkg = await export_with_evidence(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="koşu bulunamadı")
    return FileResponse(pkg, filename=pkg.name, media_type="application/zip")


@app.post("/api/runs/import")
async def import_run(request: Request) -> dict:
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


from .services.live_cctv import LiveCctvService, load_feeds  # noqa: E402

live_cctv = LiveCctvService(manager)
app.state.live_cctv = live_cctv

from .services import triage  # noqa: E402

manager.observers.append(triage.store.observe)


@app.get("/api/triage")
async def triage_snapshot() -> dict:
    return triage.store.snapshot()


@app.post("/api/triage/rule_sil")
async def triage_revoke_rule(body: dict) -> dict:
    triage.store.revoke_rule(body.get("feed", ""), body.get("category", ""))
    return triage.store.snapshot()


def _triage_args(body: dict) -> dict:
    return {
        "category": body.get("category", ""),
        "note": body.get("note", ""),
        "reviewer": body.get("reviewer", ""),
        "operator_start": body.get("operator_start"),
        "operator_end": body.get("operator_end"),
    }


@app.post("/api/triage/decide")
async def triage_decide(body: dict) -> dict:
    try:
        item = triage.store.decide(
            body.get("key", ""), body.get("verdict", ""), **_triage_args(body))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    from dataclasses import asdict
    return asdict(item)


@app.post("/api/triage/duzelt")
async def triage_revise(body: dict) -> dict:
    try:
        item = triage.store.revise(
            body.get("key", ""), body.get("verdict", ""), **_triage_args(body))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    from dataclasses import asdict
    return asdict(item)


@app.post("/api/live/start")
async def live_start(body: dict | None = None) -> list[dict]:
    if settings.mock:
        raise HTTPException(
            status_code=409,
            detail="arayüz test akışı açıkken canlı akış çekilmez",
        )
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
    return {"active": live_cctv.active,
            "feeds": [vars(s) for s in live_cctv.status()]}


@app.get("/api/live/feeds")
async def live_feed_list() -> list[dict]:
    try:
        return load_feeds()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.on_event("shutdown")
async def _stop_live_on_shutdown() -> None:
    if live_cctv.active:
        await live_cctv.stop()


@app.get("/api/interpret_config")
async def interpret_config() -> dict:
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
            pass
    return {
        "default_model": settings.main_model,
        "models": models,
        "system_prompt": SYSTEM_TR,
        "task_prompt": TASK_TR,
    }


@app.get("/api/videos")
async def list_videos() -> list[str]:
    if not settings.media_dir.exists():
        return []
    return sorted(
        p.name
        for p in settings.media_dir.iterdir()
        if p.suffix.lower() in {".mp4", ".mkv", ".avi", ".mov"}
    )


@app.get("/api/actions")
async def action_snapshot() -> dict:
    return action_dispatcher.snapshot()


@app.get("/api/actions/{request_id}/artifact")
async def action_artifact(request_id: str) -> FileResponse:
    try:
        path = action_dispatcher.artifact(request_id)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(path, filename=path.name, media_type="text/markdown; charset=utf-8")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    global _ui_replay_task
    await manager.connect(ws)
    if settings.mock and _ui_replay_task is None:
        _ui_replay_task = asyncio.create_task(
            replay_jsonl(manager, UI_REPLAY_EVENTS, settings.mock_speed),
            name="dortgoz-ui-replay",
        )
        _ui_replay_task.add_done_callback(_observe_ui_replay)
    try:
        while True:
            raw = await ws.receive_text()
            msg = OperatorMessage.model_validate_json(raw)
            await handle_operator_message(msg)
    except WebSocketDisconnect:
        manager.disconnect(ws)


async def handle_operator_message(msg: OperatorMessage) -> None:
    if msg.kind == "chat":
        await manager.broadcast(Event.wrap(ChatMessage(role="operator", text=msg.text)))
        if settings.mock:
            await manager.broadcast(
                Event.wrap(
                    ChatMessage(
                        role="agent",
                        text=f"Arayüz test akışında sorunuz alındı: '{msg.text}'. "
                        "Video analizi ve gerçek ajan bu akışta çalışmaz.",
                    )
                )
            )
        else:
            from .agent.graph import run_chat

            await run_chat(msg.text, manager)
    elif msg.kind == "actuator_response":
        try:
            result = action_dispatcher.resolve(
                msg.request_id, msg.approved, msg.operator
            )
        except (KeyError, ValueError) as exc:
            await manager.broadcast(Event.wrap(
                ChatMessage(role="agent", text=f"Aksiyon kararı reddedildi: {exc}")
            ))
        else:
            await manager.broadcast(Event.wrap(result, feed=result.feed))
    elif msg.kind == "start_run":
        await start_run(msg)
    elif msg.kind == "stop_run":
        await stop_run()


async def start_run(msg: OperatorMessage) -> None:
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
        await manager.broadcast(
            Event.wrap(
                RunStatus(run_id="-", state="idle",
                          detail="Arayüz test akışında gerçek analiz başlatılmaz."),
                feed=msg.feed,
            )
        )
        return
    except AnalysisJobStartError as exc:
        await manager.broadcast(
            Event.wrap(RunStatus(run_id="-", state="error", detail=str(exc)),
                       feed=msg.feed)
        )


async def stop_run() -> None:
    jobs: CanonicalAnalysisJobService = app.state.analysis_jobs
    await jobs.cancel_all()


settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
