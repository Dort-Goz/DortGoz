from __future__ import annotations

import json

import pytest

from dortgoz.config import settings
from dortgoz.services.live_cctv import (
    LiveFeedWorker,
    load_feeds,
    plan_segments,
)


def test_load_feeds_valid(tmp_path):
    p = tmp_path / "live_feeds.json"
    p.write_text(json.dumps([{"name": "kavsak1", "url": "https://ör/x.m3u8"}]))
    assert load_feeds(p)[0]["name"] == "kavsak1"


def test_load_feeds_falls_back_to_example(tmp_path):
    example = tmp_path / "live_feeds.example.json"
    example.write_text(json.dumps([{"name": "a", "url": "u"}]))
    assert load_feeds(tmp_path / "live_feeds.json")[0]["name"] == "a"


@pytest.mark.parametrize("bad", [
    [],
    [{"name": "x"}],
    [{"name": "a/b", "url": "u"}],
    [{"name": "a", "url": "u"}, {"name": "a", "url": "v"}],
])
def test_load_feeds_rejects_invalid(tmp_path, bad):
    p = tmp_path / "live_feeds.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load_feeds(p)


def test_plan_keeps_all_when_under_backlog(tmp_path):
    segs = [tmp_path / f"seg_{i}.mp4" for i in range(2)]
    drop, pending = plan_segments(segs, max_backlog=2)
    assert drop == [] and pending == segs


def test_plan_drops_oldest_beyond_backlog(tmp_path):
    segs = [tmp_path / f"seg_{i}.mp4" for i in range(5)]
    drop, pending = plan_segments(segs, max_backlog=2)
    assert drop == segs[:3]
    assert pending == segs[3:]


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "runs_dir", tmp_path / "runs")
    settings.runs_dir.mkdir(parents=True)
    w = LiveFeedWorker("kavsak1", "https://ör/x.m3u8", manager=None)
    w.dir.mkdir(parents=True)
    w.running = True

    async def no_snapshot(seg):
        w.status.snapshot = "/media/canli/kavsak1/latest.jpg"
    monkeypatch.setattr(w, "_snapshot", no_snapshot)
    return w


def _seg(worker, epoch: int, size: int = 100):
    p = worker.dir / f"seg_{epoch}.mp4"
    p.write_bytes(b"x" * size)
    return p


@pytest.mark.asyncio
async def test_step_processes_oldest_closed_segment(worker, monkeypatch):
    calls = []

    async def fake_run_video(manager, video, run_id, **kw):
        calls.append((video, run_id, kw))
    monkeypatch.setattr("dortgoz.pipeline.runner.run_video", fake_run_video)

    _seg(worker, 1000)
    _seg(worker, 1030)
    assert await worker._step() is True

    (video, run_id, kw) = calls[0]
    assert video == "canli/kavsak1/seg_1000.mp4"
    assert run_id == "canli-kavsak1-1000"
    assert kw["live"] is True and kw["feed"] == "kavsak1"
    assert worker.status.segments_done == 1
    assert worker.status.lag_s is not None
    assert worker.status.snapshot.endswith("latest.jpg")


@pytest.mark.asyncio
async def test_step_drops_backlog_and_counts_seconds(worker, monkeypatch):
    async def fake_run_video(*a, **kw): ...
    monkeypatch.setattr("dortgoz.pipeline.runner.run_video", fake_run_video)
    monkeypatch.setattr(settings, "live_max_backlog", 2)

    for i in range(6):
        _seg(worker, 1000 + i * 30)
    await worker._step()

    assert worker.status.dropped_s == 3 * settings.live_segment_seconds
    assert worker.status.segments_done == 1
    assert not (worker.dir / "seg_1000.mp4").exists()


@pytest.mark.asyncio
async def test_segment_failure_does_not_stop_feed(worker, monkeypatch):
    async def broken_run_video(*a, **kw):
        raise RuntimeError("model koptu")
    monkeypatch.setattr("dortgoz.pipeline.runner.run_video", broken_run_video)

    _seg(worker, 1000)
    _seg(worker, 1030)
    assert await worker._step() is True
    assert "model koptu" in worker.status.last_error
    assert "seg_1000.mp4" in worker._done


@pytest.mark.asyncio
async def test_prune_keeps_recent_segments_and_runs(worker, monkeypatch):
    async def fake_run_video(*a, **kw): ...
    monkeypatch.setattr("dortgoz.pipeline.runner.run_video", fake_run_video)
    monkeypatch.setattr(settings, "live_keep_segments", 2)
    monkeypatch.setattr(settings, "live_keep_runs", 2)
    for i in range(30):
        (settings.runs_dir / f"canli-kavsak1-{i:03d}.jsonl").write_text("{}")

    for i in range(5):
        _seg(worker, 1000 + i * 30)
    for _ in range(4):
        await worker._step()

    kept = sorted(p.name for p in worker.dir.glob("seg_*.mp4"))
    assert len(kept) <= 3
    runs = list(settings.runs_dir.glob("canli-kavsak1-*.jsonl"))
    assert len(runs) <= settings.live_keep_runs


@pytest.mark.asyncio
async def test_empty_dir_step_is_idle(worker):
    assert await worker._step() is False


def test_wipe_stale_clears_previous_session_segments(worker):
    _seg(worker, 1000)
    _seg(worker, 1030)
    (worker.dir / "latest.jpg").write_bytes(b"jpg")
    worker._wipe_stale()
    assert list(worker.dir.glob("seg_*.mp4")) == []
    assert (worker.dir / "latest.jpg").exists()
