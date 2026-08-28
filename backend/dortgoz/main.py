from __future__ import annotations

import asyncio
import logging
import math
import tempfile
import time
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from itertools import count
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.contracts import OperatorReportInput, TriageDecisionInput
from .api.errors import (
    domain_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from .api.router import router as api_router
from .api.router import runtime as api_runtime
from .config import settings
from .domain.provenance import ReviewDecision
from .domain.video import VideoIngestError
from .errors import (
    RepositoryConflictError,
    RepositoryDuplicateError,
    RepositoryError,
    RepositoryNotFoundError,
)
from .events import (
    OPERATOR_INCIDENT_PREFIX,
    ActuatorRequest,
    ChatMessage,
    Event,
    IncidentUpdate,
    OperatorMessage,
    RunStatus,
)
from .services.action_dispatcher import dispatcher as action_dispatcher
from .services.analysis_job import (
    AnalysisJobExecutionDisabled,
    AnalysisJobNotReady,
    AnalysisJobStartError,
    CanonicalAnalysisJobService,
)
from .services.deployment_readiness import DeploymentReadinessService
from .services.execution_coordinator import ExecutionCoordinator
from .services.run_identity import require_safe_run_id, safe_run_file
from .services.startup_reconciliation import StartupReconciliationService
from .ws import ConnectionManager, replay_jsonl


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _ui_replay_task
    _reconcile_persistent_work_on_startup()
    try:
        yield
    finally:
        task = _ui_replay_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _ui_replay_task = None
        if live_cctv.active:
            await live_cctv.stop()
        if mock_live.active:
            await mock_live.stop()


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

UI_REPLAY_EVENTS = Path(__file__).parent / "fixtures" / "ui_replay_events.jsonl"
_ui_replay_task: asyncio.Task[None] | None = None


def _observe_ui_replay(task: asyncio.Task[None]) -> None:
    global _ui_replay_task
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        LOGGER.exception("UI replay akışı başarısız oldu")
    finally:
        if _ui_replay_task is task:
            _ui_replay_task = None


def _ui_replay_transform(
    *,
    video: str,
    feed: str,
    run_id: str,
    request_id: str,
):
    request_seq = count(1)

    def transform(event: Event) -> Event:
        transformed = event.model_copy(deep=True)
        transformed.feed = feed
        payload = transformed.payload
        if isinstance(payload, RunStatus):
            transformed.payload = payload.model_copy(update={
                "run_id": run_id,
                "video": video,
            })
        elif isinstance(payload, ActuatorRequest):
            request = payload.model_copy(update={
                "request_id": f"{request_id}-{next(request_seq)}",
                "run_id": run_id,
                "feed": feed,
                "requested_at": time.time(),
            })
            registered, _ = action_dispatcher.register_ui_fixture(request)
            transformed.payload = registered
        elif payload.type == "tool_call":
            args = dict(payload.args)
            args["feed"] = feed
            payload.args = args
        return transformed

    return transform


async def _start_ui_replay(video: str, feed: str) -> None:
    global _ui_replay_task
    if _ui_replay_task is not None and not _ui_replay_task.done():
        await manager.broadcast(Event.wrap(
            RunStatus(
                run_id="-",
                state="processing",
                detail="Arayüz test akışı zaten çalışıyor.",
                video=video,
            ),
            feed=feed,
        ))
        return
    safe_name = Path(video).name if video else ""
    if video and safe_name != video:
        await manager.broadcast(Event.wrap(
            RunStatus(
                run_id="-",
                state="error",
                detail="Arayüz test akışı için media/ içinden geçerli bir video seçin.",
                video=safe_name,
            ),
            feed=feed,
        ))
        return
    media_root = settings.media_dir.resolve()
    video_path = (media_root / safe_name).resolve() if safe_name else None
    has_file = (
        video_path is not None
        and video_path.parent == media_root
        and video_path.is_file()
    )
    if not has_file:
        safe_name = safe_name or "sanal-test-kaydi"
        await manager.broadcast(Event.wrap(
            RunStatus(
                run_id="-",
                state="processing",
                detail="ARAYÜZ TEST AKIŞI · SANAL KAYIT — media/ içinde video bulunamadı",
                video=safe_name,
            ),
            feed=feed,
        ))
    token = uuid4().hex[:10]
    run_id = f"fixture-ui-crime-{token}"
    request_id = f"fixture-req-{token}"
    _ui_replay_task = asyncio.create_task(
        replay_jsonl(
            manager,
            UI_REPLAY_EVENTS,
            settings.mock_speed,
            transform=_ui_replay_transform(
                video=safe_name,
                feed=feed,
                run_id=run_id,
                request_id=request_id,
            ),
        ),
        name="dortgoz-ui-replay",
    )
    _ui_replay_task.add_done_callback(_observe_ui_replay)

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

async def register_analysis_source(run_id: str, video: str) -> None:
    from .pipeline.runner import resolve_media

    await runtime_projection.register_runtime_source(run_id, video, resolve_media(video))


analysis_jobs = CanonicalAnalysisJobService(
    manager,
    runs_dir=settings.runs_dir,
    max_active=settings.max_feeds,
    enabled=lambda: not settings.mock,
    finalize_run=api_runtime.incident_media.finalize_analysis,
    pre_start=ensure_analysis_ready,
    prepare_run=register_analysis_source,
    execution_coordinator=execution_coordinator,
)
app.state.analysis_jobs = analysis_jobs


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mock": settings.mock,
        "profile": settings.runtime_profile,
        "analysis_mode": "ui_fixture_replay" if settings.mock else "evren_video_analysis",
        "external_delivery": False,
    }


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


