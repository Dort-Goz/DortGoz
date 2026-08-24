

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from ..domain.media import IncidentMedia
from ..domain.memory import AnalysisStatus
from ..pipeline.ingest import grab_frame
from ..repositories.protocols import EventRepository
from ..tools.context_clip import ClipWriter, write_context_clip

LOGGER = logging.getLogger(__name__)
FrameReader = Callable[[Path, float, int], Awaitable[bytes]]


class IncidentMediaError(RuntimeError):


    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class IncidentMediaService:
    def __init__(
        self,
        repository: EventRepository,
        *,
        media_root: Path,
        before_seconds: float = 8.0,
        after_seconds: float = 8.0,
        timeout_seconds: float = 90.0,
        clip_writer: ClipWriter = write_context_clip,
        frame_reader: FrameReader = grab_frame,
    ) -> None:
        if before_seconds < 0 or after_seconds < 0 or timeout_seconds <= 0:
            raise ValueError("incident media süreleri geçerli olmalıdır")
        self.repository = repository
        self.media_root = media_root.resolve()
        self.artifact_root = (self.media_root / "_incident_media").resolve()
        self.before_seconds = before_seconds
        self.after_seconds = after_seconds
        self.timeout_seconds = timeout_seconds
        self.clip_writer = clip_writer
        self.frame_reader = frame_reader

    async def finalize_analysis(self, analysis_id: str) -> list[IncidentMedia]:


        analysis = self.repository.get_analysis(analysis_id)
        if analysis is None or analysis.status not in {
            AnalysisStatus.COMPLETED,
            AnalysisStatus.REVIEW_REQUIRED,
        }:
            return []
        saved: list[IncidentMedia] = []
        for event in self.repository.list_events(analysis_id):
            try:
                saved.append(await self.prepare(event.event_id))
            except IncidentMediaError as exc:
                LOGGER.warning(
                    "incident media üretilemedi: analysis=%s event=%s code=%s detail=%s",
                    analysis_id,
                    event.event_id,
                    exc.code,
                    exc,
                )
        return saved

    async def prepare(self, event_id: str) -> IncidentMedia:
        event = self.repository.get_event(event_id)
        if event is None:
            raise IncidentMediaError("EVENT_NOT_FOUND", "Olay bulunamadı.")
        if event.start_time is None or event.peak_time is None or event.end_time is None:
            raise IncidentMediaError("EVENT_TIME_MISSING", "Olay zaman aralığı tamamlanmadı.")
        video = self.repository.get_video(event.video_id)
        if video is None:
            raise IncidentMediaError("VIDEO_NOT_FOUND", "Olay videosu bulunamadı.")
        source = self._resolve_source(video.media_path)
        start = max(0.0, event.start_time - self.before_seconds)
        end = min(video.duration_seconds, event.end_time + self.after_seconds)
        if end <= start:
            raise IncidentMediaError("EVENT_RANGE_INVALID", "Olay klibi aralığı geçersiz.")
        peak = min(max(event.peak_time, start), end)

        media_id = str(uuid5(NAMESPACE_URL, f"dortgoz-incident-media:{event.event_id}"))
        relative_dir = Path("_incident_media") / media_id
        output_dir = (self.media_root / relative_dir).resolve()
        if not output_dir.is_relative_to(self.artifact_root):
            raise IncidentMediaError("MEDIA_PATH_REJECTED", "Olay medyası yolu reddedildi.")
        clip = output_dir / "incident.mp4"
        thumbnail = output_dir / "thumbnail.jpg"
        staged_clip = output_dir / ".incident.next.mp4"
        staged_thumbnail = output_dir / ".thumbnail.next.jpg"
        current = self.repository.get_incident_media_for_event(event.event_id)
        if (
            current is not None
            and current.event_revision == event.revision
            and await self._artifact_matches(clip, current.clip_sha256)
            and await self._artifact_matches(thumbnail, current.thumbnail_sha256)
        ):
            return current

        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            await self.clip_writer(
                source, staged_clip, start, end, self.timeout_seconds
            )
            jpeg = await self.frame_reader(source, peak, 480)
            staged_thumbnail.write_bytes(jpeg)
            if not staged_clip.is_file() or staged_clip.stat().st_size == 0:
                raise IncidentMediaError("INCIDENT_CLIP_EMPTY", "Olay klibi boş üretildi.")
            if not staged_thumbnail.is_file() or staged_thumbnail.stat().st_size == 0:
                raise IncidentMediaError(
                    "INCIDENT_THUMBNAIL_EMPTY", "Önizleme boş üretildi."
                )
            staged_clip.replace(clip)
            staged_thumbnail.replace(thumbnail)
        except IncidentMediaError:
            staged_clip.unlink(missing_ok=True)
            staged_thumbnail.unlink(missing_ok=True)
            raise
        except Exception as exc:
            staged_clip.unlink(missing_ok=True)
            staged_thumbnail.unlink(missing_ok=True)
            raise IncidentMediaError(
                "INCIDENT_MEDIA_WRITE_FAILED", "Olay klibi veya önizleme üretilemedi."
            ) from exc

        now = datetime.now(UTC)
        stored = IncidentMedia(
            media_id=media_id,
            event_id=event.event_id,
            analysis_id=event.analysis_id,
            video_id=event.video_id,
            event_revision=event.revision,
            source_refs=[video.media_path],
            source_file_sha256=video.file_hash_sha256,
            clip_ref=(relative_dir / clip.name).as_posix(),
            thumbnail_ref=(relative_dir / thumbnail.name).as_posix(),
            clip_start=start,
            clip_end=end,
            peak_time=peak,
            pre_capture_seconds=self.before_seconds,
            post_capture_seconds=self.after_seconds,
            clip_sha256=await asyncio.to_thread(self._file_hash, clip),
            thumbnail_sha256=await asyncio.to_thread(self._file_hash, thumbnail),
            clip_size_bytes=clip.stat().st_size,
            thumbnail_size_bytes=thumbnail.stat().st_size,
            created_at=current.created_at if current is not None else now,
            updated_at=now,
            revision=current.revision + 1 if current is not None else 1,
        )
        return self.repository.save_incident_media(stored)

    def _resolve_source(self, media_path: str) -> Path:
        source = (self.media_root / media_path.lstrip("/")).resolve()
        if (
            not source.is_relative_to(self.media_root)
            or not source.is_file()
            or source.is_symlink()
        ):
            raise IncidentMediaError("VIDEO_NOT_FOUND", "Olay videosu artık erişilebilir değil.")
        return source

    @staticmethod
    async def _artifact_matches(path: Path, expected_hash: str) -> bool:
        return bool(
            path.is_file()
            and path.stat().st_size > 0
            and await asyncio.to_thread(IncidentMediaService._file_hash, path) == expected_hash
        )

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


__all__ = ["IncidentMediaError", "IncidentMediaService"]
