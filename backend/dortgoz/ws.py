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

    # Yavaş/ölü bir istemci koşuyu DURDURMAMALI: gönderimler sırayla ve zaman
    # aşımsız yapılırken, TCP tamponu dolmuş TEK bir istemci (uyuyan dizüstü,
    # temiz kapanmayan sekme) `emit`i süresiz askıda bırakıp tüm analizi
    # kilitleyebiliyordu — 2026-08-05'te 38 sızıntı bağlantıyla canlı görüldü.
    SEND_TIMEOUT = 5.0

    async def broadcast(self, event: Event) -> None:
        self._seq += 1
        event.seq = self._seq
        if not self._connections:
            return
        data = event.model_dump_json()

        async def send(ws: WebSocket) -> bool:
            """True = gönderildi. Zaman aşımı/hata → False (istemci düşürülür)."""
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=self.SEND_TIMEOUT)
            except Exception:
                return False
            return True

        # Eşzamanlı gönderim: bir istemcinin gecikmesi diğerlerini bekletmesin.
        # Sonuçlar bağlantı listesiyle EŞLEŞTİRİLİR (tip kontrolüne güvenilmez).
        conns = list(self._connections)
        results = await asyncio.gather(*(send(ws) for ws in conns),
                                       return_exceptions=True)
        for ws, ok in zip(conns, results):
            if ok is not True:
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
        payload = raw.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "run_metrics":
            # Koşu özeti JSONL artifact'inde kalır; frontend Event union'ına
            # ve dolayısıyla demo/replay WS sözleşmesine girmez.
            continue
        delay = float(raw.pop("delay", 0.8)) / max(speed, 0.01)
        await asyncio.sleep(delay)
        await manager.broadcast(Event.model_validate(raw))
