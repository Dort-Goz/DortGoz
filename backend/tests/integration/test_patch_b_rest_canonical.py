from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from dortgoz import main
from dortgoz.agent.orchestrator import EventOrchestrator
from dortgoz.api import router as api_module
from dortgoz.api.router import ApiRuntime
from dortgoz.domain.video import VideoMetadata
from dortgoz.events import Event, OperatorMessage
from dortgoz.services.analysis_job import (
    AnalysisJobStatus,
    CanonicalAnalysisJobService,
    EffectiveRuntimeConfig,
)
from dortgoz.services.mock_vertical import MockVerticalAnalysisService


class FakeManager:
    async def broadcast(self, _event: Event, feed: str = "") -> None:
        del feed


class BlockingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.cancelled: list[str] = []
        self.release = asyncio.Event()

    async def __call__(
        self,
        _manager: FakeManager,
        video: str,
        run_id: str,
        **kwargs: str,
    ) -> None:
        self.calls.append((video, run_id, kwargs))
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.append(run_id)
            raise


def defaults() -> EffectiveRuntimeConfig:
    return EffectiveRuntimeConfig(
        model="default-model",
        system_prompt="default-system",
        task_prompt="default-task",
    )


def metadata(video_id: str) -> VideoMetadata:
    return VideoMetadata(
        video_id=video_id,
        original_filename="fixture.mp4",
        stored_filename=f"{video_id}.mp4",
        media_path=f"{video_id}.mp4",
        file_size_bytes=1024,
        file_hash_sha256="b" * 64,
        container="mov",
        codec="h264",
        width=640,
        height=480,
        fps=25,
        duration_seconds=90,
        has_audio=False,
        time_base="1/12800",
    )


@dataclass(slots=True)
class CanonicalApi:
    client: httpx.AsyncClient
    runtime: ApiRuntime
    jobs: CanonicalAnalysisJobService
    runner: BlockingRunner
    runs_dir: Path

    def add_video(self, video_id: str) -> VideoMetadata:
        video = metadata(video_id)
        return self.runtime.repository.create_video(video)


@pytest.fixture
async def canonical_api(monkeypatch, tmp_path: Path) -> AsyncIterator[CanonicalApi]:
    monkeypatch.setattr(api_module.settings, "event_store_path", None)
    runtime = ApiRuntime()
    runner = BlockingRunner()
    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=24,
        run_video=runner,
        defaults=defaults,
    )
    monkeypatch.setattr(api_module, "runtime", runtime)
    monkeypatch.setattr(main.app.state, "analysis_jobs", jobs)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield CanonicalApi(client, runtime, jobs, runner, tmp_path)
    await jobs.cancel_all()


async def wait_for_calls(runner: BlockingRunner, count: int) -> None:
    for _ in range(100):
        if len(runner.calls) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"runner çağrı sayısı {count} olmadı")


async def wait_for_status(
    client: httpx.AsyncClient,
    analysis_id: str,
    expected: AnalysisJobStatus,
) -> None:
    for _ in range(100):
        response = await client.get(f"/api/analyses/{analysis_id}/status")
        if response.status_code == 200 and response.json()["status"] == expected.value:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"REST job durumu {expected} olmadı")


@pytest.mark.asyncio
async def test_rest_start_uses_canonical_job_and_writes_no_legacy_analysis(
    canonical_api: CanonicalApi,
) -> None:
    video = canonical_api.add_video("00000000-0000-0000-0000-000000000101")

    response = await canonical_api.client.post(
        f"/api/videos/{video.video_id}/analyze",
        json={"profile": "mock"},
    )
    assert response.status_code == 202
    payload = response.json()
    await wait_for_calls(canonical_api.runner, 1)

    analysis_id = payload["analysis_id"]
    assert canonical_api.runner.calls[0][0] == video.stored_filename
    assert canonical_api.runner.calls[0][1] == analysis_id
    assert payload["status_url"] == f"/api/analyses/{analysis_id}/status"
    assert payload["result_url"] == f"/api/runs/{analysis_id}"
    assert canonical_api.runtime.repository.get_analysis(analysis_id) is None
    assert canonical_api.runtime.jobs == {}
    metrics = canonical_api.runtime.repository.snapshot_metrics()
    assert metrics["total_analyses"] == 0
    assert metrics["total_events"] == 0

    status = await canonical_api.client.get(payload["status_url"])
    assert status.status_code == 200
    assert status.json() == {"analysis_id": analysis_id, "status": "running"}


