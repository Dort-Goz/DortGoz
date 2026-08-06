"""Canonical local REST uçları.

Uzun analizler yalnızca ``asyncio.create_task`` ile başlatılır; HTTP isteği
video analizinin bitmesini beklemez. WebSocket'in mevcut run akışı ayrı tutulur,
ancak yeni REST sonuçları aynı in-memory repository'den okunur.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse

from ..config import settings
from ..domain.event import VerifiedEvent
from ..domain.evidence import EvidenceItem, VerifiedEventType
from ..domain.memory import AnalysisStatus
from ..domain.provenance import (
    AnalysisProvenance,
    HumanReview,
    ModelRunRef,
    ReviewDecision,
)
from ..domain.video import VideoMetadata
from ..infrastructure.storage import LocalVideoStorage
from ..pipeline.candidate_model import CandidateScorer, load_candidate_scorer
from ..pipeline.feature_cache import JsonFeatureCache
from ..repositories.errors import RepositoryNotFoundError
from ..repositories.memory import InMemoryEventRepository
from ..services.event_service import EventMemoryService
from ..services.ingest_service import VideoIngestService
from ..services.mock_vertical import MockVerticalAnalysisService
from ..tools.local_agent import LocalVlmAgentTools
from ..tools.local_vlm import LocalVlmManifest, load_local_vlm_manifest
from ..tools.protocols import ToolExecutionError
from ..tools.screening import LocalCandidateScreeningTool
from .contracts import (
    AnalysisAccepted,
    AnalysisProgress,
    AnalyzeRequest,
    HumanReviewInput,
    QueryRequest,
    QueryResponse,
    ReportResponse,
    SystemMetrics,
)
from .errors import error_response


class ApiRuntime:
    """Uygulama yaşamı boyunca paylaşılan local adapter'lar ve işler."""

    def __init__(self) -> None:
        self.repository = InMemoryEventRepository()
        self.events = EventMemoryService(self.repository)
        self.storage = LocalVideoStorage(
            settings.media_dir,
            max_bytes=settings.video_max_bytes,
        )
        self.ingest = VideoIngestService(self.storage)
        self.candidate_scorer: CandidateScorer = load_candidate_scorer(
            settings.candidate_manifest_path
        )
        # Candidate profilinin feature cache'i process yeniden başlasa da korunur;
        # yalnız türetilmiş skorları taşır, ham medya veya tensor saklamaz.
        self.candidate_cache = JsonFeatureCache(settings.candidate_cache_dir)
        self.jobs: dict[str, asyncio.Task[None]] = {}


runtime = ApiRuntime()
router = APIRouter(prefix="/api")


@router.post("/videos", response_model=VideoMetadata, status_code=201)
async def upload_video(file: UploadFile = File(...)) -> VideoMetadata | JSONResponse:
    """Videoyu UUID adıyla media köküne alır ve ffprobe ile doğrular."""

    incoming_root = settings.media_dir / ".incoming"
    incoming_root.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix.lower() or ".upload"
    descriptor, raw_path = tempfile.mkstemp(prefix="upload-", suffix=suffix, dir=incoming_root)
    os.close(descriptor)
    source = Path(raw_path)
    copied = 0
    try:
        with source.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                copied += len(chunk)
                if copied > settings.video_max_bytes:
                    return error_response(
                        "FILE_TOO_LARGE",
                        "Video boyut sınırını aşıyor.",
                        status_code=413,
                    )
                handle.write(chunk)
        metadata = await runtime.ingest.ingest_file(
            source,
            original_filename=file.filename,
        )
        return runtime.repository.create_video(metadata)
    finally:
        source.unlink(missing_ok=True)
        await file.close()


@router.get("/videos/{video_id}", response_model=VideoMetadata)
async def get_video(video_id: str) -> VideoMetadata:
    video = runtime.repository.get_video(video_id)
    if video is None:
        raise RepositoryNotFoundError(f"video bulunamadı: {video_id}")
    if not video.processable:
        code = video.error_code.value if video.error_code is not None else "DECODE_FAILED"
        return error_response(
            code,
            "Video işlenebilirlik kontrolünden geçmedi.",
            status_code=400,
        )
    return video


