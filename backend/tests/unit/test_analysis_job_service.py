from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dortgoz.events import Event, OperatorMessage, RunStatus
from dortgoz.services.analysis_job import (
    AnalysisJobCapacityError,
    AnalysisJobConflict,
    AnalysisJobNotReady,
    AnalysisJobStatus,
    CanonicalAnalysisJobService,
    EffectiveRuntimeConfig,
    iter_run_lines,
    resolve_jsonl_status,
)
from dortgoz.services.execution_coordinator import (
    ExclusiveWorkload,
    ExecutionCoordinator,
    LiveWorkloadActive,
)


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


def service(tmp_path: Path, runner: BlockingRunner, *, max_active: int = 24):
    return CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=max_active,
        run_video=runner,
        defaults=defaults,
    )


async def wait_for_calls(runner: BlockingRunner, count: int) -> None:
    for _ in range(100):
        if len(runner.calls) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"runner çağrı sayısı {count} olmadı")


async def wait_for_status(
    jobs: CanonicalAnalysisJobService,
    analysis_id: str,
    expected: AnalysisJobStatus,
) -> None:
    for _ in range(100):
        if await jobs.status(analysis_id) == expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"job durumu {expected} olmadı")


def write_status(path: Path, analysis_id: str, state: str) -> None:
    event = Event.wrap(RunStatus(run_id=analysis_id, state=state))  # type: ignore[arg-type]
    path.write_text(event.model_dump_json() + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_twenty_identical_starts_invoke_run_video_once(tmp_path: Path) -> None:
    runner = BlockingRunner()
    jobs = service(tmp_path, runner)

    snapshots = await asyncio.gather(*(jobs.start("camera.mp4") for _ in range(20)))
    await wait_for_calls(runner, 1)

    assert len({item.analysis_id for item in snapshots}) == 1
    assert runner.calls[0][1] == snapshots[0].analysis_id
    assert snapshots[0].run_id == snapshots[0].analysis_id
    await jobs.cancel_all()


@pytest.mark.asyncio
async def test_pre_start_gate_fails_before_runner_or_job_creation(tmp_path: Path) -> None:
    runner = BlockingRunner()

    async def reject() -> None:
        raise AnalysisJobNotReady("SQLite hazır değil")

    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=1,
        run_video=runner,
        defaults=defaults,
        pre_start=reject,
    )

    with pytest.raises(AnalysisJobNotReady, match="SQLite"):
        await jobs.start("camera.mp4")

    assert runner.calls == []


async def test_canonical_job_holds_live_lease_until_runner_teardown(tmp_path: Path) -> None:
    runner = BlockingRunner()
    coordinator = ExecutionCoordinator(tmp_path / "event.sqlite3")
    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=1,
        run_video=runner,
        defaults=defaults,
        execution_coordinator=coordinator,
    )

    snapshot = await jobs.start("camera.mp4")
    await wait_for_calls(runner, 1)
    with pytest.raises(LiveWorkloadActive):
        coordinator.acquire_exclusive(ExclusiveWorkload.SHADOW)

    await jobs.cancel(snapshot.analysis_id)
    shadow = coordinator.acquire_exclusive(ExclusiveWorkload.SHADOW)
    shadow.release()


@pytest.mark.asyncio
async def test_same_feed_reuses_only_same_video_and_effective_config(tmp_path: Path) -> None:
    runner = BlockingRunner()
    jobs = service(tmp_path, runner)
    first = await jobs.start("camera.mp4", feed="KAM-1")

    same = await jobs.start("./camera.mp4", feed="KAM-1", model="default-model")
    assert same.analysis_id == first.analysis_id

    with pytest.raises(AnalysisJobConflict):
        await jobs.start("other.mp4", feed="KAM-1")
    with pytest.raises(AnalysisJobConflict):
        await jobs.start("camera.mp4", feed="KAM-1", task_prompt="changed")

    await wait_for_calls(runner, 1)
    await jobs.cancel_all()


@pytest.mark.asyncio
async def test_different_feeds_are_independent_and_capacity_is_preserved(tmp_path: Path) -> None:
    runner = BlockingRunner()
    jobs = service(tmp_path, runner, max_active=2)

    first = await jobs.start("camera.mp4", feed="KAM-1")
    second = await jobs.start("camera.mp4", feed="KAM-2")
    await wait_for_calls(runner, 2)

    assert first.analysis_id != second.analysis_id
    with pytest.raises(AnalysisJobCapacityError):
        await jobs.start("camera.mp4", feed="KAM-3")
    await jobs.cancel_all()


@pytest.mark.asyncio
async def test_cancel_is_per_job_and_idempotent(tmp_path: Path) -> None:
    runner = BlockingRunner()
    jobs = service(tmp_path, runner)
    first = await jobs.start("one.mp4", feed="KAM-1")
    second = await jobs.start("two.mp4", feed="KAM-2")
    await wait_for_calls(runner, 2)

    assert await jobs.cancel(first.analysis_id) == AnalysisJobStatus.CANCELLED
    assert runner.cancelled == [first.analysis_id]
    assert await jobs.active_count() == 1
    assert await jobs.cancel(first.analysis_id) == AnalysisJobStatus.CANCELLED
    assert runner.cancelled == [first.analysis_id]

    await jobs.cancel_all()
    assert second.analysis_id in runner.cancelled


