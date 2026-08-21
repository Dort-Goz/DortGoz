from __future__ import annotations

import asyncio
import logging
import tempfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.contracts import TriageDecisionInput
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
from .services.analysis_job import (
    AnalysisJobExecutionDisabled,
    AnalysisJobNotReady,
    AnalysisJobStartError,
    CanonicalAnalysisJobService,
)
from .services.deployment_readiness import DeploymentReadinessService
from .services.execution_coordinator import ExecutionCoordinator
from .services.run_identity import safe_run_file
from .services.startup_reconciliation import StartupReconciliationService
from .ws import ConnectionManager, replay_jsonl


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _reconcile_persistent_work_on_startup()
    try:
        yield
    finally:
        if live_cctv.active:
            await live_cctv.stop()


app = FastAPI(title="Dörtgöz", version="0.1.0", lifespan=lifespan)
manager = ConnectionManager()
LOGGER = logging.getLogger(__name__)

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
_mock_replay_task: asyncio.Task[None] | None = None


def _observe_mock_replay(task: asyncio.Task[None]) -> None:
    global _mock_replay_task
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        LOGGER.exception("mock replay görevi başarısız oldu")
    finally:
        if _mock_replay_task is task:
            _mock_replay_task = None

deployment_readiness = DeploymentReadinessService(settings, api_runtime.repository)
app.state.deployment_readiness = deployment_readiness
execution_coordinator = ExecutionCoordinator(
    settings.event_store_path or settings.runs_dir / ".execution-coordination.sqlite3"
)
app.state.execution_coordinator = execution_coordinator
BACKEND_BOOT_ID = uuid4().hex
startup_reconciliation = StartupReconciliationService(
    api_runtime.repository,
    execution_coordinator,
    boot_id=BACKEND_BOOT_ID,
)
app.state.startup_reconciliation = startup_reconciliation


def _reconcile_persistent_work_on_startup() -> None:
    report = startup_reconciliation.reconcile()
    if report.training_interrupted or report.training_conflicts:
        LOGGER.warning(
            "başlangıç uzlaştırması: interrupted=%s conflicts=%s active_skipped=%s",
            report.training_interrupted,
            report.training_conflicts,
            report.training_active_skipped,
        )


async def ensure_analysis_ready() -> None:
    if settings.runtime_profile != "competition-real":
        return
    report = await deployment_readiness.inspect()
    if not report.ready:
        raise AnalysisJobNotReady(
            "competition-real analiz kapısı kapalı: " + "; ".join(report.blocking_reasons())
        )

analysis_jobs = CanonicalAnalysisJobService(
    manager,
    runs_dir=settings.runs_dir,
    max_active=settings.max_feeds,
    enabled=lambda: not settings.mock,
    finalize_run=api_runtime.incident_media.finalize_analysis,
    pre_start=ensure_analysis_ready,
    execution_coordinator=execution_coordinator,
)
app.state.analysis_jobs = analysis_jobs


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mock": settings.mock, "profile": settings.runtime_profile}


@app.get("/ready")
async def readiness() -> JSONResponse:
    report = await deployment_readiness.inspect(force=True)
    return JSONResponse(
        status_code=200 if report.ready else 503,
        content=report.as_dict(),
    )


@app.get("/api/runs")
async def list_runs() -> list[str]:
    if not settings.runs_dir.exists():
        return []
    return sorted(p.stem for p in settings.runs_dir.glob("*.jsonl"))


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> list[dict]:
    from .pipeline.runner import load_run

    try:
        path = safe_run_file(settings.runs_dir, run_id, ".jsonl")
    except ValueError:
        raise HTTPException(status_code=404, detail="koşu bulunamadı")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="koşu bulunamadı")
    return load_run(run_id)


@app.get("/api/runs/{run_id}/export")
async def export_run(run_id: str) -> FileResponse:
    from .services.analysis_package import export_with_evidence

    try:
        safe_run_file(settings.runs_dir, run_id, ".jsonl")
        pkg = await export_with_evidence(run_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="koşu bulunamadı")
    return FileResponse(pkg, filename=pkg.name, media_type="application/zip")


IMPORT_MAX_BYTES = settings.video_max_bytes + 256 * 1024 * 1024


def _stage_import_package(data: bytes) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(data)
        return Path(tmp.name)


