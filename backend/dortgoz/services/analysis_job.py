from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from ..ws import ConnectionManager
from .weight_guard import guard as weight_guard

LOGGER = logging.getLogger(__name__)

RunVideoCallable = Callable[..., Awaitable[None]]
EnabledPredicate = Callable[[], bool]
IdFactory = Callable[[], str]


class AnalysisJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class AnalysisJobStartError(RuntimeError):
    pass


class AnalysisJobConflict(AnalysisJobStartError):
    pass


class AnalysisJobCapacityError(AnalysisJobStartError):
    pass


class AnalysisJobExecutionDisabled(AnalysisJobStartError):
    pass


@dataclass(frozen=True, slots=True)
class EffectiveRuntimeConfig:
    model: str
    system_prompt: str
    task_prompt: str
    mode: str = ""


@dataclass(frozen=True, slots=True)
class AnalysisJobSnapshot:
    analysis_id: str
    video: str
    feed: str
    status: AnalysisJobStatus
    effective_config: EffectiveRuntimeConfig

    @property
    def run_id(self) -> str:
        return self.analysis_id


@dataclass(slots=True)
class _JobRecord:
    analysis_id: str
    video: str
    video_identity: str
    feed: str
    effective_config: EffectiveRuntimeConfig
    status: AnalysisJobStatus = AnalysisJobStatus.QUEUED
    task: asyncio.Task[None] | None = None
    cancel_requested: bool = False

    def snapshot(self) -> AnalysisJobSnapshot:
        return AnalysisJobSnapshot(
            analysis_id=self.analysis_id,
            video=self.video,
            feed=self.feed,
            status=self.status,
            effective_config=self.effective_config,
        )


async def _default_run_video(
    manager: ConnectionManager,
    video: str,
    run_id: str,
    **kwargs: str,
) -> None:
    from ..pipeline.runner import run_video

    await run_video(manager, video, run_id, **kwargs)


def _default_runtime_config() -> EffectiveRuntimeConfig:
    from ..config import settings
    from ..pipeline.interpret import SYSTEM_TR, TASK_TR

    return EffectiveRuntimeConfig(
        model=settings.main_model,
        system_prompt=SYSTEM_TR,
        task_prompt=TASK_TR,
    )


def _video_identity(video: str) -> str:

    return Path(video).as_posix()


def _retrieve_task_exception(task: asyncio.Task[None]) -> None:

    try:
        error = task.exception()
    except asyncio.CancelledError:
        return
    if error is not None:
        LOGGER.error(
            "canonical analysis job task failed: %s",
            task.get_name(),
            exc_info=(type(error), error, error.__traceback__),
        )


