from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import cast

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from ..config import settings
from ..domain.event import VerifiedEvent
from ..domain.evidence import EvidenceItem, VerifiedEventType
from ..domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
)
from ..domain.learning import (
    DriftSnapshot,
    LearningOrchestratorOverview,
    LearningPlan,
    LearningRouteQueue,
)
from ..domain.model_lifecycle import ModelVersion, TrainingJob
from ..domain.pipeline import LearningPipelineView, PipelineModelItem
from ..domain.priority import InterventionPriority
from ..domain.provenance import HumanReview, ProcedureSource, ReviewDecision
from ..domain.video import VideoMetadata
from ..errors import RepositoryNotFoundError
from ..infrastructure.storage import LocalVideoStorage
from ..repositories.memory import InMemoryEventRepository
from ..repositories.sqlite import SqliteEventRepository
from ..services.analysis_job import (
    AnalysisJobCapacityError,
    AnalysisJobConflict,
    AnalysisJobExecutionDisabled,
    AnalysisJobStartError,
    CanonicalAnalysisJobService,
)
from ..services.dfine_deployment import execute_dfine_onnx_export
from ..services.dfine_training import DfineTrainingError, DfineTrainingService
from ..services.event_service import EventMemoryService
from ..services.execution_coordinator import (
    ExecutionCoordinationError,
    ExecutionCoordinator,
)
from ..services.incident_media import IncidentMediaError, IncidentMediaService
from ..services.ingest_service import VideoIngestService
from ..services.learning_orchestrator import LearningOrchestrator
from ..services.learning_pipeline import (
    LearningPipelineError,
    LearningPipelineService,
)
from ..services.model_registry import ModelRegistryError, ModelRegistryService
from ..services.training_sample import TrainingSampleError, TrainingSampleService
from ..services.training_selection import load_training_selection_policy
from .contracts import (
    AnalysisAccepted,
    AnalysisProgress,
    AnalyzeRequest,
    BatchApprovalFailure,
    BatchApprovalInput,
    BatchApprovalResult,
    DevelopmentApprovalInput,
    HumanReviewInput,
    IncidentMediaView,
    ModelPromotionInput,
    QueryRequest,
    QueryResponse,
    ReportResponse,
    SystemMetrics,
    TrainingJobPlanInput,
    TrainingSamplePrepareInput,
    TrainingSampleReviewInput,
    TrainingSampleView,
)
from .errors import error_response

LOGGER = logging.getLogger(__name__)


class ApiRuntime:

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
            live_segment_seconds=settings.live_segment_seconds,
            live_tail_seconds=settings.live_clip_tail_seconds,
            live_retention_hours=settings.live_clip_retention_hours,
            live_max_per_feed=settings.live_clip_max_per_feed,
        )
        self.training_samples = TrainingSampleService(
            self.repository,
            media_root=settings.media_dir,
            dataset_manifest_root=settings.runs_dir / "datasets",
            frame_root=settings.media_dir / "_training_samples",
            frame_width=settings.training_frame_width,
        )
        self.learning = LearningOrchestrator(self.repository)
        self.coordinator = (
            ExecutionCoordinator(settings.event_store_path)
            if settings.event_store_path is not None
            else None
        )
        self.pipeline = LearningPipelineService(
            self.repository,
            self.learning,
            workspace_root=settings.dfine_workspace_root,
            policy_path=settings.dfine_training_policy,
            dfine_repository=settings.dfine_training_repository,
            base_checkpoint=settings.dfine_base_checkpoint,
            dataset_manifest_path=settings.dfine_dataset_manifest,
            execution_coordinator=self.coordinator,
        )
        self.registry = ModelRegistryService(
            self.repository,
            workspace_root=settings.dfine_workspace_root,
            registry_root=settings.dfine_workspace_root / "models" / "dfine" / "local",
        )
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.training_runs: dict[str, asyncio.Task[None]] = {}

    def analysis_running(self) -> bool:
        return any(not task.done() for task in self.jobs.values())

    def training_service(self) -> DfineTrainingService:
        policy, _ = self.pipeline.policies()
        if policy is None:
            raise LearningPipelineError(
                "TRAINING_POLICY_UNREADABLE",
                f"eğitim politikası okunamadı: {settings.dfine_training_policy}",
            )
        selection_path = (
            settings.dfine_workspace_root / "defaults" / "dfine_sample_selection.json"
        )
        return DfineTrainingService(
            self.repository,
            workspace_root=settings.dfine_workspace_root,
            frame_root=settings.media_dir / "_training_samples",
            runs_root=settings.runs_dir,
            policy=policy,
            selection_policy=(
                load_training_selection_policy(selection_path)
                if selection_path.is_file()
                else None
            ),
            active_analysis_probe=self.analysis_running,
            execution_coordinator=self.coordinator,
        )