@app.post("/api/runs/import")
async def import_run(request: Request) -> dict:

    from .services.analysis_package import import_analysis

    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > IMPORT_MAX_BYTES:
            raise HTTPException(status_code=413, detail="paket gövdesi çok büyük")
    if not data:
        raise HTTPException(status_code=400, detail="boş paket gövdesi")
    tmp_path = await asyncio.to_thread(_stage_import_package, bytes(data))
    try:
        ctx = await asyncio.to_thread(import_analysis, tmp_path)
    except (ValueError, zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"geçersiz paket: {exc}")
    finally:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
    return {"run_id": ctx.run_id, "video": ctx.video, "verdict": ctx.verdict(),
            "incidents": len(ctx.incidents), "reports": len(ctx.reports)}


from .services.analysis_projection import RuntimeAnalysisProjection  # noqa: E402
from .services.live_cctv import LiveCctvService, load_feeds  # noqa: E402

runtime_projection = RuntimeAnalysisProjection(
    api_runtime.repository,
    settings.runs_dir,
    allow_virtual_sources=settings.mock,
)
live_cctv = LiveCctvService(
    manager,
    prepare_run=runtime_projection.register_runtime_source,
    finalize_run=api_runtime.incident_media.finalize_analysis,
    execution_coordinator=execution_coordinator,
)
app.state.live_cctv = live_cctv

from .services import triage  # noqa: E402

# Projection ilk observer'dır. Triage aynı yayın turunda kalıcı event_id alır.
triage.store.configure(
    api_runtime.repository,
    api_runtime.events,
    runtime_projection.event_id_for,
)
manager.observers.append(runtime_projection.observe)
manager.observers.append(triage.store.observe)


@app.get("/api/triage")
async def triage_snapshot() -> dict:
    return triage.store.snapshot()


@app.post("/api/triage/rules/{proposal_id}/approve")
async def triage_approve_rule(proposal_id: str, body: dict) -> dict:
    try:
        triage.store.approve_rule(
            proposal_id,
            body.get("reviewer", "operator-console"),
            int(body.get("duration_hours", triage.DEFAULT_RULE_HOURS)),
            expected_revision=(
                int(body["revision"]) if body.get("revision") is not None else None
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return triage.store.snapshot()


@app.post("/api/triage/rules/{proposal_id}/reject")
async def triage_reject_rule(proposal_id: str, body: dict) -> dict:
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
async def triage_decide(body: TriageDecisionInput) -> dict:
    try:
        item = triage.store.decide(
            body.key,
            body.verdict,
            category=body.category or "",
            risk_level=body.risk_level,
            start_time=body.start_time,
            peak_time=body.peak_time,
            end_time=body.end_time,
            false_alarm_reason=body.false_alarm_reason,
            intervention_required=body.intervention_required,
            note=body.note,
            reviewer=body.reviewer,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except (triage.TriagePersistenceError, RepositoryError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    from dataclasses import asdict
    return asdict(item)


@app.post("/api/live/start")
async def live_start(body: dict | None = None) -> list[dict]:
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
    return {"active": live_cctv.active,
            "feeds": [vars(s) for s in live_cctv.status()]}


@app.get("/api/live/feeds")
async def live_feed_list() -> list[dict]:
    try:
        return load_feeds()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    global _mock_replay_task
    await manager.connect(ws)
    if settings.mock and _mock_replay_task is None:
        _mock_replay_task = asyncio.create_task(
            replay_jsonl(manager, MOCK_EVENTS, settings.mock_speed),
            name="dortgoz-mock-replay",
        )
        _mock_replay_task.add_done_callback(_observe_mock_replay)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = OperatorMessage.model_validate_json(raw)
            except ValidationError:
                continue
            await handle_operator_message(msg, ws=ws)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)


async def handle_operator_message(msg: OperatorMessage, *, ws: WebSocket | None = None) -> None:
    if msg.kind == "sync":
        if ws is not None:
            await manager.replay_since(ws, msg.from_seq)
    elif msg.kind == "chat":
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
            from .agent.graph import run_chat

            await run_chat(msg.text, manager)
    elif msg.kind == "actuator_response":
        from .agent.actuators import registry as actuator_registry

        try:
            result = actuator_registry.resolve(msg.request_id, msg.approved)
        except (KeyError, ValueError) as exc:
            await manager.broadcast(
                Event.wrap(ChatMessage(role="agent", text=f"Aktüatör kararı reddedildi: {exc}"))
            )
        else:
            await manager.broadcast(Event.wrap(result))
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
                          detail="Mock kipte gerçek analiz başlatılmaz — kayıt zaten oynuyor."),
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