@router.post("/videos/{video_id}/analyze", response_model=AnalysisAccepted, status_code=202)
async def analyze_video(video_id: str, request: AnalyzeRequest) -> AnalysisAccepted | JSONResponse:
    video = runtime.repository.get_video(video_id)
    if video is None:
        raise RepositoryNotFoundError(f"video bulunamadı: {video_id}")
    if request.profile not in {"mock", "candidate", "local_vlm"}:
        return error_response(
            "MODEL_UNAVAILABLE",
            "Bu analiz profili local backend'de kayıtlı değil.",
            status_code=503,
            retryable=True,
        )
    vlm_manifest: LocalVlmManifest | None = None
    if request.profile == "local_vlm":
        if settings.mock:
            return error_response(
                "MODEL_UNAVAILABLE",
                "Gerçek local VLM profili DORTGOZ_MOCK=1 iken açılamaz.",
                status_code=503,
                retryable=False,
            )
        if settings.vlm_manifest_path is None:
            return error_response(
                "MODEL_UNAVAILABLE",
                "Yerel VLM manifest yolu yapılandırılmamış.",
                status_code=503,
                retryable=False,
            )
        try:
            vlm_manifest = await asyncio.to_thread(
                load_local_vlm_manifest, settings.vlm_manifest_path
            )
        except ToolExecutionError as exc:
            return error_response(
                "MODEL_UNAVAILABLE",
                str(exc),
                status_code=503,
                details={"reason": exc.code},
                retryable=exc.code in {"MODEL_MANIFEST_MISSING", "MODEL_ARTIFACT_MISSING"},
            )

    for record in (runtime.repository.get_analysis(analysis_id) for analysis_id in runtime.jobs):
        if record and record.status in {
            AnalysisStatus.QUEUED,
            AnalysisStatus.RUNNING,
        }:
            return error_response(
                "ANALYSIS_ALREADY_RUNNING",
                "Local backend tek analiz sınırı nedeniyle başka bir analiz çalışıyor.",
                status_code=409,
            )

    analysis_id = str(uuid4())
    provenance = AnalysisProvenance(
        contract_version="1.0.0",
        config_version=request.config_version,
        code_revision="task-06-v1",
        model_runs=[
            ModelRunRef(
                model_id=(
                    runtime.candidate_scorer.model_id
                    if request.profile in {"candidate", "local_vlm"}
                    else "mock-screening-v1"
                ),
                role="screening",
                config_version=request.config_version,
                code_revision="task-06-v1",
            )
        ]
        + (
            [
                ModelRunRef(
                    model_id=vlm_manifest.model_id,
                    role="vlm",
                    prompt_version=vlm_manifest.prompt_version,
                    config_version=request.config_version,
                    code_revision="task-08-v1",
                    artifact_sha256=vlm_manifest.artifact_sha256,
                    model_license=vlm_manifest.license,
                    model_source=vlm_manifest.source,
                )
            ]
            if vlm_manifest is not None
            else []
        ),
    )
    runtime.events.start_analysis(video, provenance, analysis_id=analysis_id)
    task = asyncio.create_task(_run_analysis(analysis_id, video, request.profile, vlm_manifest))
    runtime.jobs[analysis_id] = task
    return AnalysisAccepted(
        analysis_id=analysis_id,
        video_id=video_id,
        status=AnalysisStatus.QUEUED,
        status_url=f"/api/analyses/{analysis_id}/status",
    )


async def _run_analysis(
    analysis_id: str,
    video: VideoMetadata,
    profile: str,
    vlm_manifest: LocalVlmManifest | None = None,
) -> None:
    runtime.repository.update_analysis_status(analysis_id, AnalysisStatus.RUNNING.value, 0.05)
    try:
        screening = (
            LocalCandidateScreeningTool(
                video_root=settings.media_dir,
                model=runtime.candidate_scorer,
                cache=runtime.candidate_cache,
            )
            if profile in {"candidate", "local_vlm"}
            else None
        )
        tools = (
            LocalVlmAgentTools(metadata=video, settings=settings, manifest=vlm_manifest)
            if profile == "local_vlm" and vlm_manifest is not None
            else None
        )
        result = await MockVerticalAnalysisService(screening=screening, tools=tools).analyze(
            video, analysis_id=analysis_id
        )
        total = max(1, len(result.candidates))
        for index, state in enumerate(result.candidates, start=1):
            runtime.events.persist_terminal_state(
                state,
                update_analysis=False,
                progress=index / total,
            )
        final_status = (
            AnalysisStatus.REVIEW_REQUIRED
            if result.human_review_count
            else AnalysisStatus.COMPLETED
        )
        runtime.repository.update_analysis_status(
            analysis_id,
            final_status.value,
            1.0,
        )
    except Exception as exc:
        runtime.repository.update_analysis_status(
            analysis_id,
            AnalysisStatus.FAILED.value,
            1.0,
            error=f"{type(exc).__name__}: {exc}",
        )


