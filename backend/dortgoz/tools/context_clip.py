

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..domain.candidate import CandidateEvent
from ..domain.context import ContextClip
from ..domain.video import VideoMetadata
from .protocols import ToolExecutionError

ClipWriter = Callable[[Path, Path, float, float, float], Awaitable[None]]

_BROWSER_ENCODERS = ("libx264", "libopenh264")
_video_encoder: str | None = None


async def browser_video_encoder() -> str:
    global _video_encoder
    if _video_encoder is not None:
        return _video_encoder
    available = b""
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "quiet", "-encoders",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        available, _ = await asyncio.wait_for(process.communicate(), timeout=20)
    except (FileNotFoundError, TimeoutError, OSError):
        available = b""
    text = available.decode("utf-8", "replace")
    _video_encoder = next(
        (name for name in _BROWSER_ENCODERS if f" {name} " in text), "mpeg4"
    )
    return _video_encoder


class LocalContextClipTool:
    def __init__(
        self,
        *,
        media_root: Path,
        workspace_root: Path,
        fps: float = 2.0,
        timeout_seconds: float = 90.0,
        clip_writer: ClipWriter | None = None,
    ) -> None:
        if fps <= 0 or timeout_seconds <= 0:
            raise ValueError("context clip fps ve timeout pozitif olmalı")
        self.media_root = media_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.runs_root = (self.workspace_root / "runs").resolve()
        self.fps = fps
        self.timeout_seconds = timeout_seconds
        self.clip_writer = clip_writer or write_context_clip

    async def create(
        self,
        metadata: VideoMetadata,
        candidate: CandidateEvent,
        *,
        analysis_id: str,
        before_seconds: float,
        after_seconds: float,
        expanded: bool,
    ) -> ContextClip:
        if before_seconds < 0 or after_seconds < 0:
            raise ToolExecutionError("CONTEXT_RANGE_INVALID", "Context süreleri negatif olamaz.")
        start = max(0.0, candidate.start_time - before_seconds)
        end = min(metadata.duration_seconds, candidate.end_time + after_seconds)
        if not start <= candidate.peak_time <= end or end <= start:
            raise ToolExecutionError("CONTEXT_RANGE_INVALID", "Context clip aralığı geçersiz.")
        source = self._resolve_video(metadata)
        output_dir = (self.runs_root / analysis_id / candidate.candidate_id).resolve()
        if not output_dir.is_relative_to(self.runs_root):
            raise ToolExecutionError("ARTIFACT_PATH_REJECTED", "Context yolu runs kökü dışında.")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / ("context-expanded.mp4" if expanded else "candidate-context.mp4")
        try:
            await self.clip_writer(source, target, start, end, self.timeout_seconds)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("CONTEXT_CLIP_FAILED", "Yerel context clip üretilemedi.") from exc
        if not target.is_file() or target.stat().st_size == 0:
            raise ToolExecutionError("CONTEXT_CLIP_FAILED", "Yerel context clip boş üretildi.")
        return ContextClip(
            candidate_id=candidate.candidate_id,
            clip_start=start,
            clip_end=end,
            clip_path=target.relative_to(self.workspace_root).as_posix(),
            frame_count=max(1, round((end - start) * self.fps)),
            fps=self.fps,
            hash_sha256=_file_hash(target),
            expanded=expanded,
        )

    def _resolve_video(self, metadata: VideoMetadata) -> Path:
        target = (self.media_root / metadata.media_path).resolve()
        if not target.is_relative_to(self.media_root) or not target.is_file():
            raise ToolExecutionError("VIDEO_NOT_FOUND", "Context için local video bulunamadı.")
        return target


async def write_context_clip(
    source: Path, target: Path, start: float, end: float, timeout_seconds: float
) -> None:
    temporary = target.with_name(f".{target.stem}.tmp{target.suffix}")
    encoder = await browser_video_encoder()
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            encoder,
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(temporary),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ToolExecutionError("FFMPEG_UNAVAILABLE", "ffmpeg context clip için bulunamadı.") from exc
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise ToolExecutionError("CONTEXT_CLIP_TIMEOUT", "Context clip zaman aşımına uğradı.") from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace")[-200:]
        raise ToolExecutionError("CONTEXT_CLIP_FAILED", detail or "ffmpeg context clip başarısız")
    temporary.replace(target)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["LocalContextClipTool", "write_context_clip"]