runtime = ApiRuntime()
router = APIRouter(prefix="/api")


def _canonical_analysis_jobs(request: Request) -> CanonicalAnalysisJobService:
    jobs = getattr(request.app.state, "analysis_jobs", None)
    if jobs is None:
        raise RuntimeError("canonical analysis job service yapılandırılmadı")
    return cast(CanonicalAnalysisJobService, jobs)


@router.post("/videos", response_model=VideoMetadata, status_code=201)
async def upload_video(file: UploadFile = File(...)) -> VideoMetadata | JSONResponse:

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
) -> list[VerifiedEvent] | JSONResponse:
    if runtime.repository.get_analysis(analysis_id) is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    try:
        return runtime.repository.list_events(analysis_id, status=status)
    except ValueError as exc:
        return error_response("INVALID_STATUS", str(exc), status_code=422)


@router.get("/events/{event_id}", response_model=VerifiedEvent)
async def get_event(event_id: str) -> VerifiedEvent:
    event = runtime.repository.get_event(event_id)
    if event is None:
        raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
    return event


@router.get("/events/{event_id}/evidence", response_model=list[EvidenceItem])
async def get_event_evidence(event_id: str) -> list[EvidenceItem]:
    event = runtime.repository.get_event(event_id)
    if event is None:
        raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
    return event.evidence


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
async def review_event(
    event_id: str,
    body: HumanReviewInput,
) -> HumanReview | JSONResponse:
    try:
        decision = ReviewDecision(body.decision)
        event_type = (
            VerifiedEventType(body.event_type) if body.event_type is not None else None
        )
    except ValueError as exc:
        return error_response("INVALID_REVIEW", str(exc), status_code=422)
    review = runtime.events.review_event(
        event_id,
        decision,
        reviewer=body.reviewer,
        note=body.note,
        event_type=event_type.value if event_type is not None else None,
        start_time=body.start_time,
        peak_time=body.peak_time,
        end_time=body.end_time,
        risk_level=body.risk_level,
        false_alarm_reason=body.false_alarm_reason,
        intervention_required=body.intervention_required,
    )
    if any(value is not None for value in (body.start_time, body.peak_time, body.end_time)):
        try:
            await runtime.incident_media.prepare(event_id)
        except IncidentMediaError as exc:
            LOGGER.warning(
                "review sonrası incident media yenilenemedi: event=%s code=%s",
                event_id,
                exc.code,
            )
    return review


@router.get("/events/{event_id}/reviews", response_model=list[HumanReview])
async def list_event_reviews(event_id: str) -> list[HumanReview]:
    return runtime.repository.list_reviews(event_id)


@router.get("/events/{event_id}/learning-plan", response_model=LearningPlan)
async def event_learning_plan(event_id: str) -> LearningPlan:
    return runtime.learning.plan(event_id)


@router.get("/system/learning-health", response_model=DriftSnapshot)
async def learning_health() -> DriftSnapshot:
    return runtime.learning.drift_snapshot()


@router.get(
    "/system/learning-orchestrator",
    response_model=LearningOrchestratorOverview,
)
async def learning_orchestrator_overview() -> LearningOrchestratorOverview:
    return runtime.learning.overview()


@router.get("/learning/routes/{use}", response_model=LearningRouteQueue)
async def learning_route_queue(use: DevelopmentUse) -> LearningRouteQueue:
    return runtime.learning.route_queue(use)


@router.get("/learning/pipeline", response_model=LearningPipelineView)
async def learning_pipeline() -> LearningPipelineView:
    """İnceleme → Onay → Kuyruk → Eğitim → Ölçüm → Terfi, tek okumada."""
    return runtime.pipeline.view()


@router.post("/learning/approvals/batch", response_model=BatchApprovalResult)
async def batch_development_approval(body: BatchApprovalInput) -> BatchApprovalResult:
    """Approve the same development uses across many reviewed events."""
    approved: list[str] = []
    failures: list[BatchApprovalFailure] = []
    for event_id in body.event_ids:
        try:
            reviews = runtime.repository.list_reviews(event_id)
        except RepositoryNotFoundError as exc:
            failures.append(BatchApprovalFailure(event_id=event_id, reason=str(exc)))
            continue
        if not reviews:
            failures.append(
                BatchApprovalFailure(
                    event_id=event_id, reason="olay için insan incelemesi yok"
                )
            )
            continue
        try:
            runtime.events.record_development_decision(
                event_id,
                reviews[-1].review_id,
                DevelopmentApprovalStatus.APPROVED,
                approved_uses=list(body.approved_uses),
                reviewer=body.reviewer,
                note=body.note,
            )
        except (RepositoryNotFoundError, ValueError) as exc:
            failures.append(BatchApprovalFailure(event_id=event_id, reason=str(exc)))
            continue
        approved.append(event_id)
    return BatchApprovalResult(approved_event_ids=approved, failures=failures)