@pytest.mark.parametrize("profile", ["mock", "candidate", "local_vlm"])
@pytest.mark.asyncio
async def test_deprecated_profiles_never_invoke_legacy_execution(
    canonical_api: CanonicalApi,
    monkeypatch,
    profile: str,
) -> None:
    video = canonical_api.add_video("00000000-0000-0000-0000-000000000102")
    calls: Counter[str] = Counter()

    async def forbidden_vertical(*_args, **_kwargs):
        calls["mock_vertical"] += 1
        raise AssertionError("legacy vertical çağrılmamalı")

    async def forbidden_orchestrator(*_args, **_kwargs):
        calls["event_orchestrator"] += 1
        raise AssertionError("legacy orchestrator çağrılmamalı")

    monkeypatch.setattr(MockVerticalAnalysisService, "analyze", forbidden_vertical)
    monkeypatch.setattr(EventOrchestrator, "run", forbidden_orchestrator)

    response = await canonical_api.client.post(
        f"/api/videos/{video.video_id}/analyze",
        json={"profile": profile},
    )
    assert response.status_code == 202
    await wait_for_calls(canonical_api.runner, 1)
    assert calls == Counter()


@pytest.mark.asyncio
async def test_rest_and_ws_concurrent_start_share_one_analysis(
    canonical_api: CanonicalApi,
    monkeypatch,
) -> None:
    video = canonical_api.add_video("00000000-0000-0000-0000-000000000103")
    snapshots = []
    original_start = canonical_api.jobs.start

    async def recording_start(*args, **kwargs):
        snapshot = await original_start(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(canonical_api.jobs, "start", recording_start)
    rest_response, _ = await asyncio.gather(
        canonical_api.client.post(
            f"/api/videos/{video.video_id}/analyze",
            json={"profile": "candidate"},
        ),
        main.start_run(OperatorMessage(kind="start_run", video=video.stored_filename)),
    )
    await wait_for_calls(canonical_api.runner, 1)

    assert rest_response.status_code == 202
    assert len(canonical_api.runner.calls) == 1
    assert len(snapshots) == 2
    assert len({snapshot.analysis_id for snapshot in snapshots}) == 1
    assert rest_response.json()["analysis_id"] == snapshots[0].analysis_id


@pytest.mark.asyncio
async def test_twenty_concurrent_rest_starts_are_single_flight(
    canonical_api: CanonicalApi,
) -> None:
    video = canonical_api.add_video("00000000-0000-0000-0000-000000000104")

    responses = await asyncio.gather(
        *(
            canonical_api.client.post(
                f"/api/videos/{video.video_id}/analyze",
                json={"profile": "mock"},
            )
            for _ in range(20)
        )
    )
    await wait_for_calls(canonical_api.runner, 1)

    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["analysis_id"] for response in responses}) == 1
    assert len(canonical_api.runner.calls) == 1


@pytest.mark.asyncio
async def test_rest_feed_conflicts_and_independence_reuse_service_policy(
    canonical_api: CanonicalApi,
) -> None:
    first_video = canonical_api.add_video("00000000-0000-0000-0000-000000000105")
    second_video = canonical_api.add_video("00000000-0000-0000-0000-000000000106")
    first = await canonical_api.client.post(
        f"/api/videos/{first_video.video_id}/analyze",
        json={"feed": "KAM-1"},
    )
    await wait_for_calls(canonical_api.runner, 1)

    video_conflict = await canonical_api.client.post(
        f"/api/videos/{second_video.video_id}/analyze",
        json={"feed": "KAM-1"},
    )
    config_conflict = await canonical_api.client.post(
        f"/api/videos/{first_video.video_id}/analyze",
        json={"feed": "KAM-1", "model": "other-model"},
    )
    independent = await canonical_api.client.post(
        f"/api/videos/{first_video.video_id}/analyze",
        json={"feed": "KAM-2"},
    )
    await wait_for_calls(canonical_api.runner, 2)

    assert first.status_code == 202
    assert video_conflict.status_code == 409
    assert video_conflict.json()["error"]["code"] == "ANALYSIS_CONFLICT"
    assert config_conflict.status_code == 409
    assert config_conflict.json()["error"]["code"] == "ANALYSIS_CONFLICT"
    assert independent.status_code == 202
    assert independent.json()["analysis_id"] != first.json()["analysis_id"]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("done", "completed"),
        ("error", "failed"),
        ("idle", "cancelled"),
        ("processing", "interrupted"),
    ],
)
@pytest.mark.asyncio
async def test_rest_status_uses_canonical_jsonl_mapping(
    canonical_api: CanonicalApi,
    state: str,
    expected: str,
) -> None:
    analysis_id = f"rest-{state}"
    payload = {
        "payload": {
            "type": "run_status",
            "run_id": analysis_id,
            "state": state,
        }
    }
    (canonical_api.runs_dir / f"{analysis_id}.jsonl").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    response = await canonical_api.client.get(f"/api/analyses/{analysis_id}/status")

    assert response.status_code == 200
    assert response.json() == {"analysis_id": analysis_id, "status": expected}


