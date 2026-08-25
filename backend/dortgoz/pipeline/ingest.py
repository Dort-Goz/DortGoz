from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from pathlib import Path

GRAY_W, GRAY_H = 64, 48
_FRAME_BYTES = GRAY_W * GRAY_H

PIXEL_TAU = 18
BG_ALPHA = 0.02


@dataclass(frozen=True)
class MotionSample:

    t: float
    changed: float
    fg: float
    mad: float
    grid: bytes = b""

    @property
    def activity(self) -> float:
        return max(self.changed, self.fg)


class FFmpegError(RuntimeError):
    pass


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(err.decode("utf-8", "replace")[-400:])
    return out


async def probe_duration(video: Path) -> float:
    out = await _run(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video),
    )
    return float(out.decode().strip())

async def grab_clip(video: Path, start: float, end: float, width: int = 720) -> bytes:
    if start < 0 or end <= start:
        raise ValueError("video aralığı geçersiz")
    return await _run(
        "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(video), "-map", "0:v:0", "-an",
        "-vf", f"scale={width}:-2:force_original_aspect_ratio=decrease",
        "-c:v", "mpeg4", "-q:v", "5", "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov", "-",
    )


async def motion_profile(video: Path, base_fps: float = 1.0) -> list[MotionSample]:
    raw = await _run(
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"fps={base_fps},scale={GRAY_W}:{GRAY_H}",
        "-pix_fmt", "gray", "-f", "rawvideo", "-",
    )
    profile: list[MotionSample] = []
    prev: bytes | None = None
    background: list[float] | None = None
    for idx in range(0, len(raw) - _FRAME_BYTES + 1, _FRAME_BYTES):
        frame = raw[idx:idx + _FRAME_BYTES]
        t = (idx // _FRAME_BYTES) / base_fps

        if prev is None:
            mad = changed = 0.0
            background = [float(p) for p in frame]
        else:
            total = 0
            hits = 0
            for a, b in zip(frame, prev):
                d = a - b if a > b else b - a
                total += d
                if d > PIXEL_TAU:
                    hits += 1
            mad = total / (_FRAME_BYTES * 255)
            changed = hits / _FRAME_BYTES

        assert background is not None
        fg = sum(1 for a, b in zip(frame, background)
                 if abs(a - b) > PIXEL_TAU) / _FRAME_BYTES
        background = [b + BG_ALPHA * (a - b) for a, b in zip(frame, background)]

        profile.append(MotionSample(t=t, changed=changed, fg=fg, mad=mad,
                                    grid=frame))
        prev = frame
    return profile


def noise_floor(profile: list[MotionSample], percentile: float = 0.05) -> float:
    vals = sorted(s.activity for s in profile)
    if not vals:
        return 0.0
    return vals[min(int(len(vals) * percentile), len(vals) - 1)]


def adaptive_gate(profile: list[MotionSample], k: float = 4.0,
                  minimum: float = 0.004, ceiling: float = 0.010) -> float:
    return min(max(noise_floor(profile) * k, minimum), ceiling)


async def _grab_frame_ffmpeg(video: Path, t: float, width: int) -> bytes:
    last_err: FFmpegError | None = None
    for attempt_t in (t, max(0.0, t - 1.0), max(0.0, t - 2.5)):
        try:
            out = await _run(
                "ffmpeg", "-v", "error", "-ss", f"{attempt_t:.3f}", "-i", str(video),
                "-frames:v", "1", "-vf", f"scale={width}:-2",
                "-f", "image2", "-c:v", "mjpeg", "-",
            )
            if out:
                return out
        except FFmpegError as exc:
            last_err = exc
    raise last_err or FFmpegError(f"kare alınamadı: t={t:.3f} {video.name}")


_frame_tasks: dict[tuple[str, float, int], asyncio.Task] = {}
_FRAME_TASKS_MAX = 128


def _frame_task(video: Path, t: float, width: int) -> asyncio.Task:
    key = (str(video), round(t, 3), width)
    task = _frame_tasks.pop(key, None)
    loop = asyncio.get_running_loop()
    stale = task is not None and (
        (task.done() and (task.cancelled() or task.exception() is not None))
        or (not task.done() and task.get_loop() is not loop)
    )
    if task is None or stale:
        task = loop.create_task(_grab_frame_ffmpeg(video, t, width))
        task.add_done_callback(
            lambda tk: tk.exception() if not tk.cancelled() else None)
    _frame_tasks[key] = task
    while len(_frame_tasks) > _FRAME_TASKS_MAX:
        _frame_tasks.pop(next(iter(_frame_tasks)))
    return task


def prefetch_frames(video: Path, ts: list[float], width: int = 512) -> None:
    for t in ts:
        _frame_task(video, t, width)

_clip_tasks: dict[tuple[str, float, float, int, str], asyncio.Task] = {}
_CLIP_TASKS_MAX = 4

_clip_owner_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "dortgoz_clip_owner", default=""
)


def set_clip_owner(owner: str) -> None:
    _clip_owner_var.set(owner)


def _clip_owner() -> str:
    return _clip_owner_var.get()


def clip_task(video: Path, start: float, end: float, width: int) -> asyncio.Task:
    key = (str(video), round(start, 3), round(end, 3), width, _clip_owner())
    task = _clip_tasks.pop(key, None)
    loop = asyncio.get_running_loop()
    stale = task is not None and (
        (task.done() and (task.cancelled() or task.exception() is not None))
        or (not task.done() and task.get_loop() is not loop)
    )
    if task is None or stale:
        task = loop.create_task(grab_clip(video, start, end, width))
        task.add_done_callback(
            lambda tk: tk.exception() if not tk.cancelled() else None)
    _clip_tasks[key] = task
    while len(_clip_tasks) > _CLIP_TASKS_MAX:
        _clip_tasks.pop(next(iter(_clip_tasks)))
    return task

def prefetch_clip(video: Path, start: float, end: float, width: int) -> None:
    if end > start:
        clip_task(video, start, end, width)

async def shared_clip(video: Path, start: float, end: float, width: int) -> bytes:
    return await asyncio.shield(clip_task(video, start, end, width))


async def drain_clip_tasks() -> None:
    loop = asyncio.get_running_loop()
    owner = _clip_owner()
    matched = {
        key: task for key, task in _clip_tasks.items()
        if task.get_loop() is loop and key[-1] == owner
    }
    for key in matched:
        _clip_tasks.pop(key, None)
    tasks = [task for task in matched.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

async def drain_frame_tasks(video: Path) -> None:


    loop = asyncio.get_running_loop()
    tasks = [
        task
        for (video_path, _timestamp, _width), task in _frame_tasks.items()
        if video_path == str(video) and task.get_loop() is loop and not task.done()
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def grab_frame(video: Path, t: float, width: int = 512) -> bytes:
    return await asyncio.shield(_frame_task(video, t, width))
