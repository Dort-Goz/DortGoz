"""WebSocket bağlantı yöneticisi + mock yeniden oynatma."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import WebSocket

from .events import Event


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._seq = 0

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    async def broadcast(self, event: Event) -> None:
        self._seq += 1
        event.seq = self._seq
        data = event.model_dump_json()
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


async def replay_jsonl(manager: ConnectionManager, path: Path, speed: float = 1.0) -> None:
    """Kayıtlı olay akışını (JSONL) gerçekçi gecikmelerle yeniden oynat.

    Her satır bir Event; satırdaki `delay` alanı (sn) varsa ondan, yoksa 0.8 sn
    sabit aralıktan beklenir. Demo ve GPU'suz arayüz geliştirme için.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        raw = json.loads(line)
        delay = float(raw.pop("delay", 0.8)) / max(speed, 0.01)
        await asyncio.sleep(delay)
        await manager.broadcast(Event.model_validate(raw))
