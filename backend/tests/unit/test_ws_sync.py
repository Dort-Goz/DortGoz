"""WebSocket sıra geçmişi ve yeniden bağlanma eşleme testi."""

import asyncio
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


@pytest.mark.asyncio
async def test_backend_restart_resets_ahead_client_cursor() -> None:
    manager = ConnectionManager()
    socket = FakeSocket()
    await manager.connect(socket)  # type: ignore[arg-type]

    await manager.replay_since(socket, 500)  # type: ignore[arg-type]
    await manager.broadcast(Event.wrap(ChatMessage(role="agent", text="yeni süreç")))

    control = json.loads(socket.sent[0])
    assert control == {"kind": "sync_reset", "oldest_seq": 1, "latest_seq": 0}
    assert json.loads(socket.sent[1])["seq"] == 1


@pytest.mark.asyncio
async def test_dropped_slow_client_socket_is_closed() -> None:
    """Düşürülen istemcinin soketi de kapanmalı — yoksa arayüz yeniden bağlanmaz."""

    class StalledSocket(FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.close_code: int | None = None

        async def send_text(self, data: str) -> None:
            await asyncio.sleep(3600)

        async def close(self, code: int = 1000) -> None:
            self.close_code = code

    manager = ConnectionManager()
    manager.SEND_TIMEOUT = 0.05
    stalled = StalledSocket()
    healthy = FakeSocket()
    await manager.connect(stalled)  # type: ignore[arg-type]
    await manager.connect(healthy)  # type: ignore[arg-type]
    manager._syncing.clear()

    await manager.broadcast(Event.wrap(ChatMessage(role="agent", text="x")))

    assert healthy.sent, "sağlıklı istemci mesajı almalı"
    assert stalled not in manager._connections
    assert stalled.close_code == 1011


@pytest.mark.asyncio
async def test_close_failure_does_not_break_broadcast() -> None:
    """Kapatma hatası yutulur; yayın diğer istemciler için tamamlanır."""

    class UnclosableSocket(FakeSocket):
        async def send_text(self, data: str) -> None:
            raise ConnectionResetError("bağlantı düştü")

        async def close(self, code: int = 1000) -> None:
            raise RuntimeError("soket zaten kapalı")

    manager = ConnectionManager()
    broken = UnclosableSocket()
    healthy = FakeSocket()
    await manager.connect(broken)  # type: ignore[arg-type]
    await manager.connect(healthy)  # type: ignore[arg-type]
    manager._syncing.clear()

    await manager.broadcast(Event.wrap(ChatMessage(role="agent", text="x")))

    assert [json.loads(item)["seq"] for item in healthy.sent] == [1]
    assert broken not in manager._connections
