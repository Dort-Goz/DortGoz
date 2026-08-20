from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import cast

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from ..config import settings
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
from ..services.ingest_service import VideoIngestService
from ..services.procedure_service import ProcedureService
from ..services.risk_engine import RiskEngine, load_risk_ruleset
from .contracts import (
    AnalysisAccepted,
    AnalysisProgress,
    AnalyzeRequest,
)
from .errors import error_response


class ApiRuntime:

    def __init__(self) -> None:
        self.repository = (
            SqliteEventRepository(settings.event_store_path)
            if settings.event_store_path is not None
            else InMemoryEventRepository()
        )
        self.storage = LocalVideoStorage(
            settings.media_dir,
            max_bytes=settings.video_max_bytes,
        )
        self.ingest = VideoIngestService(self.storage)
        self.candidate_scorer: CandidateScorer = load_candidate_scorer(
            settings.candidate_manifest_path
        )
        self.candidate_cache = JsonFeatureCache(settings.candidate_cache_dir)
        project_root = settings.media_dir.parent
        self.risk_engine = RiskEngine(
            load_risk_ruleset(project_root / "defaults" / "risk_rules.yaml")
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


__all__ = ["ApiRuntime", "router", "runtime"]
