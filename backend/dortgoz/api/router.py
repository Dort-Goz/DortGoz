"""Canonical local REST uçları.

REST ve WebSocket analiz başlatma yolları aynı process-local canonical job
servisini kullanır. Legacy event repository uçları ayrı sözleşme olarak korunur.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import cast

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from ..config import settings
from ..domain.event import VerifiedEvent
from ..domain.evidence import EvidenceItem, VerifiedEventType
from ..domain.feedback import DevelopmentApproval
from ..domain.memory import AnalysisStatus
from ..domain.priority import InterventionPriority
from ..domain.provenance import (
    HumanReview,
    ProcedureSource,
    ReviewDecision,
)
from ..domain.video import VideoMetadata
from ..infrastructure.storage import LocalVideoStorage
from ..pipeline.candidate_model import CandidateScorer, load_candidate_scorer
from ..pipeline.feature_cache import JsonFeatureCache
from ..repositories.errors import RepositoryNotFoundError
from ..repositories.memory import InMemoryEventRepository
from ..repositories.procedure_index import LocalProcedureIndex
from ..repositories.sqlite import SqliteEventRepository
from ..services.analysis_job import (
    AnalysisJobCapacityError,
    AnalysisJobConflict,
    AnalysisJobExecutionDisabled,
    AnalysisJobStartError,
    CanonicalAnalysisJobService,
)
from ..services.event_service import EventMemoryService
from ..services.incident_media import IncidentMediaError, IncidentMediaService
from ..services.ingest_service import VideoIngestService
from ..services.mock_vertical import MockVerticalAnalysisService
from ..services.procedure_service import ProcedureService
from ..services.risk_engine import RiskEngine, load_risk_ruleset
from ..services.training_sample import TrainingSampleError, TrainingSampleService
from ..tools.local_agent import LocalVlmAgentTools
from ..tools.local_vlm import LocalVlmManifest
from ..tools.screening import LocalCandidateScreeningTool
from .contracts import (
    AnalysisAccepted,
    AnalysisProgress,
    AnalyzeRequest,
    DevelopmentApprovalInput,
    HumanReviewInput,
    IncidentMediaView,
    QueryRequest,
    QueryResponse,
    ReportResponse,
    SystemMetrics,
    TrainingSamplePrepareInput,
    TrainingSampleReviewInput,
    TrainingSampleView,
)
from .errors import error_response

LOGGER = logging.getLogger(__name__)


class ApiRuntime:
    """Uygulama yaşamı boyunca paylaşılan local adapter'lar ve işler."""

    def __init__(self) -> None:
        self.repository = (
            SqliteEventRepository(settings.event_store_path)
            if settings.event_store_path is not None
            else InMemoryEventRepository()
        )
        self.events = EventMemoryService(self.repository)
        self.storage = LocalVideoStorage(
            settings.media_dir,
            max_bytes=settings.video_max_bytes,
        )
        self.ingest = VideoIngestService(self.storage)
        self.incident_media = IncidentMediaService(
            self.repository,
            media_root=settings.media_dir,
            before_seconds=settings.incident_pre_capture_seconds,
            after_seconds=settings.incident_post_capture_seconds,
            timeout_seconds=settings.incident_clip_timeout_seconds,
        )
        self.training_samples = TrainingSampleService(
            self.repository,
            media_root=settings.media_dir,
            dataset_manifest_root=settings.runs_dir / "datasets",
            frame_root=settings.media_dir / "_training_samples",
            frame_width=settings.training_frame_width,
        )
        self.candidate_scorer: CandidateScorer = load_candidate_scorer(
            settings.candidate_manifest_path
        )
        # Candidate profilinin feature cache'i process yeniden başlasa da korunur;
        # yalnız türetilmiş skorları taşır, ham medya veya tensor saklamaz.
        self.candidate_cache = JsonFeatureCache(settings.candidate_cache_dir)
        project_root = settings.media_dir.parent
        self.risk_engine = RiskEngine(
            load_risk_ruleset(project_root / "configs" / "risk_rules.yaml")
        )
        self.procedure_service = ProcedureService(
            LocalProcedureIndex.load(
                project_root / "data" / "procedures",
                project_root / "data" / "procedures" / "manifest.json",
            )
        )
        self.jobs: dict[str, asyncio.Task[None]] = {}


runtime = ApiRuntime()
router = APIRouter(prefix="/api")


