"""WebSocket sıra geçmişi ve yeniden bağlanma eşleme testi."""

import json
from collections import deque

import pytest

from dortgoz.events import ChatMessage, Event
from dortgoz.ws import ConnectionManager


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_reconnect_replays_only_missing_events_in_sequence() -> None:
    manager = ConnectionManager()
    first = FakeSocket()
    await manager.connect(first)  # type: ignore[arg-type]
    await manager.broadcast(Event.wrap(ChatMessage(role="agent", text="bir")))
    await manager.broadcast(Event.wrap(ChatMessage(role="agent", text="iki")))
    assert first.sent == []  # sync tamamlanmadan canlı delta gönderilmez

    await manager.replay_since(first, 0)  # type: ignore[arg-type]
    assert [json.loads(item)["seq"] for item in first.sent] == [1, 2]
    manager.disconnect(first)  # type: ignore[arg-type]

    second = FakeSocket()
    await manager.connect(second)  # type: ignore[arg-type]
    await manager.broadcast(Event.wrap(ChatMessage(role="agent", text="üç")))
    await manager.replay_since(second, 1)  # type: ignore[arg-type]

    assert [json.loads(item)["seq"] for item in second.sent] == [2, 3]


@pytest.mark.asyncio
async def test_history_gap_requests_client_state_reset() -> None:
    manager = ConnectionManager()
    manager._history = deque(maxlen=2)
    socket = FakeSocket()
    await manager.connect(socket)  # type: ignore[arg-type]
    for number in range(4):
        await manager.broadcast(Event.wrap(ChatMessage(role="agent", text=str(number))))

    await manager.replay_since(socket, 1)  # type: ignore[arg-type]

    control = json.loads(socket.sent[0])
    assert control == {"kind": "sync_reset", "oldest_seq": 3, "latest_seq": 4}
    assert [json.loads(item)["seq"] for item in socket.sent[1:]] == [3, 4]
