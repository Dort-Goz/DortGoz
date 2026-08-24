from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dortgoz.pipeline import ingest


@pytest.fixture(autouse=True)
def _clean():
    ingest._clip_tasks.clear()
    yield
    ingest._clip_tasks.clear()


@pytest.mark.asyncio
async def test_same_window_is_encoded_once(monkeypatch) -> None:
    calls: list[tuple[float, float]] = []

    async def fake_grab(_video, start, end, _width):
        calls.append((start, end))
        await asyncio.sleep(0)
        return b"clip"

    monkeypatch.setattr(ingest, "grab_clip", fake_grab)
    video = Path("clip.mp4")

    first, second = await asyncio.gather(
        ingest.shared_clip(video, 0.0, 30.0, 720),
        ingest.shared_clip(video, 0.0, 30.0, 720),
    )
    third = await ingest.shared_clip(video, 0.0, 30.0, 720)

    assert first == second == third == b"clip"
    assert calls == [(0.0, 30.0)]


@pytest.mark.asyncio
async def test_prefetch_serves_the_next_window(monkeypatch) -> None:
    calls: list[tuple[float, float]] = []

    async def fake_grab(_video, start, end, _width):
        calls.append((start, end))
        return b"clip"

    monkeypatch.setattr(ingest, "grab_clip", fake_grab)
    video = Path("clip.mp4")

    ingest.prefetch_clip(video, 30.0, 60.0, 720)
    await asyncio.sleep(0)
    await ingest.shared_clip(video, 30.0, 60.0, 720)

    assert calls == [(30.0, 60.0)]


@pytest.mark.asyncio
async def test_failed_clip_is_retried_not_cached(monkeypatch) -> None:
    attempts = {"n": 0}

    async def flaky(_video, _start, _end, _width):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ingest.FFmpegError("bozuk")
        return b"clip"

    monkeypatch.setattr(ingest, "grab_clip", flaky)
    video = Path("clip.mp4")

    with pytest.raises(ingest.FFmpegError):
        await ingest.shared_clip(video, 0.0, 30.0, 720)
    assert await ingest.shared_clip(video, 0.0, 30.0, 720) == b"clip"
    assert attempts["n"] == 2