def _canonical_analysis_jobs(request: Request) -> CanonicalAnalysisJobService:
    jobs = getattr(request.app.state, "analysis_jobs", None)
    if jobs is None:
        raise RuntimeError("canonical analysis job service yapılandırılmadı")
    return cast(CanonicalAnalysisJobService, jobs)


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
async def analyze_video(
    video_id: str,
    body: AnalyzeRequest,
    request: Request,
) -> AnalysisAccepted | JSONResponse:
    video = runtime.repository.get_video(video_id)
    if video is None:
        raise RepositoryNotFoundError(f"video bulunamadı: {video_id}")

    jobs = _canonical_analysis_jobs(request)
    try:
        snapshot = await jobs.start(
            video.stored_filename,
            feed=body.feed,
            model=body.model,
            system_prompt=body.system_prompt,
            task_prompt=body.task_prompt,
            mode=body.mode,
        )
    except AnalysisJobConflict as exc:
        return error_response(
            "ANALYSIS_CONFLICT",
            str(exc),
            status_code=409,
        )
    except AnalysisJobCapacityError as exc:
        return error_response(
            "ANALYSIS_CAPACITY_EXCEEDED",
            str(exc),
            status_code=409,
            retryable=True,
        )
    except AnalysisJobExecutionDisabled as exc:
        return error_response(
            "ANALYSIS_EXECUTION_DISABLED",
            str(exc),
            status_code=503,
        )
    except AnalysisJobStartError as exc:
        return error_response(
            "ANALYSIS_START_FAILED",
            str(exc),
            status_code=500,
            retryable=True,
        )
    except Exception as exc:
        return error_response(
            "ANALYSIS_START_FAILED",
            "Canonical analiz başlatılamadı.",
            status_code=500,
            details={"reason": type(exc).__name__},
            retryable=True,
        )

    return AnalysisAccepted(
        analysis_id=snapshot.analysis_id,
        video_id=video_id,
        status=snapshot.status,
        status_url=f"/api/analyses/{snapshot.analysis_id}/status",
        result_url=f"/api/runs/{snapshot.analysis_id}",
    )


# Legacy vertical helper: Patch C'ye kadar doğrudan test/uyumluluk için tutulur;
# production REST route'larının hiçbirinden çağrılmaz.
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
            projected = EventMemoryService._event_from_state(state)
            risk = runtime.risk_engine.assess(projected)
            recommendation = runtime.procedure_service.recommend(projected, risk)
            state = state.model_copy(update={"risk": risk, "procedures": recommendation.actions})
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
async def analysis_status(analysis_id: str, request: Request) -> AnalysisProgress:
    status = await _canonical_analysis_jobs(request).status(analysis_id)
    if status is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    return AnalysisProgress(analysis_id=analysis_id, status=status)


@router.post("/analyses/{analysis_id}/cancel", response_model=AnalysisProgress)
async def cancel_analysis(analysis_id: str, request: Request) -> AnalysisProgress:
    status = await _canonical_analysis_jobs(request).cancel(analysis_id)
    if status is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    return AnalysisProgress(analysis_id=analysis_id, status=status)


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


def _incident_media_view(media) -> IncidentMediaView:
    return IncidentMediaView.model_validate(
        {
            **media.model_dump(),
            "clip_url": f"/media/{media.clip_ref}",
            "thumbnail_url": f"/media/{media.thumbnail_ref}",
        }
    )


@router.get("/events/{event_id}/media", response_model=IncidentMediaView)
async def get_event_media(event_id: str) -> IncidentMediaView:
    if runtime.repository.get_event(event_id) is None:
        raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
    media = runtime.repository.get_incident_media_for_event(event_id)
    if media is None:
        raise RepositoryNotFoundError(f"event medyası bulunamadı: {event_id}")
    return _incident_media_view(media)


@router.get("/events/{event_id}/priority", response_model=InterventionPriority)
async def get_event_priority(event_id: str) -> InterventionPriority:
    if runtime.repository.get_event(event_id) is None:
        raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
    priority = runtime.repository.get_intervention_priority_for_event(event_id)
    if priority is None:
        raise RepositoryNotFoundError(f"event önceliği bulunamadı: {event_id}")
    return priority


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
        false_alarm_reason=request.false_alarm_reason,
        intervention_required=request.intervention_required,
    )
    if any(
        value is not None
        for value in (request.start_time, request.peak_time, request.end_time)
    ):
        try:
            await runtime.incident_media.prepare(event_id)
        except IncidentMediaError as exc:
            LOGGER.warning(
                "review sonrası incident media yenilenemedi: event=%s code=%s",
                event_id,
                exc.code,
            )
    return review.model_dump(mode="json")


