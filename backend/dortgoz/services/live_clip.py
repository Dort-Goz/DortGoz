from __future__ import annotations

import asyncio
import re
from pathlib import Path

LIVE_DIR_NAME = "canli"
LIVE_RUN_PREFIX = "canli-"
SEGMENT_PATTERN = re.compile(r"^seg_(\d+)$")
ADJACENCY_TOLERANCE_SECONDS = 2.0


def _written_bytes(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


class LiveClipError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def segment_start_epoch(path: Path) -> float | None:
    match = SEGMENT_PATTERN.match(path.stem)
    return float(match.group(1)) if match else None


def is_live_segment(media_path: str) -> bool:
    candidate = Path(media_path)
    parts = candidate.parts
    return (
        len(parts) >= 3
        and parts[0] == LIVE_DIR_NAME
        and SEGMENT_PATTERN.match(candidate.stem) is not None
    )


def feed_from_run_id(run_id: str) -> str:
    if not run_id.startswith(LIVE_RUN_PREFIX):
        return ""
    head, _, tail = run_id[len(LIVE_RUN_PREFIX):].rpartition("-")
    return head if head and tail else ""


def feed_from_media_path(media_path: str) -> str:
    return Path(media_path).parts[1] if is_live_segment(media_path) else ""


def segments_covering(
    directory: Path,
    start_epoch: float,
    end_epoch: float,
    segment_seconds: float,
) -> list[Path]:
    if end_epoch <= start_epoch or segment_seconds <= 0:
        return []
    found: list[tuple[float, Path]] = []
    for candidate in directory.glob("seg_*.mp4"):
        epoch = segment_start_epoch(candidate)
        if epoch is None or candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.stat().st_size == 0:
            continue
        if epoch < end_epoch and epoch + segment_seconds > start_epoch:
            found.append((epoch, candidate))
    found.sort()
    ordered: list[Path] = []
    previous_end: float | None = None
    for epoch, path in found:
        if previous_end is not None and epoch - previous_end > ADJACENCY_TOLERANCE_SECONDS:
            break
        ordered.append(path)
        previous_end = epoch + segment_seconds
    return ordered


async def concat_segments(segments: list[Path], target: Path, timeout_seconds: float) -> None:
    if len(segments) < 2:
        raise LiveClipError("LIVE_CONCAT_INPUT", "Birleştirme en az iki segment gerektirir.")
    listing = target.with_name(f".{target.stem}.concat.txt")
    listing.write_text(
        "".join(f"file '{path.as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                for path in segments),
        encoding="utf-8",
    )
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-map", "0:v:0", "-an", "-c", "copy",
            "-y", str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        listing.unlink(missing_ok=True)
        raise LiveClipError("FFMPEG_UNAVAILABLE", "ffmpeg segment birleştirme için yok.") from exc
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        listing.unlink(missing_ok=True)
        raise LiveClipError("LIVE_CONCAT_TIMEOUT", "Segment birleştirme zaman aşımına uğradı.") from exc
    finally:
        listing.unlink(missing_ok=True)
    written = await asyncio.to_thread(_written_bytes, target)
    if process.returncode != 0 or written == 0:
        detail = stderr.decode("utf-8", "replace")[-200:]
        raise LiveClipError("LIVE_CONCAT_FAILED", detail or "segment birleştirme başarısız")


__all__ = [
    "ADJACENCY_TOLERANCE_SECONDS",
    "LIVE_RUN_PREFIX",
    "LiveClipError",
    "concat_segments",
    "feed_from_media_path",
    "feed_from_run_id",
    "is_live_segment",
    "segment_start_epoch",
    "segments_covering",
]
