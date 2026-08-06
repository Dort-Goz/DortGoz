"""Güvenli storage ile ffprobe doğrulamasını birleştiren ingest servisi."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from ..domain.video import VideoMetadata, VideoProbe
from ..infrastructure.ffmpeg import probe_video
from ..infrastructure.storage import LocalVideoStorage

ProbeFunction = Callable[[Path], Awaitable[VideoProbe]]


class VideoIngestService:
    def __init__(self, storage: LocalVideoStorage, probe: ProbeFunction = probe_video) -> None:
        self.storage = storage
        self.probe = probe

    async def ingest_file(
        self, source: Path, *, original_filename: str | None = None
    ) -> VideoMetadata:
        stored = await self.storage.store(source, original_filename)
        try:
            probed = await self.probe(stored.absolute_path)
        except Exception:
            await self.storage.remove(stored)
            raise

        warnings = [
            "BLACK_FRAME_RATIO_NOT_MEASURED",
            "FREEZE_RATIO_NOT_MEASURED",
            "DECODE_VALIDATED_ZERO_ERRORS",
        ]
        if stored.duplicate_of_video_id:
            warnings.append(f"DUPLICATE_CONTENT:{stored.duplicate_of_video_id}")
        return VideoMetadata(
            video_id=stored.video_id,
            original_filename=stored.original_filename,
            stored_filename=stored.stored_filename,
            media_path=stored.media_path,
            file_size_bytes=stored.file_size_bytes,
            file_hash_sha256=stored.file_hash_sha256,
            warnings=warnings,
            **probed.model_dump(),
        )
