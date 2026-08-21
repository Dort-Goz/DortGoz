from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from fastapi import WebSocket

from .events import Event


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._seq = 0
        self.observers: list = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    SEND_TIMEOUT = 5.0

    async def broadcast(self, event: Event) -> None:
        self._seq += 1
        event.seq = self._seq
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
                await asyncio.wait_for(ws.send_text(data), timeout=self.SEND_TIMEOUT)
            except Exception:
                return False
            return True

        conns = list(self._connections)
        results = await asyncio.gather(*(send(ws) for ws in conns),
                                       return_exceptions=True)
        for ws, ok in zip(conns, results):
            if ok is not True:
                self.disconnect(ws)


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
        if transform is not None:
            event = transform(event)
        await manager.broadcast(event)
