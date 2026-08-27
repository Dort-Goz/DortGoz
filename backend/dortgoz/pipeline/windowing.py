from __future__ import annotations

from .ingest import MotionSample

WINDOW_SECONDS = 30.0
MIN_TAIL_SECONDS = 8.0


def windows(duration: float, length: float = WINDOW_SECONDS) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    out: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        out.append((start, min(start + length, duration)))
        start += length
    if len(out) > 1 and out[-1][1] - out[-1][0] < MIN_TAIL_SECONDS:
        out[-2:] = [(out[-2][0], out[-1][1])]
    return out


def activity_windows(
    profile: list[MotionSample],
    duration: float,
    gate: float,
    *,
    min_len: float = 8.0,
    max_len: float = 45.0,
    preroll: float = 3.0,
    quiet_tail: float = 6.0,
) -> list[tuple[float, float]]:
    if duration <= 0 or not profile:
        return []
    step = profile[1].t - profile[0].t if len(profile) > 1 else 1.0
    step = step if step > 0 else 1.0

    out: list[tuple[float, float]] = []
    start: float | None = None
    quiet_for = 0.0
    for s in profile:
        active = s.activity >= gate
        if start is None:
            if active:
                start = max(0.0, s.t - preroll)
                quiet_for = 0.0
            continue
        quiet_for = 0.0 if active else quiet_for + step
        too_long = s.t - start >= max_len
        if quiet_for >= quiet_tail or too_long:
            end = s.t - (quiet_for - step) if quiet_for >= quiet_tail else s.t
            end = min(duration, max(end, start + min_len))
            out.append((start, end))
            start = None if quiet_for >= quiet_tail else max(0.0, s.t)
            quiet_for = 0.0
    if start is not None:
        out.append((start, min(duration, max(start + min_len, profile[-1].t + step))))
    return out


def window_motion(profile: list[MotionSample], start: float, end: float) -> float:
    scores = [s.activity for s in profile if start <= s.t < end]
    return max(scores) if scores else 0.0


def activity_levels(profile: list[MotionSample], start: float, end: float,
                    gate: float, cap: int = 3) -> list[int]:
    levels: list[int] = []
    for sample in profile:
        if not start <= sample.t < end:
            continue
        if sample.activity < gate or gate <= 0:
            levels.append(0)
            continue
        excess = (sample.activity - gate) / gate
        levels.append(min(cap, 1 + int(excess)))
    return levels


def select_keyframes(
    profile: list[MotionSample],
    start: float,
    end: float,
    k: int = 6,
    min_gap: float | None = None,
) -> list[float]:
    samples = [s for s in profile if start <= s.t < end]
    if not samples:
        return _uniform(start, end, k)
    if min_gap is None:
        min_gap = (end - start) / (k * 1.5) if k else 0.0

    picked: list[float] = []
    for sample in sorted(samples, key=lambda s: (-s.activity, s.t)):
        if sample.activity <= 0.0:
            break
        if all(abs(sample.t - p) >= min_gap for p in picked):
            picked.append(sample.t)
        if len(picked) == k:
            break

    if len(picked) < k:
        for t in _uniform(start, end, k):
            if len(picked) == k:
                break
            if all(abs(t - p) >= min_gap for p in picked):
                picked.append(t)
    return _dedup(sorted(picked), samples)


def _dedup(times: list[float], samples: list[MotionSample],
           threshold: float | None = None) -> list[float]:
    if threshold is None:
        from ..config import settings
        threshold = settings.keyframe_dedup
    if threshold <= 0 or len(times) <= 2:
        return times

    from .ingest import PIXEL_TAU

    def grid_at(t: float) -> bytes:
        return min(samples, key=lambda s: abs(s.t - t)).grid

    def diff(a: bytes, b: bytes) -> float:
        if not a or not b or len(a) != len(b):
            return 1.0
        hits = sum(1 for x, y in zip(a, b)
                   if (x - y if x > y else y - x) > PIXEL_TAU)
        return hits / len(a)

    kept: list[float] = [times[0]]
    kept_grids = [grid_at(times[0])]
    for t in times[1:]:
        g = grid_at(t)
        if all(diff(g, kg) >= threshold for kg in kept_grids):
            kept.append(t)
            kept_grids.append(g)
    if len(kept) < 2:
        kept = [times[0], times[-1]]
    return kept


def _uniform(start: float, end: float, k: int) -> list[float]:
    if k <= 0 or end <= start:
        return []
    step = (end - start) / k
    return [start + step * (i + 0.5) for i in range(k)]