@pytest.mark.asyncio
async def test_cancel_before_runner_first_turn_is_cancelled_and_releases_feed(
    tmp_path: Path,
) -> None:
    runner = BlockingRunner()
    jobs = service(tmp_path, runner)
    first = await jobs.start("one.mp4")

    assert await jobs.cancel(first.analysis_id) == AnalysisJobStatus.CANCELLED
    assert await jobs.cancel(first.analysis_id) == AnalysisJobStatus.CANCELLED
    assert runner.calls == []

    replacement = await jobs.start("two.mp4")
    assert replacement.analysis_id != first.analysis_id
    await jobs.cancel_all()


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("done", AnalysisJobStatus.COMPLETED),
        ("error", AnalysisJobStatus.FAILED),
        ("idle", AnalysisJobStatus.CANCELLED),
        ("processing", AnalysisJobStatus.INTERRUPTED),
    ],
)
def test_terminal_status_is_reconstructed_from_jsonl(
    tmp_path: Path,
    state: str,
    expected: AnalysisJobStatus,
) -> None:
    analysis_id = f"job-{state}"
    write_status(tmp_path / f"{analysis_id}.jsonl", analysis_id, state)

    assert resolve_jsonl_status(tmp_path, analysis_id, active=False) == expected


def test_partial_final_jsonl_line_is_ignored(tmp_path: Path) -> None:
    analysis_id = "partial-job"
    path = tmp_path / f"{analysis_id}.jsonl"
    write_status(path, analysis_id, "processing")
    with path.open("ab") as stream:
        stream.write(b'{"payload":{"type":"run_status","state":"done"')

    assert (
        resolve_jsonl_status(tmp_path, analysis_id, active=False) == AnalysisJobStatus.INTERRUPTED
    )


@pytest.mark.asyncio
async def test_fresh_service_resolves_pre_restart_jsonl_without_resuming(tmp_path: Path) -> None:
    analysis_id = "pre-restart-job"
    write_status(tmp_path / f"{analysis_id}.jsonl", analysis_id, "done")
    runner = BlockingRunner()
    restarted = service(tmp_path, runner)

    assert await restarted.status(analysis_id) == AnalysisJobStatus.COMPLETED
    assert runner.calls == []


@pytest.mark.asyncio
async def test_task_completion_uses_jsonl_error_not_task_success(tmp_path: Path) -> None:
    async def error_runner(
        _manager: FakeManager,
        _video: str,
        run_id: str,
        **_kwargs: str,
    ) -> None:
        write_status(tmp_path / f"{run_id}.jsonl", run_id, "error")

    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=1,
        run_video=error_runner,
        defaults=defaults,
    )
    snapshot = await jobs.start("camera.mp4")

    await wait_for_status(jobs, snapshot.analysis_id, AnalysisJobStatus.FAILED)


@pytest.mark.asyncio
async def test_successful_task_keeps_jsonl_done_terminal_semantics(tmp_path: Path) -> None:
    async def completed_runner(
        _manager: FakeManager,
        _video: str,
        run_id: str,
        **_kwargs: str,
    ) -> None:
        write_status(tmp_path / f"{run_id}.jsonl", run_id, "done")

    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=1,
        run_video=completed_runner,
        defaults=defaults,
    )
    snapshot = await jobs.start("camera.mp4")

    await wait_for_status(jobs, snapshot.analysis_id, AnalysisJobStatus.COMPLETED)
    assert await jobs.active_count() == 0


@pytest.mark.asyncio
async def test_completed_task_finalizes_incident_media(tmp_path: Path) -> None:
    finalized: list[str] = []

    async def completed_runner(
        _manager: FakeManager,
        _video: str,
        run_id: str,
        **_kwargs: str,
    ) -> None:
        write_status(tmp_path / f"{run_id}.jsonl", run_id, "done")

    async def finalize_run(analysis_id: str) -> None:
        finalized.append(analysis_id)

    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=1,
        run_video=completed_runner,
        defaults=defaults,
        finalize_run=finalize_run,
    )
    snapshot = await jobs.start("camera.mp4")

    await wait_for_status(jobs, snapshot.analysis_id, AnalysisJobStatus.COMPLETED)
    for _ in range(100):
        if finalized:
            break
        await asyncio.sleep(0)
    assert finalized == [snapshot.analysis_id]


