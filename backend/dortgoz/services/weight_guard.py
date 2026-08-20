from __future__ import annotations

import logging
import os
import re
from collections import deque

import httpx

from ..config import settings

log = logging.getLogger(__name__)

CJK_RE = re.compile(r"[぀-ヿ一-鿿]")
WINDOW = 20
THRESHOLD = 2


class WeightGuard:
    def __init__(self) -> None:
        self._recent: deque[bool] = deque(maxlen=WINDOW)
        self.total_hits = 0
        self.heals = 0
        self._alerts: list[str] = []

    def record(self, text: str) -> bool:
        hit = bool(CJK_RE.search(text))
        self._recent.append(hit)
        if hit:
            self.total_hits += 1
            sample = CJK_RE.search(text).group()
            self._alerts.append(
                f"Ağırlık bozulması belirtisi: çıktıda CJK sızıntısı ('{sample}') — "
                f"toplam {self.total_hits}. isabet. Kuyruk boşalınca sayfa önbelleği "
                f"tazelenecek; süregelen raporlara ihtiyatla yaklaşın."
            )
        return hit

    @property
    def needs_heal(self) -> bool:
        return sum(self._recent) >= THRESHOLD

    def drain_alerts(self) -> list[str]:
        alerts, self._alerts = self._alerts, []
        return alerts

    async def heal(self) -> None:
        base = settings.llama_base_url.rsplit("/v1", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.get(f"{base}/unload")
        except Exception as exc:
            log.warning("weight_guard: unload başarısız: %s", exc)
        paths = [p for p in settings.gguf_paths.split(":") if p]
        if not paths:
            log.warning("weight_guard: DORTGOZ_GGUF_PATHS boş — sayfa düşürme "
                        "atlandı, yeniden yükleme bozuk sayfaları bulabilir")
        for p in paths:
            try:
                fd = os.open(p, os.O_RDONLY)
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                finally:
                    os.close(fd)
            except OSError as exc:
                log.warning("weight_guard: %s düşürülemedi: %s", p, exc)
        self._recent.clear()
        self.heals += 1
        log.info("weight_guard: sayfa önbelleği tazelendi (%d. iyileşme)", self.heals)


guard = WeightGuard()