@router.get("/events/{event_id}/reviews", response_model=list[HumanReview])
async def list_event_reviews(event_id: str) -> list[HumanReview]:
    return runtime.repository.list_reviews(event_id)


@router.post(
    "/events/{event_id}/development-approval",
    response_model=DevelopmentApproval,
)
async def record_development_approval(
    event_id: str, request: DevelopmentApprovalInput
) -> DevelopmentApproval:
    return runtime.events.record_development_decision(
        event_id,
        request.review_id,
        request.status,
        approved_uses=request.approved_uses,
        reviewer=request.reviewer,
        note=request.note,
        supersedes_approval_id=request.supersedes_approval_id,
    )


@router.get(
    "/events/{event_id}/development-approvals",
    response_model=list[DevelopmentApproval],
)
async def list_development_approvals(event_id: str) -> list[DevelopmentApproval]:
    return runtime.repository.list_development_approvals(event_id)


def _training_sample_view(sample) -> TrainingSampleView:
    return TrainingSampleView.model_validate(
        {**sample.model_dump(), "frame_url": f"/media/{sample.frame_ref}"}
    )


@router.post(
    "/events/{event_id}/training-samples",
    response_model=list[TrainingSampleView],
)
async def prepare_training_samples(
    event_id: str, request: TrainingSamplePrepareInput
) -> list[TrainingSampleView] | JSONResponse:
    try:
        samples = await runtime.training_samples.prepare(
            event_id,
            request.approval_id,
            request.dataset_manifest_name,
            prepared_by=request.prepared_by,
            timestamps=request.timestamps,
        )
    except TrainingSampleError as exc:
        return error_response(exc.code, str(exc), status_code=exc.status_code)
    return [_training_sample_view(sample) for sample in samples]


@router.get(
    "/events/{event_id}/training-samples",
    response_model=list[TrainingSampleView],
)
async def list_training_samples(event_id: str) -> list[TrainingSampleView]:
    if runtime.repository.get_event(event_id) is None:
        raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
    return [
        _training_sample_view(sample)
        for sample in runtime.repository.list_training_samples(event_id)
    ]


@router.post(
    "/training-samples/{sample_id}/review",
    response_model=TrainingSampleView,
)
async def review_training_sample(
    sample_id: str, request: TrainingSampleReviewInput
) -> TrainingSampleView | JSONResponse:
    try:
        sample = runtime.training_samples.verify(
            sample_id,
            review_result=request.review_result,
            boxes=request.boxes,
            reviewer=request.reviewer,
            annotation_tool=request.annotation_tool,
        )
    except TrainingSampleError as exc:
        return error_response(exc.code, str(exc), status_code=exc.status_code)
    return _training_sample_view(sample)


@router.post("/analyses/{analysis_id}/query", response_model=QueryResponse)
async def query_analysis(analysis_id: str, request: QueryRequest) -> QueryResponse:
    if runtime.repository.get_analysis(analysis_id) is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    if request.referenced_event_id:
        referenced = runtime.repository.get_event(request.referenced_event_id)
        if referenced is None or referenced.analysis_id != analysis_id:
            raise RepositoryNotFoundError(f"event bulunamadı: {request.referenced_event_id}")
    events = runtime.events.query(analysis_id, request.question)
    if request.referenced_event_id:
        events = [event for event in events if event.event_id == request.referenced_event_id]
    event_refs = [event.event_id for event in events]
    evidence_refs = [item.evidence_id for event in events for item in event.evidence]
    procedure_sources = list(
        {
            (
                action.document_id,
                action.section,
                action.version,
                action.content_hash,
            ): ProcedureSource(
                document_id=action.document_id,
                section=action.section,
                version=action.version,
                content_hash=action.content_hash,
            )
            for event in events
            for action in event.actions
        }.values()
    )
    uncertainties = [item for event in events for item in event.uncertainties]
    labels = ", ".join(event.event_type.value for event in events)
    answer = f"{len(events)} eşleşen olay bulundu" + (f": {labels}." if labels else ".")
    return QueryResponse(
        answer_tr=answer,
        event_refs=event_refs,
        evidence_refs=evidence_refs,
        procedure_sources=procedure_sources,
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
