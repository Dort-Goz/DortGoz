from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Callable
from pathlib import Path

from fastapi import WebSocket

from .events import Event

LOGGER = logging.getLogger(__name__)


class ConnectionManager:
    HISTORY_LIMIT = 10_000

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._syncing: set[WebSocket] = set()
        self._send_locks: dict[WebSocket, asyncio.Lock] = {}
        self._history: deque[Event] = deque(maxlen=self.HISTORY_LIMIT)
        self._seq = 0
        self.observers: list = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        self._syncing.add(ws)
        self._send_locks[ws] = asyncio.Lock()

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        self._syncing.discard(ws)
        self._send_locks.pop(ws, None)

    async def replay_since(self, ws: WebSocket, from_seq: int) -> None:
        """Eksik olayları sırayla gönder ve ardından canlı yayını aç."""

        if ws not in self._connections:
            return
        lock = self._send_locks.setdefault(ws, asyncio.Lock())
        cursor = max(0, from_seq)
        reset_sent = False
        async with lock:
            while ws in self._connections:
                history = list(self._history)
                if cursor > self._seq and not reset_sent:
                    oldest_seq = history[0].seq if history else 1
                    await ws.send_text(
                        json.dumps(
                            {
                                "kind": "sync_reset",
                                "oldest_seq": oldest_seq,
                                "latest_seq": self._seq,
                            }
                        )
                    )
                    cursor = oldest_seq - 1
                    reset_sent = True
                if history and cursor and cursor < history[0].seq - 1 and not reset_sent:
                    await ws.send_text(
                        json.dumps(
                            {
                                "kind": "sync_reset",
                                "oldest_seq": history[0].seq,
                                "latest_seq": self._seq,
                            }
                        )
                    )
                    cursor = history[0].seq - 1
                    reset_sent = True
                for event in (item for item in history if item.seq > cursor):
                    await asyncio.wait_for(
                        ws.send_text(event.model_dump_json()),
                        timeout=self.SEND_TIMEOUT,
                    )
                    cursor = event.seq
                if cursor >= self._seq:
                    self._syncing.discard(ws)
                    return

    SEND_TIMEOUT = 5.0

    async def broadcast(self, event: Event) -> None:
        self._seq += 1
        event.seq = self._seq
        self._history.append(event.model_copy(deep=True))
        for observer in self.observers:
            try:
                observer(event)
            except Exception:
                pass
        if not self._connections:
            return
        data = event.model_dump_json()

        async def send(ws: WebSocket) -> bool:
            try:
                lock = self._send_locks.setdefault(ws, asyncio.Lock())
                async with lock:
                    await asyncio.wait_for(ws.send_text(data), timeout=self.SEND_TIMEOUT)
            except Exception:
                return False
            return True

        conns = [ws for ws in self._connections if ws not in self._syncing]
        results = await asyncio.gather(*(send(ws) for ws in conns),
                                       return_exceptions=True)
        dropped = [ws for ws, ok in zip(conns, results) if ok is not True]
        for ws in dropped:
            self.disconnect(ws)
        if dropped:
            await asyncio.gather(*(self._close_dropped(ws) for ws in dropped))

    async def _close_dropped(self, ws: WebSocket) -> None:
        try:
            await asyncio.wait_for(ws.close(code=1011), timeout=self.SEND_TIMEOUT)
        except Exception:
            LOGGER.debug("düşürülen istemci soketi kapatılamadı", exc_info=True)


async def replay_jsonl(
    manager: ConnectionManager,
    path: Path,
    speed: float = 1.0,
    *,
    transform: Callable[[Event], Event] | None = None,
) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        raw = json.loads(line)
        payload = raw.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "run_metrics":
            continue
        delay = float(raw.pop("delay", 0.8)) / max(speed, 0.01)
        await asyncio.sleep(delay)
        event = Event.model_validate(raw)
        await manager.broadcast(transform(event) if transform is not None else event)