@router.get("/learning/jobs/{job_id}", response_model=TrainingJob)
async def get_training_job(job_id: str) -> TrainingJob:
    job = runtime.repository.get_training_job(job_id)
    if job is None:
        raise RepositoryNotFoundError(f"training job bulunamadı: {job_id}")
    return job


@router.post("/learning/jobs", response_model=TrainingJob, status_code=201)
async def plan_training_job(body: TrainingJobPlanInput) -> TrainingJob | JSONResponse:
    """Paket oluştur: doğrulanmış kareleri COCO'ya aktarır ve işi kuyruğa alır."""
    readiness = runtime.pipeline.readiness()
    if not readiness.can_plan:
        return error_response(
            "TRAINING_NOT_CONFIGURED",
            "; ".join(readiness.blockers),
            status_code=409,
        )
    try:
        service = runtime.training_service()
        job = await asyncio.to_thread(
            lambda: service.plan(
                dataset_manifest_path=cast(Path, settings.dfine_dataset_manifest),
                dfine_repository=cast(Path, settings.dfine_training_repository),
                base_checkpoint=cast(Path, settings.dfine_base_checkpoint),
                architecture=body.architecture,
                requested_by=body.requested_by,
                epochs=body.epochs,
                batch_size=body.batch_size,
                workers=body.workers,
                gpu_index=body.gpu_index,
                max_gpu_minutes=body.max_gpu_minutes,
                seed=body.seed,
            )
        )
    except LearningPipelineError as exc:
        return error_response(exc.code, str(exc), status_code=exc.status_code)
    except DfineTrainingError as exc:
        return error_response(exc.code, str(exc), status_code=409)
    except (OSError, ValueError) as exc:
        return error_response("TRAINING_PLAN_FAILED", str(exc), status_code=409)
    return job


@router.post("/learning/jobs/{job_id}/run", response_model=TrainingJob, status_code=202)
async def run_training_job(job_id: str) -> TrainingJob | JSONResponse:
    """Start a queued job. A human calls this; nothing schedules it."""
    job = runtime.repository.get_training_job(job_id)
    if job is None:
        raise RepositoryNotFoundError(f"training job bulunamadı: {job_id}")
    readiness = runtime.pipeline.readiness()
    if not readiness.can_run:
        return error_response(
            "TRAINING_NOT_RUNNABLE",
            "; ".join(readiness.blockers) or "eğitim şu anda başlatılamaz",
            status_code=409,
        )
    existing = runtime.training_runs.get(job_id)
    if existing is not None and not existing.done():
        return error_response(
            "TRAINING_ALREADY_RUNNING", "bu iş zaten çalışıyor", status_code=409
        )
    try:
        service = runtime.training_service()
    except LearningPipelineError as exc:
        return error_response(exc.code, str(exc), status_code=exc.status_code)

    dfine_repository = cast(Path, settings.dfine_training_repository)
    base_checkpoint = cast(Path, settings.dfine_base_checkpoint)
    python_executable = settings.dfine_python or Path(sys.executable)

    async def execute() -> None:
        try:
            await asyncio.to_thread(
                service.execute,
                job_id,
                dfine_repository=dfine_repository,
                base_checkpoint=base_checkpoint,
                python_executable=python_executable,
            )
        except (DfineTrainingError, ExecutionCoordinationError, OSError) as exc:
            LOGGER.warning("D-FINE eğitim işi başarısız: %s: %s", job_id, exc)

    runtime.training_runs[job_id] = asyncio.create_task(execute())
    return job


@router.post(
    "/learning/models/{model_version_id}/export-onnx",
    response_model=ModelVersion,
)
async def export_candidate_onnx(model_version_id: str) -> ModelVersion | JSONResponse:
    """Ölçümün ilk adımı: aday checkpoint'i doğrulanmış ONNX'e aktarır."""
    candidate = runtime.repository.get_model_version(model_version_id)
    if candidate is None:
        raise RepositoryNotFoundError(f"model version bulunamadı: {model_version_id}")
    job = runtime.repository.get_training_job(candidate.training_job_id)
    if job is None:
        raise RepositoryNotFoundError(
            f"training job bulunamadı: {candidate.training_job_id}"
        )
    readiness = runtime.pipeline.readiness()
    if readiness.active_workload is not None:
        return error_response(
            "EXCLUSIVE_WORKLOAD_ACTIVE",
            f"münhasir iş çalışıyor: {readiness.active_workload}",
            status_code=409,
        )
    if settings.dfine_training_repository is None:
        return error_response(
            "TRAINING_NOT_CONFIGURED",
            "D-FINE eğitim deposu yapılandırılmadı "
            "(DORTGOZ_DFINE_TRAINING_REPOSITORY)",
            status_code=409,
        )
    try:
        version, _outcome, _log = await asyncio.to_thread(
            execute_dfine_onnx_export,
            repository=runtime.repository,
            candidate=candidate,
            training_job=job,
            workspace_root=settings.dfine_workspace_root,
            dfine_repository=settings.dfine_training_repository,
            python_executable=settings.dfine_python or Path(sys.executable),
            runs_root=settings.runs_dir,
            registry_root=(
                settings.dfine_workspace_root / "models" / "dfine" / "local"
            ),
            active_analysis_probe=runtime.analysis_running,
        )
    except (ModelRegistryError, DfineTrainingError) as exc:
        return error_response(exc.code, str(exc), status_code=409)
    except (OSError, ValueError, RuntimeError) as exc:
        return error_response("ONNX_EXPORT_FAILED", str(exc), status_code=409)
    return version