async def _stage_import_package(request: Request) -> Path:
    total = 0
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        staged = Path(tmp.name)
        try:
            async for chunk in request.stream():
                total += len(chunk)
                if total > IMPORT_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="paket gövdesi çok büyük")
                await asyncio.to_thread(tmp.write, chunk)
            if total == 0:
                raise HTTPException(status_code=400, detail="boş paket gövdesi")
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
    return staged


@app.post("/api/runs/import")
async def import_run(request: Request) -> dict:

    from .services.analysis_package import import_analysis

    tmp_path = await _stage_import_package(request)
    try:
        ctx = await asyncio.to_thread(import_analysis, tmp_path)
    except (ValueError, zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"geçersiz paket: {exc}")
    finally:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
    return {"run_id": ctx.run_id, "video": ctx.video, "verdict": ctx.verdict(),
            "incidents": len(ctx.incidents), "reports": len(ctx.reports)}


from .services.analysis_projection import RuntimeAnalysisProjection
from .services.live_cctv import LiveCctvService, load_feeds
from .services.mock_console import MockLiveService, mock_chat, placeholder_frame

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
mock_live = MockLiveService(manager)


def _live_service():
    return mock_live if settings.mock else live_cctv


from .services import triage

triage.store.configure(
    api_runtime.repository,
    api_runtime.events,
    runtime_projection.event_id_for,
)
manager.observers.append(runtime_projection.observe)
manager.observers.append(triage.store.observe)


@app.get("/api/triage")
async def triage_snapshot() -> dict:
    snapshot = triage.store.snapshot()
    for item in snapshot["confirmed"]:
        try:
            item["suggested_actions"] = action_dispatcher.suggestions(
                item["feed"], item["incident_id"]
            )
        except ValueError:
            item["suggested_actions"] = []
    return snapshot


def _mock_frame(timestamp: float) -> Response:
    return Response(
        content=placeholder_frame(timestamp),
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=60"},
    )


@app.get("/api/triage/evidence-frame")
async def triage_evidence_frame(key: str, timestamp: float) -> Response:
    item = triage.store.get_item(key)
    if item is None:
        raise HTTPException(status_code=404, detail="inceleme kaydı bulunamadı")
    if not math.isfinite(timestamp):
        raise HTTPException(status_code=422, detail="kanıt zamanı geçersiz")
    matched = next(
        (
            evidence
            for evidence in item.evidence_refs
            if abs(float(evidence.get("timestamp", -1.0)) - timestamp) <= 0.001
        ),
        None,
    )
    if matched is None:
        if settings.mock:
            return _mock_frame(timestamp)
        raise HTTPException(status_code=404, detail="olaya bağlı kanıt karesi bulunamadı")
    if not item.video:
        if settings.mock:
            return _mock_frame(timestamp)
        raise HTTPException(status_code=404, detail="kanıt videosu bulunamadı")
    from .pipeline import ingest
    from .pipeline.runner import resolve_media

    try:
        jpeg = await ingest.grab_frame(resolve_media(item.video), timestamp, width=480)
    except (FileNotFoundError, ValueError, ingest.FFmpegError) as exc:
        if settings.mock:
            return _mock_frame(timestamp)
        raise HTTPException(status_code=422, detail=f"kanıt karesi üretilemedi: {exc}")
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


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


_report_media_tasks: set[asyncio.Task] = set()


async def _prepare_report_media(event_id: str, delay: float) -> None:
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await api_runtime.incident_media.prepare(event_id)
    except Exception:
        LOGGER.warning(
            "operatör bildirimi için kanıt klibi üretilemedi: %s", event_id, exc_info=True
        )


