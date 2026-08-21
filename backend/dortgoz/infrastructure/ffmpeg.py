from __future__ import annotations

import asyncio
import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..domain.video import VideoErrorCode, VideoIngestError, VideoProbe

SUPPORTED_CONTAINERS = frozenset({"avi", "matroska", "mov", "mp4", "mpeg", "mpegts", "webm"})
SUPPORTED_CODECS = frozenset({"av1", "h264", "hevc", "mjpeg", "mpeg4", "vp8", "vp9"})


def _rate(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def parse_probe_json(data: dict[str, Any]) -> VideoProbe:
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video is None:
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "video akışı bulunamadı")

    format_info = data.get("format") or {}
    avg_rate = _rate(video.get("avg_frame_rate"))
    real_rate = _rate(video.get("r_frame_rate"))
    fps = avg_rate or real_rate
    if not math.isfinite(fps) or fps <= 0:
        raise VideoIngestError(VideoErrorCode.INVALID_FPS, "geçerli FPS bulunamadı")

    raw_duration = video.get("duration") or format_info.get("duration")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        duration = 0.0
    if not math.isfinite(duration) or duration <= 0:
        raise VideoIngestError(VideoErrorCode.INVALID_DURATION, "video süresi pozitif değil")

    format_names = {
        item.strip().lower()
        for item in str(format_info.get("format_name") or "unknown").split(",")
    }
    supported_formats = format_names & SUPPORTED_CONTAINERS
    if not supported_formats:
        rendered = ",".join(sorted(format_names))
        raise VideoIngestError(
            VideoErrorCode.UNSUPPORTED_CONTAINER,
            f"desteklenmeyen container: {rendered}",
        )
    format_name = sorted(supported_formats)[0]
    codec = str(video.get("codec_name") or "unknown").lower()
    if codec not in SUPPORTED_CODECS:
        raise VideoIngestError(
            VideoErrorCode.UNSUPPORTED_CODEC, f"desteklenmeyen codec: {codec}"
        )
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise VideoIngestError(
            VideoErrorCode.DECODE_FAILED, "geçerli video çözünürlüğü bulunamadı"
        )
    time_base = str(video.get("time_base") or "")
    if _rate(time_base) <= 0:
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "video time_base bulunamadı")

    return VideoProbe(
        container=format_name,
        codec=codec,
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration,
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
        time_base=time_base,
        variable_fps=bool(avg_rate and real_rate and abs(avg_rate - real_rate) > 0.01),
    )


async def probe_video(path: Path, timeout_seconds: float = 30.0) -> VideoProbe:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "ffprobe bulunamadı") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "ffprobe zaman aşımı") from exc

    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace")[-300:]
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, detail or "ffprobe başarısız")
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "ffprobe geçersiz JSON döndürdü") from exc
    probe = parse_probe_json(raw)
    await check_decode(path, timeout_seconds=max(timeout_seconds, 120.0))
    return probe


async def check_decode(path: Path, timeout_seconds: float = 120.0) -> None:

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "null",
            "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "ffmpeg bulunamadı") from exc

    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "decode zaman aşımı") from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace")[-300:]
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, detail or "video decode edilemedi")
