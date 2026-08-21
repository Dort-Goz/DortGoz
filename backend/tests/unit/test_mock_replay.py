"""UI replay arka plan görevinin hata gözlemi."""

import asyncio
import logging

import pytest

from dortgoz import main


@pytest.mark.asyncio
async def test_failed_ui_replay_is_logged_and_can_retry(caplog) -> None:
    async def fail() -> None:
        raise RuntimeError("bozuk replay")

    task = asyncio.create_task(fail())
    main._ui_replay_task = task
    task.add_done_callback(main._observe_ui_replay)

    with caplog.at_level(logging.ERROR, logger=main.__name__):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert main._ui_replay_task is None
    assert "UI replay akışı başarısız oldu" in caplog.text