@app.post("/api/triage/report")
async def triage_report(body: OperatorReportInput) -> dict:
    from .services.live_clip import segment_for_epoch, segment_start_epoch

    if body.live and body.end > time.time() + 60:
        raise HTTPException(status_code=422, detail="bildirim penceresi gelecekte olamaz")
    incident_id = f"{OPERATOR_INCIDENT_PREFIX}{uuid4().hex[:10]}"
    start, end = body.start, body.end
    run_id, video = body.run_id, body.video
    if body.live:
        run_id, video = "", ""
        segment = segment_for_epoch(
            settings.media_dir / "canli" / body.feed,
            body.start,
            float(settings.live_segment_seconds),
        )
        if segment is not None:
            epoch = segment_start_epoch(segment)
            candidate_run = f"canli-{body.feed}-{segment.stem.removeprefix('seg_')}"
            if (
                epoch is not None
                and api_runtime.repository.get_analysis(candidate_run) is not None
            ):
                run_id = candidate_run
                video = segment.relative_to(settings.media_dir).as_posix()
                start, end = body.start - epoch, body.end - epoch
    payload = IncidentUpdate(
        incident_id=incident_id,
        t=(start + end) / 2,
        phase="sonuclandi",
        title="Operatör bildirimi",
        anomaly_type=body.category,
        risk=body.risk,
        detail=body.note.strip(),
        olay_baslangic=start,
        olay_bitis=end,
    )
    event_id = None
    if run_id and api_runtime.repository.get_analysis(run_id) is not None:
        event_id = runtime_projection.persist_operator_incident(body.feed, run_id, payload)
    try:
        item = triage.store.report_missed(
            feed=body.feed,
            live=body.live,
            payload=payload,
            event_id=event_id,
            run_id=run_id,
            video=video,
            reviewer=body.reviewer,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if event_id is not None:
        event = api_runtime.repository.get_event(event_id)
        if event is not None:
            api_runtime.events.review_event(
                event_id,
                ReviewDecision.EDIT,
                reviewer=body.reviewer,
                note=body.note.strip(),
                event_type=event.event_type.value,
                start_time=start,
                peak_time=(start + end) / 2,
                end_time=end,
                risk_level=body.risk,
            )
    if not body.live or event_id is not None:
        await manager.broadcast(Event.wrap(payload, feed=body.feed, live=body.live))
    if event_id is not None:
        delay = float(settings.live_segment_seconds) * 2 if body.live else 0.0
        task = asyncio.create_task(_prepare_report_media(event_id, delay))
        _report_media_tasks.add(task)
        task.add_done_callback(_report_media_tasks.discard)
    from dataclasses import asdict
    return asdict(item)


@app.post("/api/live/start")
async def live_start(body: dict | None = None) -> list[dict]:
    try:
        statuses = await _live_service().start(mode=(body or {}).get("mode", ""))
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return [vars(s) for s in statuses]


@app.post("/api/live/stop")
async def live_stop() -> dict:
    await _live_service().stop()
    return {"status": "durdu"}


@app.get("/api/live/status")
async def live_status() -> dict:
    service = _live_service()
    return {"active": service.active,
            "feeds": [vars(s) for s in service.status()]}


@app.get("/api/review/events")
async def browse_stored_events(
    origin: str = "all",
    status: str = "all",
    urgency: str = "all",
    category: str = "all",
    feed: str = "",
    query: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    from .services.event_browser import EventFilters, browse_events

    return await asyncio.to_thread(
        browse_events,
        api_runtime.repository,
        settings.media_dir,
        EventFilters(
            origin=origin,
            status=status,
            urgency=urgency,
            category=category,
            feed=feed[:128],
            query=query[:200],
            limit=limit,
            offset=offset,
        ),
    )


PREVIEW_BOUNDARY = "dortgozkare"


def _preview_part(feed: str, frame: bytes) -> bytes:
    return (
        f"--{PREVIEW_BOUNDARY}\r\n"
        f"Content-Type: image/jpeg\r\n"
        f"X-Feed: {feed}\r\n"
        f"Content-Length: {len(frame)}\r\n\r\n"
    ).encode() + frame + b"\r\n"


@app.get("/api/live/preview")
async def live_preview_all():
    service = _live_service()
    frames = getattr(service, "preview_all", None)
    if frames is None or not settings.live_preview or not service.active:
        raise HTTPException(status_code=404, detail="canlı önizleme kapalı")

    async def multipart():
        async for feed, frame in frames():
            yield _preview_part(feed, frame)

    return StreamingResponse(
        multipart(),
        media_type=f"multipart/x-mixed-replace; boundary={PREVIEW_BOUNDARY}",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/live/preview/{feed}")
async def live_preview(feed: str):
    try:
        require_safe_run_id(feed)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="akış adı geçersiz")
    frames = _live_service().preview_frames(feed)
    if frames is None:
        raise HTTPException(status_code=404, detail="akış canlı değil")

    async def multipart():
        async for frame in frames:
            yield _preview_part(feed, frame)

    return StreamingResponse(
        multipart(),
        media_type=f"multipart/x-mixed-replace; boundary={PREVIEW_BOUNDARY}",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/live/clips")
async def live_clip_archive(feed: str = "", limit: int = 200) -> dict:
    from .services.live_archive import list_live_clips

    clips = await asyncio.to_thread(
        list_live_clips,
        api_runtime.repository,
        settings.media_dir,
        feed=feed,
        limit=max(1, min(limit, 500)),
    )
    return {
        "clips": clips,
        "retention_hours": settings.live_clip_retention_hours,
        "max_per_feed": settings.live_clip_max_per_feed,
    }


@app.get("/api/live/feeds")
async def live_feed_list() -> list[dict]:
    try:
        return load_feeds()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/interpret_config")
async def interpret_config() -> dict:
    from .pipeline.interpret import SYSTEM_TR, TASK_TR

    models = [settings.video_model, settings.main_model, settings.second_opinion_model]
    if not settings.mock:
        try:
            from .agent.llm import main_client

            page = await asyncio.wait_for(main_client().models.list(), timeout=5)
            ids = {m.id for m in page.data}
            models = [model for model in models if model in ids]
        except Exception:
            pass
    return {
        "default_model": settings.video_model,
        "models": models,
        "system_prompt": SYSTEM_TR,
        "task_prompt": TASK_TR,
    }


@app.get("/api/videos")
async def list_videos() -> list[str]:
    from .services import video_library

    return await asyncio.to_thread(video_library.catalog)


@app.get("/api/video/{video:path}")
async def video_file(video: str) -> FileResponse:
    """Oynatıcı kaynağı: medya klasörü ve veri kümesi aynı adresten okunur."""
    from .pipeline.runner import resolve_media

    try:
        path = await asyncio.to_thread(resolve_media, video)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="video bulunamadı")
    return FileResponse(path)


@app.get("/api/actions")
async def action_snapshot() -> dict:
    return action_dispatcher.snapshot(fixture_only=settings.mock)


@app.post("/api/actions/request")
async def request_action(body: dict) -> dict:
    try:
        request, created = action_dispatcher.request(
            str(body.get("action", "")),
            str(body.get("incident_id", "")),
            str(body.get("feed", "")),
            "Operatör olay inceleme merkezinden yerel taslak istedi.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if created:
        await manager.broadcast(Event.wrap(request, feed=request.feed, live=request.live))
    return {"created": created, "request": request.model_dump(mode="json")}


@app.get("/api/actions/{request_id}/artifact")
async def action_artifact(request_id: str) -> FileResponse:
    try:
        path = action_dispatcher.artifact(request_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(path, filename=path.name, media_type="text/markdown; charset=utf-8")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
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
        dialogue_id = msg.dialogue_id.strip() or "legacy"
        await manager.broadcast(Event.wrap(
            ChatMessage(
                role="operator",
                text=msg.text,
                dialogue_id=dialogue_id,
            ),
            feed=msg.feed,
        ))
        if settings.mock:
            await mock_chat(
                msg.text,
                manager,
                dialogue_id=dialogue_id,
                feed=msg.feed,
                referenced_event_id=msg.referenced_event_id,
            )
        else:
            from .agent.graph import run_chat

            await run_chat(
                msg.text,
                manager,
                dialogue_id=dialogue_id,
                feed=msg.feed if "feed" in msg.model_fields_set else None,
                referenced_event_id=msg.referenced_event_id,
            )
    elif msg.kind == "actuator_response":
        try:
            result = action_dispatcher.resolve(
                msg.request_id,
                msg.approved,
                msg.operator,
            )
        except KeyError:
            from .agent.actuators import registry as actuator_registry

            try:
                result = actuator_registry.resolve(msg.request_id, msg.approved)
            except (KeyError, ValueError) as exc:
                await manager.broadcast(Event.wrap(
                    ChatMessage(role="agent", text=f"Aksiyon kararı reddedildi: {exc}")
                ))
                return
        except ValueError as exc:
            await manager.broadcast(
                Event.wrap(ChatMessage(role="agent", text=f"Aksiyon kararı reddedildi: {exc}"))
            )
            return
        await manager.broadcast(Event.wrap(
            result,
            feed=getattr(result, "feed", ""),
            live=getattr(result, "live", False),
        ))
    elif msg.kind == "start_run":
        await start_run(msg)
    elif msg.kind == "stop_run":
        await stop_run()


async def start_run(msg: OperatorMessage) -> None:
    if settings.mock:
        await _start_ui_replay(msg.video, msg.feed)
        return
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
    global _ui_replay_task
    if settings.mock:
        task = _ui_replay_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _ui_replay_task = None
        await manager.broadcast(Event.wrap(
            RunStatus(run_id="-", state="idle", detail="Arayüz test akışı durduruldu.")
        ))
        return
    jobs: CanonicalAnalysisJobService = app.state.analysis_jobs
    await jobs.cancel_all()


settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