@pytest.mark.asyncio
async def test_fatal_task_is_interrupted_and_exception_is_retrieved(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FatalTaskError(BaseException):
        pass

    async def fatal_runner(
        _manager: FakeManager,
        _video: str,
        _run_id: str,
        **_kwargs: str,
    ) -> None:
        raise FatalTaskError("fatal runner exit")

    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=1,
        run_video=fatal_runner,
        defaults=defaults,
    )
    snapshot = await jobs.start("camera.mp4")

    await wait_for_status(jobs, snapshot.analysis_id, AnalysisJobStatus.INTERRUPTED)
    await asyncio.sleep(0)

    assert await jobs.active_count() == 0
    assert await jobs.status(snapshot.analysis_id) not in {
        AnalysisJobStatus.QUEUED,
        AnalysisJobStatus.RUNNING,
    }
    assert "canonical analysis job task failed" in caplog.text
    assert "Task exception was never retrieved" not in caplog.text


@pytest.mark.asyncio
async def test_ordinary_exception_keeps_failed_terminal_semantics(tmp_path: Path) -> None:
    async def failing_runner(
        _manager: FakeManager,
        _video: str,
        _run_id: str,
        **_kwargs: str,
    ) -> None:
        raise RuntimeError("ordinary runner failure")

    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=1,
        run_video=failing_runner,
        defaults=defaults,
    )
    snapshot = await jobs.start("camera.mp4")

    await wait_for_status(jobs, snapshot.analysis_id, AnalysisJobStatus.FAILED)
    assert await jobs.active_count() == 0


@pytest.mark.asyncio
async def test_existing_run_artifacts_are_never_overwritten(tmp_path: Path) -> None:
    existing_jsonl = tmp_path / "existing-jsonl.jsonl"
    existing_jsonl.write_bytes(b"sentinel-jsonl")
    existing_meta = tmp_path / "existing-meta.meta.json"
    existing_meta.write_bytes(b"sentinel-meta")
    candidates = iter(["existing-jsonl", "existing-meta", "fresh-id"])
    runner = BlockingRunner()
    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=1,
        run_video=runner,
        defaults=defaults,
        id_factory=candidates.__next__,
    )

    snapshot = await jobs.start("camera.mp4")
    assert snapshot.analysis_id == "fresh-id"
    assert existing_jsonl.read_bytes() == b"sentinel-jsonl"
    assert existing_meta.read_bytes() == b"sentinel-meta"
    await jobs.cancel_all()


@pytest.mark.asyncio
async def test_ws_start_and_stop_use_the_shared_job_service(monkeypatch, tmp_path: Path) -> None:
    from dortgoz import main

    runner = BlockingRunner()
    jobs = service(tmp_path, runner)
    monkeypatch.setattr(main.settings, "mock", False)
    monkeypatch.setattr(main.app.state, "analysis_jobs", jobs)
    message = OperatorMessage(kind="start_run", video="camera.mp4")

    await asyncio.gather(*(main.handle_operator_message(message) for _ in range(20)))
    await wait_for_calls(runner, 1)
    await main.stop_run()

    assert len(runner.calls) == 1
    assert runner.cancelled == [runner.calls[0][1]]


@pytest.mark.asyncio
async def test_ws_stop_still_cancels_every_feed(monkeypatch, tmp_path: Path) -> None:
    from dortgoz import main

    runner = BlockingRunner()
    jobs = service(tmp_path, runner)
    monkeypatch.setattr(main.settings, "mock", False)
    monkeypatch.setattr(main.app.state, "analysis_jobs", jobs)
    await main.handle_operator_message(
        OperatorMessage(kind="start_run", video="one.mp4", feed="KAM-1")
    )
    await main.handle_operator_message(
        OperatorMessage(kind="start_run", video="two.mp4", feed="KAM-2")
    )
    await wait_for_calls(runner, 2)

    await main.handle_operator_message(OperatorMessage(kind="stop_run"))

    assert set(runner.cancelled) == {run_id for _, run_id, _ in runner.calls}


@pytest.mark.asyncio
async def test_mock_ws_start_never_launches_runner(monkeypatch, tmp_path: Path) -> None:
    from dortgoz import main

    runner = BlockingRunner()
    jobs = CanonicalAnalysisJobService(
        FakeManager(),
        runs_dir=tmp_path,
        max_active=24,
        run_video=runner,
        defaults=defaults,
        enabled=lambda: not main.settings.mock,
    )
    monkeypatch.setattr(main.settings, "mock", True)
    monkeypatch.setattr(main.app.state, "analysis_jobs", jobs)

    await main.handle_operator_message(OperatorMessage(kind="start_run", video="must-not-run.mp4"))
    await asyncio.sleep(0)

    assert runner.calls == []


def test_iter_run_lines_skips_broken_lines_and_reports_count(tmp_path: Path) -> None:
    """Tek bozuk satır tüm koşuyu erişilemez yapmamalı; atlanan sayılmalı."""
    path = tmp_path / "kirik.jsonl"
    path.write_text(
        '{"seq": 0, "payload": {"type": "agent_step"}}\n'
        "\n"
        "[1, 2, 3]\n"
        '{"seq": 1, "payload": {"type": "agent_step"}}\n'
        '{"seq": 2, "payload": {"type"',
        encoding="utf-8",
    )

    stats: dict = {}
    lines = list(iter_run_lines(path, stats=stats))

    assert [item["seq"] for item in lines] == [0, 1]
    assert stats == {"atlanan": 2, "toplam": 4}