class CanonicalAnalysisJobService:

    def __init__(
        self,
        manager: ConnectionManager,
        *,
        runs_dir: Path,
        max_active: int,
        run_video: RunVideoCallable = _default_run_video,
        defaults: Callable[[], EffectiveRuntimeConfig] = _default_runtime_config,
        enabled: EnabledPredicate = lambda: True,
        id_factory: IdFactory = lambda: uuid4().hex,
    ) -> None:
        if max_active < 1:
            raise ValueError("max_active en az 1 olmalı")
        self.manager = manager
        self.runs_dir = runs_dir
        self.max_active = max_active
        self._run_video = run_video
        self._defaults = defaults
        self._enabled = enabled
        self._id_factory = id_factory
        self._lock = asyncio.Lock()
        self._records: dict[str, _JobRecord] = {}
        self._active_by_feed: dict[str, str] = {}
        self._terminal_status: dict[str, AnalysisJobStatus] = {}

    async def start(
        self,
        video: str,
        *,
        feed: str = "",
        model: str = "",
        system_prompt: str = "",
        task_prompt: str = "",
        mode: str = "",
    ) -> AnalysisJobSnapshot:

        if not self._enabled():
            raise AnalysisJobExecutionDisabled("mock fixture etkin; gerçek runner başlatılmadı")

        defaults = self._defaults()
        effective = EffectiveRuntimeConfig(
            model=model or defaults.model,
            system_prompt=system_prompt or defaults.system_prompt,
            task_prompt=task_prompt or defaults.task_prompt,
            mode=mode,
        )
        identity = _video_identity(video)

        async with self._lock:
            current_id = self._active_by_feed.get(feed)
            if current_id is not None:
                current = self._records[current_id]
                if current.task is not None and not current.task.done():
                    if current.video_identity == identity and current.effective_config == effective:
                        return current.snapshot()
                    label = f" ({feed})" if feed else ""
                    raise AnalysisJobConflict(f"bu akışta farklı bir koşu sürüyor{label}")
                self._active_by_feed.pop(feed, None)

            if self._active_count_locked() >= self.max_active:
                raise AnalysisJobCapacityError(
                    f"akış sınırı: aynı anda en çok {self.max_active} koşu"
                )

            analysis_id = self._new_analysis_id_locked()
            record = _JobRecord(
                analysis_id=analysis_id,
                video=video,
                video_identity=identity,
                feed=feed,
                effective_config=effective,
            )
            self._records[analysis_id] = record
            self._active_by_feed[feed] = analysis_id
            task = asyncio.create_task(
                self._execute(
                    record,
                    model=model,
                    system_prompt=system_prompt,
                    task_prompt=task_prompt,
                    mode=mode,
                ),
                name=f"dortgoz-analysis-{analysis_id}",
            )
            record.task = task
            task.add_done_callback(_retrieve_task_exception)
            return record.snapshot()

    async def cancel(self, analysis_id: str) -> AnalysisJobStatus | None:

        async with self._lock:
            record = self._records.get(analysis_id)
            if record is None:
                cached = self._terminal_status.get(analysis_id)
                if cached is not None:
                    return cached
                return resolve_jsonl_status(self.runs_dir, analysis_id, active=False)
            task = record.task
            if task is None or task.done():
                return record.status
            record.cancel_requested = True
            task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass
        async with self._lock:
            parsed = resolve_jsonl_status(self.runs_dir, analysis_id, active=False)
            if parsed is not None:
                record.status = parsed
                if record.status == AnalysisJobStatus.INTERRUPTED and record.cancel_requested:
                    record.status = AnalysisJobStatus.CANCELLED
            elif record.status in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING}:
                record.status = AnalysisJobStatus.CANCELLED
            if record.task is not None and record.task.done():
                if self._active_by_feed.get(record.feed) == record.analysis_id:
                    self._active_by_feed.pop(record.feed, None)
                self._records.pop(record.analysis_id, None)
                self._terminal_status[record.analysis_id] = record.status
            return record.status

    async def cancel_all(self) -> None:

        async with self._lock:
            analysis_ids = [
                analysis_id
                for analysis_id, record in self._records.items()
                if record.task is not None and not record.task.done()
            ]
        if analysis_ids:
            await asyncio.gather(*(self.cancel(analysis_id) for analysis_id in analysis_ids))

    async def status(self, analysis_id: str) -> AnalysisJobStatus | None:

        async with self._lock:
            record = self._records.get(analysis_id)
            if record is not None:
                return record.status
            cached = self._terminal_status.get(analysis_id)
            if cached is not None:
                return cached
        return resolve_jsonl_status(self.runs_dir, analysis_id, active=False)

    async def active_count(self) -> int:
        async with self._lock:
            return self._active_count_locked()

    async def _execute(
        self,
        record: _JobRecord,
        *,
        model: str,
        system_prompt: str,
        task_prompt: str,
        mode: str = "",
    ) -> None:
        record.status = AnalysisJobStatus.RUNNING
        try:
            await self._run_video(
                self.manager,
                record.video,
                record.analysis_id,
                model=model,
                system_prompt=system_prompt,
                task_prompt=task_prompt,
                feed=record.feed,
                mode=mode,
            )
        except asyncio.CancelledError:
            parsed = resolve_jsonl_status(self.runs_dir, record.analysis_id, active=False)
            record.status = parsed or AnalysisJobStatus.CANCELLED
            if record.status == AnalysisJobStatus.INTERRUPTED and record.cancel_requested:
                record.status = AnalysisJobStatus.CANCELLED
            raise
        except Exception:
            parsed = resolve_jsonl_status(self.runs_dir, record.analysis_id, active=False)
            record.status = parsed or AnalysisJobStatus.FAILED
        except BaseException:
            record.status = (
                resolve_jsonl_status(self.runs_dir, record.analysis_id, active=False)
                or AnalysisJobStatus.INTERRUPTED
            )
            raise
        else:
            record.status = (
                resolve_jsonl_status(self.runs_dir, record.analysis_id, active=False)
                or AnalysisJobStatus.INTERRUPTED
            )
        finally:
            if record.status in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING}:
                record.status = (
                    resolve_jsonl_status(self.runs_dir, record.analysis_id, active=False)
                    or AnalysisJobStatus.INTERRUPTED
                )
            async with self._lock:
                if self._active_by_feed.get(record.feed) == record.analysis_id:
                    self._active_by_feed.pop(record.feed, None)
                self._records.pop(record.analysis_id, None)
                self._terminal_status[record.analysis_id] = record.status
                idle = self._active_count_locked() == 0
            if idle and weight_guard.needs_heal:
                try:
                    await weight_guard.heal()
                except Exception:
                    LOGGER.exception("weight_guard iyileşmesi başarısız")

    def _active_count_locked(self) -> int:
        return sum(
            record.task is not None and not record.task.done() for record in self._records.values()
        )

    def _new_analysis_id_locked(self) -> str:
        for _ in range(100):
            candidate = self._id_factory()
            if not candidate or Path(candidate).name != candidate:
                continue
            if candidate in self._records:
                continue
            if candidate in self._terminal_status:
                continue
            if (self.runs_dir / f"{candidate}.jsonl").exists():
                continue
            if (self.runs_dir / f"{candidate}.meta.json").exists():
                continue
            return candidate
        raise RuntimeError("benzersiz analysis_id üretilemedi")


