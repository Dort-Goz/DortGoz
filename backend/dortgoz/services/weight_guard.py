"""Ağırlık sayfa-önbelleği bozulması nöbetçisi.

Saatlerce yük altında mmap'li GGUF sayfaları ana bellekte bozulabiliyor
(2026-08-13 ölçümü: Türkçe çıktıya CJK karakter sızıntısı; model yeniden
yüklemesi İYİLEŞTİRMİYOR çünkü sayfalar önbellekten geliyor). Tek çare:
sayfaları düşürüp diskten yeniden okutmak (`posix_fadvise DONTNEED`).
Benchmark koşucusundaki koruma burada üretim yoluna alınır:

- `record(text)`: her ham VLM çıktısı taranır; CJK isabeti sayılır ve
  operatör uyarısı kuyruklanır (koşucu `drain_alerts` ile yayınlar).
- `needs_heal`: son WINDOW çıktıda THRESHOLD+ isabet — tek tük örnekleme
  gürültüsü değil, sistematik sızıntı işareti.
- `heal()`: model sunucusu `/unload` + GGUF sayfalarını düşürme. Koşu ORTASINDA
  çağrılmaz; iş servisi kuyruk boşaldığında tetikler (canlı kipte segment
  arası). GGUF yolları `DORTGOZ_GGUF_PATHS` (":" ayraçlı) ile bildirilir;
  boşsa yalnız unload yapılır (yeni yükleme büyük olasılıkla aynı bozuk
  sayfaları bulur — uyarı günlüklenir).
"""
from __future__ import annotations

import logging
import os
import re
from collections import deque

import httpx

from ..config import settings

log = logging.getLogger(__name__)

# Türkçe raporda hiçbir CJK karakterinin işi yok — tek isabet bile anomali,
# eşik yalnız tek-token örnekleme gürültüsüne karşı tampon.
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
        """Ham model çıktısını tarar; CJK isabetinde uyarı kuyruklar."""
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
        """Modeli indir + GGUF sayfalarını düşür. Yalnız kuyruk boşken çağrılır."""
        base = settings.llama_base_url.rsplit("/v1", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.get(f"{base}/unload")
        except Exception as exc:  # sunucu kapalıysa evict yine değerli
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


guard = WeightGuard()   # süreç-küresel tekil — tüm koşular aynı modeli paylaşır
