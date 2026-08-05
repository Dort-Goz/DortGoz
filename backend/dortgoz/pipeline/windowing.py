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


def activity_windows(
    profile: list[MotionSample],
    duration: float,
    gate: float,
    *,
    min_len: float = 8.0,
    max_len: float = 45.0,
    preroll: float = 3.0,
    quiet_tail: float = 6.0,
) -> list[tuple[float, float]]:
    """Pencereleri SABİT IZGARAYA değil, ETKİNLİĞİN KENDİSİNE hizalar.

    Sabit 30 sn'lik ızgarada 25. saniyede başlayan bir olay ikiye bölünüyor:
    ilk pencerenin 25 saniyesi boş koridor, olayın açılışı sonraki pencereye
    kayıyor — hem kareler boşa gidiyor hem olayın başlangıcı bağlamsız kalıyor.
    Burada profil örnek örnek taranır: ölü bölge ATLANIR (VLM hiç çağrılmaz),
    pencere etkinliğin başladığı yerde açılır ve etkinlik `quiet_tail` kadar
    sürekli sustuğunda kapanır.

    - `preroll`  : olayın açılışı kırpılmasın diye başlangıçtan biraz öncesi
    - `quiet_tail`: bu kadar sessizlik pencereyi bitirir (kısa duraklamalar bölmez)
    - `min_len`  : tek saniyelik kıpırtı bile yeterli bağlamla okunsun
    - `max_len`  : uzun olay parça parça okunsun (kare yoğunluğu ve token sınırı)

    Profil çözünürlüğü `base_fps` kadardır (1 fps → ±1 sn hassasiyet).
    """
    if duration <= 0 or not profile:
        return []
    step = profile[1].t - profile[0].t if len(profile) > 1 else 1.0
    step = step if step > 0 else 1.0

    out: list[tuple[float, float]] = []
    start: float | None = None
    quiet_for = 0.0
    for s in profile:
        active = s.activity >= gate
        if start is None:
            if active:
                start = max(0.0, s.t - preroll)
                quiet_for = 0.0
            continue
        quiet_for = 0.0 if active else quiet_for + step
        too_long = s.t - start >= max_len
        if quiet_for >= quiet_tail or too_long:
            # Sessizlikle bitiyorsa kuyruğun tamamını pencereye katma
            end = s.t - (quiet_for - step) if quiet_for >= quiet_tail else s.t
            end = min(duration, max(end, start + min_len))
            out.append((start, end))
            start = None if quiet_for >= quiet_tail else max(0.0, s.t)
            quiet_for = 0.0
    if start is not None:
        out.append((start, min(duration, max(start + min_len, profile[-1].t + step))))
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
