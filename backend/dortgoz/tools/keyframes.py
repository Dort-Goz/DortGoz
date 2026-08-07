"""Candidate zaman aralığından local, hash'li VLM keyframe üretimi."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..domain.candidate import CandidateEvent
from ..domain.context import KeyframeRef
from ..domain.video import VideoMetadata
from ..pipeline.ingest import FFmpegError, grab_frame
from .protocols import ToolExecutionError

FrameFetcher = Callable[[Path, float], Awaitable[bytes]]


class LocalKeyframeTool:
    """Orijinal videoya dokunmadan `runs/` altında seçili JPEG kanıtı yazar."""

    def __init__(
        self,
        *,
        media_root: Path,
        workspace_root: Path,
        frame_fetcher: FrameFetcher | None = None,
    ) -> None:
        self.media_root = media_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.runs_root = (self.workspace_root / "runs").resolve()
        self.frame_fetcher = frame_fetcher or _fetch_frame

    async def capture(
        self,
        metadata: VideoMetadata,
        candidate: CandidateEvent,
        *,
        analysis_id: str,
        clip_start: float,
        clip_end: float,
    ) -> list[KeyframeRef]:
        if not 0 <= clip_start <= candidate.peak_time <= clip_end <= metadata.duration_seconds:
            raise ToolExecutionError("KEYFRAME_RANGE_INVALID", "Keyframe zaman aralığı geçersiz.")
        video = self._resolve_video(metadata)
        output_dir = (self.runs_root / analysis_id / candidate.candidate_id / "frames").resolve()
        if not output_dir.is_relative_to(self.runs_root):
            raise ToolExecutionError("ARTIFACT_PATH_REJECTED", "Keyframe yolu runs kökü dışında.")
        output_dir.mkdir(parents=True, exist_ok=True)
        choices = (("before", clip_start), ("peak", candidate.peak_time), ("after", clip_end))
        try:
            payloads = await asyncio.gather(
                *(self.frame_fetcher(video, timestamp) for _, timestamp in choices)
            )
        except FFmpegError as exc:
            raise ToolExecutionError("KEYFRAME_CAPTURE_FAILED", "VLM için kare alınamadı.") from exc
        frames: list[KeyframeRef] = []
        for (label, timestamp), jpeg in zip(choices, payloads):
            if not jpeg:
                raise ToolExecutionError("KEYFRAME_CAPTURE_FAILED", "VLM için boş kare üretildi.")
            target = output_dir / f"{label}.jpg"
            await asyncio.to_thread(_atomic_write, target, jpeg)
            relative = target.relative_to(self.workspace_root).as_posix()
            frames.append(
                KeyframeRef(
                    frame_id=f"{candidate.candidate_id}-{label}",
                    timestamp=timestamp,
                    frame_path=relative,
                    hash_sha256=hashlib.sha256(jpeg).hexdigest(),
                    selection_reason=f"candidate_{label}",
                    quality_score=None,
                )
            )
        return frames

    def _resolve_video(self, metadata: VideoMetadata) -> Path:
        target = (self.media_root / metadata.media_path).resolve()
        if not target.is_relative_to(self.media_root) or not target.is_file():
            raise ToolExecutionError("VIDEO_NOT_FOUND", "VLM için local video bulunamadı.")
        return target


async def _fetch_frame(video: Path, timestamp: float) -> bytes:
    return await grab_frame(video, timestamp)


def _atomic_write(target: Path, payload: bytes) -> None:
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)


__all__ = ["LocalKeyframeTool"]