@router.get(
    "/learning/models/{model_version_id}/gate",
    response_model=PipelineModelItem,
)
async def model_promotion_gate(model_version_id: str) -> PipelineModelItem | JSONResponse:
    try:
        return runtime.pipeline.promotion_gate(model_version_id)
    except LearningPipelineError as exc:
        return error_response(exc.code, str(exc), status_code=exc.status_code)


@router.post(
    "/learning/models/{model_version_id}/promote",
    response_model=ModelVersion,
)
async def promote_model(
    model_version_id: str,
    body: ModelPromotionInput,
) -> ModelVersion | JSONResponse:
    """Promote a candidate. The gate is enforced by ModelRegistryService."""
    _, promotion_policy = runtime.pipeline.policies()
    if promotion_policy is None:
        return error_response(
            "PROMOTION_POLICY_UNREADABLE",
            f"terfi politikası okunamadı: {settings.dfine_training_policy}",
            status_code=409,
        )
    try:
        return await asyncio.to_thread(
            lambda: runtime.registry.promote(
                model_version_id,
                policy=promotion_policy,
                approved_by=body.approved_by,
                reason=body.reason,
            )
        )
    except ModelRegistryError as exc:
        return error_response(
            exc.code,
            "; ".join([str(exc), *exc.reasons]),
            status_code=409,
        )


@router.post(
    "/events/{event_id}/development-approval",
    response_model=DevelopmentApproval,
)
async def record_development_approval(
    event_id: str,
    body: DevelopmentApprovalInput,
) -> DevelopmentApproval:
    return runtime.events.record_development_decision(
        event_id,
        body.review_id,
        body.status,
        approved_uses=body.approved_uses,
        reviewer=body.reviewer,
        note=body.note,
        supersedes_approval_id=body.supersedes_approval_id,
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
    event_id: str,
    body: TrainingSamplePrepareInput,
) -> list[TrainingSampleView] | JSONResponse:
    try:
        samples = await runtime.training_samples.prepare(
            event_id,
            body.approval_id,
            body.dataset_manifest_name,
            prepared_by=body.prepared_by,
            timestamps=body.timestamps,
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
    sample_id: str,
    body: TrainingSampleReviewInput,
) -> TrainingSampleView | JSONResponse:
    try:
        sample = runtime.training_samples.verify(
            sample_id,
            review_result=body.review_result,
            boxes=body.boxes,
            reviewer=body.reviewer,
            annotation_tool=body.annotation_tool,
        )
    except TrainingSampleError as exc:
        return error_response(exc.code, str(exc), status_code=exc.status_code)
    return _training_sample_view(sample)


@router.post("/analyses/{analysis_id}/query", response_model=QueryResponse)
async def query_analysis(analysis_id: str, body: QueryRequest) -> QueryResponse:
    if runtime.repository.get_analysis(analysis_id) is None:
        raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
    if body.referenced_event_id:
        referenced = runtime.repository.get_event(body.referenced_event_id)
        if referenced is None or referenced.analysis_id != analysis_id:
            raise RepositoryNotFoundError(f"event bulunamadı: {body.referenced_event_id}")
    events = runtime.events.query(analysis_id, body.question)
    if body.referenced_event_id:
        events = [event for event in events if event.event_id == body.referenced_event_id]
    sources = {
        (action.document_id, action.section, action.version, action.content_hash): ProcedureSource(
            document_id=action.document_id,
            section=action.section,
            version=action.version,
            content_hash=action.content_hash,
        )
        for event in events
        for action in event.actions
    }
    labels = ", ".join(event.event_type.value for event in events)
    return QueryResponse(
        answer_tr=f"{len(events)} eşleşen olay bulundu" + (f": {labels}." if labels else "."),
        event_refs=[event.event_id for event in events],
        evidence_refs=[item.evidence_id for event in events for item in event.evidence],
        procedure_sources=list(sources.values()),
        uncertainties=sorted({item for event in events for item in event.uncertainties}),
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