def resolve_jsonl_status(
    runs_dir: Path,
    analysis_id: str,
    *,
    active: bool,
) -> AnalysisJobStatus | None:

    if not analysis_id or Path(analysis_id).name != analysis_id:
        return None
    path = runs_dir / f"{analysis_id}.jsonl"
    if not path.is_file():
        return None

    last_state: str | None = None
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return AnalysisJobStatus.RUNNING if active else AnalysisJobStatus.INTERRUPTED

    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        payload = raw.get("payload", raw) if isinstance(raw, dict) else None
        if not isinstance(payload, dict) or payload.get("type") != "run_status":
            continue
        if payload.get("run_id") != analysis_id:
            continue
        state = payload.get("state")
        if isinstance(state, str):
            last_state = state

    if last_state == "done":
        return AnalysisJobStatus.COMPLETED
    if last_state == "error":
        return AnalysisJobStatus.FAILED
    if last_state == "idle":
        return AnalysisJobStatus.CANCELLED
    if last_state == "processing":
        return AnalysisJobStatus.RUNNING if active else AnalysisJobStatus.INTERRUPTED
    return AnalysisJobStatus.RUNNING if active else AnalysisJobStatus.INTERRUPTED


__all__ = [
    "AnalysisJobCapacityError",
    "AnalysisJobConflict",
    "AnalysisJobExecutionDisabled",
    "AnalysisJobSnapshot",
    "AnalysisJobStartError",
    "AnalysisJobStatus",
    "CanonicalAnalysisJobService",
    "EffectiveRuntimeConfig",
    "resolve_jsonl_status",
]
