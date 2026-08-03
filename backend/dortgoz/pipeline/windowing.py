"""[2] AKILLI KARE SEÇİMİ — puan-kapılı örnekleme + 30 sn pencereler.

Tasarım dayanağı (Holmes-VAD): tekdüze örnekleme yerine puan-kapılı seçim
= +23 AP ve 7,7 kat hız. Kare seçimi hattın en belirleyici halkasıdır.

Hafta 1 sürümü yalnız **hareket** puanıyla kapılar; hafta 2'de aynı arayüze
dedektör/iz puanları eklenir (`select_keyframes` imzası değişmeden kalır).

Ölçüm notu (2026-08-03, canlı): 320×240 kare ≈ 80 prompt token, kodlama
~0,24 sn/kare. Yani k'yı büyütmenin maliyeti düşük; darboğaz üretilen token.

TODO(hafta 2): dedektör/iz puanlarını `scores` içine karıştır; phash tekilleştirme
TODO(hafta 3): burst(window, t, fps=5..10) — tırmandırma döngüsü için yoğun örnekleme
"""

from __future__ import annotations

from .ingest import MotionSample

WINDOW_SECONDS = 30.0


def windows(duration: float, length: float = WINDOW_SECONDS) -> list[tuple[float, float]]:
    """Videoyu kesişmesiz pencerelere böler; son pencere kısa kalabilir.

    A5 açık: örtüşme mi, pencere+defter birleştirmesi mi — şimdilik kesişmesiz.
    """
    if duration <= 0:
        return []
    out: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        out.append((start, min(start + length, duration)))
        start += length
    return out


def window_motion(profile: list[MotionSample], start: float, end: float) -> float:
    """Pencerenin tepe etkinlik puanı — eleme kararı buna bakar."""
    scores = [s.activity for s in profile if start <= s.t < end]
    return max(scores) if scores else 0.0


def select_keyframes(
    profile: list[MotionSample],
    start: float,
    end: float,
    k: int = 6,
    min_gap: float | None = None,
) -> list[float]:
    """Pencereden en bilgilendirici `k` kare zamanını seçer.

    Puanı yüksek anlar önce alınır, ancak seçilenler arasında `min_gap` saniyelik
    asgari mesafe aranır — tek bir hareketli saniyeye yığılmayı önler. Pencere
    tamamen sakinse tekdüze örneklemeye düşer (bilgi yok, hepsi eşit).
    """
    samples = [s for s in profile if start <= s.t < end]
    if not samples:
        return _uniform(start, end, k)
    if min_gap is None:
        min_gap = (end - start) / (k * 1.5) if k else 0.0

    picked: list[float] = []
    for sample in sorted(samples, key=lambda s: (-s.activity, s.t)):
        if sample.activity <= 0.0:
            break                      # sıfır puanlılar tekdüze dolguya kalsın
        if all(abs(sample.t - p) >= min_gap for p in picked):
            picked.append(sample.t)
        if len(picked) == k:
            break

    if len(picked) < k:                # sakin pencere / az örnek → tekdüze ile tamamla
        for t in _uniform(start, end, k):
            if len(picked) == k:
                break
            if all(abs(t - p) >= min_gap for p in picked):
                picked.append(t)
    return sorted(picked)


def _uniform(start: float, end: float, k: int) -> list[float]:
    if k <= 0 or end <= start:
        return []
    step = (end - start) / k
    return [start + step * (i + 0.5) for i in range(k)]