@pytest.mark.asyncio
async def test_task_completion_without_terminal_jsonl_is_interrupted(
    canonical_api: CanonicalApi,
) -> None:
    video = canonical_api.add_video("00000000-0000-0000-0000-000000000107")
    canonical_api.runner.release.set()

    response = await canonical_api.client.post(f"/api/videos/{video.video_id}/analyze", json={})
    assert response.status_code == 202
    await wait_for_status(
        canonical_api.client,
        response.json()["analysis_id"],
        AnalysisJobStatus.INTERRUPTED,
    )


@pytest.mark.asyncio
async def test_rest_cancel_is_per_job_and_idempotent(canonical_api: CanonicalApi) -> None:
    first_video = canonical_api.add_video("00000000-0000-0000-0000-000000000108")
    second_video = canonical_api.add_video("00000000-0000-0000-0000-000000000109")
    first = await canonical_api.client.post(
        f"/api/videos/{first_video.video_id}/analyze",
        json={"feed": "KAM-1"},
    )
    second = await canonical_api.client.post(
        f"/api/videos/{second_video.video_id}/analyze",
        json={"feed": "KAM-2"},
    )
    await wait_for_calls(canonical_api.runner, 2)
    first_id = first.json()["analysis_id"]
    second_id = second.json()["analysis_id"]

    cancelled = await canonical_api.client.post(f"/api/analyses/{first_id}/cancel")
    repeated = await canonical_api.client.post(f"/api/analyses/{first_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json() == {"analysis_id": first_id, "status": "cancelled"}
    assert repeated.json() == cancelled.json()
    assert canonical_api.runner.cancelled == [first_id]
    assert await canonical_api.jobs.active_count() == 1
    assert await canonical_api.jobs.status(second_id) == AnalysisJobStatus.RUNNING


@pytest.mark.asyncio
async def test_rest_errors_are_typed_and_never_fall_back_to_legacy(
    canonical_api: CanonicalApi,
    monkeypatch,
) -> None:
    video = canonical_api.add_video("00000000-0000-0000-0000-000000000110")
    legacy_calls = 0

    async def forbidden_vertical(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        raise AssertionError("legacy fallback çağrılmamalı")

    async def failing_start(*_args, **_kwargs):
        raise RuntimeError("injected canonical start failure")

    monkeypatch.setattr(MockVerticalAnalysisService, "analyze", forbidden_vertical)
    monkeypatch.setattr(canonical_api.jobs, "start", failing_start)

    missing = await canonical_api.client.post("/api/videos/does-not-exist/analyze", json={})
    invalid = await canonical_api.client.post(
        f"/api/videos/{video.video_id}/analyze",
        json={"profile": ""},
    )
    failed = await canonical_api.client.post(
        f"/api/videos/{video.video_id}/analyze",
        json={"profile": "mock"},
    )
    missing_status = await canonical_api.client.get("/api/analyses/unknown/status")
    missing_cancel = await canonical_api.client.post("/api/analyses/unknown/cancel")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "VIDEO_NOT_FOUND"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "ANALYSIS_START_FAILED"
    assert failed.json()["error"]["details"] == {"reason": "RuntimeError"}
    assert missing_status.status_code == 404
    assert missing_status.json()["error"]["code"] == "ANALYSIS_NOT_FOUND"
    assert missing_cancel.status_code == 404
    assert missing_cancel.json()["error"]["code"] == "ANALYSIS_NOT_FOUND"
    assert legacy_calls == 0
    metrics = canonical_api.runtime.repository.snapshot_metrics()
    assert metrics["total_analyses"] == 0
    assert metrics["total_events"] == 0
    assert canonical_api.runner.calls == []
