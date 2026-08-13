"""Çalışma kipi (dengeli/hassas/genis) zinciri: bayrak çözümü + job kimliği."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_analysis_job_service import BlockingRunner, service, wait_for_calls

from dortgoz.config import settings
from dortgoz.events import OperatorMessage
from dortgoz.pipeline.runner import _mode_flags
from dortgoz.services.analysis_job import AnalysisJobConflict


def test_mode_flags_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dual_read", False)
    monkeypatch.setattr(settings, "final_sweep", False)
    assert _mode_flags("") == (False, False, False)
    assert _mode_flags("dengeli") == (False, False, False)
    assert _mode_flags("genis") == (True, False, True)
    assert _mode_flags("hassas") == (False, True, False)
    # kip verilmediğinde ayar bayrakları geçerli kalır
    monkeypatch.setattr(settings, "dual_read", True)
    monkeypatch.setattr(settings, "final_sweep", True)
    assert _mode_flags("") == (True, False, True)


def test_operator_message_mode_validated() -> None:
    assert OperatorMessage(kind="start_run", video="v.mp4", mode="genis").mode == "genis"
    with pytest.raises(ValueError):
        OperatorMessage(kind="start_run", video="v.mp4", mode="turbo")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mode_joins_job_identity(tmp_path: Path) -> None:
    runner = BlockingRunner()
    jobs = service(tmp_path, runner)
    first = await jobs.start("camera.mp4", feed="KAM-1", mode="genis")

    same = await jobs.start("camera.mp4", feed="KAM-1", mode="genis")
    assert same.analysis_id == first.analysis_id

    with pytest.raises(AnalysisJobConflict):
        await jobs.start("camera.mp4", feed="KAM-1", mode="hassas")

    await wait_for_calls(runner, 1)
    assert runner.calls[0][2].get("mode") == "genis"
    await jobs.cancel_all()