@router.get("/analyses/{analysis_id}/status", response_model=AnalysisProgress)
async def analysis_status(analysis_id: str) -> AnalysisProgress:
    record = runtime.repository.get_analysis(analysis_id)
    if record is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    return AnalysisProgress.from_record(record)


@router.get("/analyses/{analysis_id}/events", response_model=list[VerifiedEvent])
async def analysis_events(
    analysis_id: str,
    status: str | None = Query(default=None),
) -> list[dict]:
    if runtime.repository.get_analysis(analysis_id) is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    try:
        events = runtime.repository.list_events(analysis_id, status=status)
    except ValueError as exc:
        return error_response("INVALID_STATUS", str(exc), status_code=422)
    return [event.model_dump(mode="json") for event in events]


@router.get("/events/{event_id}", response_model=VerifiedEvent)
async def get_event(event_id: str) -> VerifiedEvent:
    event = runtime.repository.get_event(event_id)
    if event is None:
        raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
    return event.model_dump(mode="json")


@router.get("/events/{event_id}/evidence", response_model=list[EvidenceItem])
async def get_event_evidence(event_id: str) -> list[EvidenceItem]:
    event = runtime.repository.get_event(event_id)
    if event is None:
        raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
    return [item.model_dump(mode="json") for item in event.evidence]


@router.post("/events/{event_id}/review", response_model=HumanReview)
async def review_event(event_id: str, request: HumanReviewInput) -> HumanReview | JSONResponse:
    try:
        decision = ReviewDecision(request.decision)
        event_type = (
            VerifiedEventType(request.event_type) if request.event_type is not None else None
        )
    except ValueError as exc:
        return error_response("INVALID_REVIEW", str(exc), status_code=422)
    review = runtime.events.review_event(
        event_id,
        decision,
        reviewer=request.reviewer,
        note=request.note,
        event_type=event_type.value if event_type is not None else None,
        start_time=request.start_time,
        peak_time=request.peak_time,
        end_time=request.end_time,
        risk_level=request.risk_level,
    )
    return review.model_dump(mode="json")


@router.post("/analyses/{analysis_id}/query", response_model=QueryResponse)
async def query_analysis(analysis_id: str, request: QueryRequest) -> QueryResponse:
    if runtime.repository.get_analysis(analysis_id) is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    if request.referenced_event_id:
        referenced = runtime.repository.get_event(request.referenced_event_id)
        if referenced is None or referenced.analysis_id != analysis_id:
            raise RepositoryNotFoundError(
                f"event bulunamadı: {request.referenced_event_id}"
            )
    events = runtime.events.query(analysis_id, request.question)
    if request.referenced_event_id:
        events = [event for event in events if event.event_id == request.referenced_event_id]
    event_refs = [event.event_id for event in events]
    evidence_refs = [item.evidence_id for event in events for item in event.evidence]
    uncertainties = [item for event in events for item in event.uncertainties]
    labels = ", ".join(event.event_type.value for event in events)
    answer = (
        f"{len(events)} eşleşen olay bulundu"
        + (f": {labels}." if labels else ".")
    )
    return QueryResponse(
        answer_tr=answer,
        event_refs=event_refs,
        evidence_refs=evidence_refs,
        uncertainties=sorted(set(uncertainties)),
        tool_trace=[trace for event in events for trace in event.decision_trace],
    )


@router.get("/reports/{analysis_id}", response_model=ReportResponse)
async def analysis_report(analysis_id: str) -> ReportResponse:
    result = runtime.events.get_analysis_result(analysis_id)
    if result is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    return ReportResponse.model_validate(result.model_dump())


@router.get("/system/metrics", response_model=SystemMetrics)
async def system_metrics() -> SystemMetrics:
    snapshot = runtime.repository.snapshot_metrics()
    active = sum(not task.done() for task in runtime.jobs.values())
    return SystemMetrics(active_analyses=active, **snapshot)


__all__ = ["ApiRuntime", "router", "runtime"]
