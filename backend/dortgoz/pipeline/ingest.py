from __future__ import annotations

import asyncio
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


async def grab_frame(video: Path, t: float, width: int = 512) -> bytes:
    return await asyncio.shield(_frame_task(video, t, width))
