"""[0] ALIM — ffmpeg kod çözme, temel örnekleme, hareket ön filtresi.

Profil tek bir ffmpeg geçişinde çıkarılır: video 64×48 gri ham kareye indirgenir.
Kare başına 3 KB olduğu için saf Python yeterli — bu aşamada numpy/opencv yok.

## Neden iki sinyal (2026-08-03 ölçümü)

İlk sürüm yalnız **ortalama mutlak fark** kullanıyordu; ölçüldüğünde eleme gücü
yoktu. Sentetik ölü görüntüyle kıyas (donmuş kare 30 sn):

| Ölçüt | gürültülü ölü görüntü | gerçek görüntü (min) | pay |
|---|---|---|---|
| ortalama mutlak fark | 0,0042 | 0,0061 | 1,5× |
| **değişen piksel oranı** | **0,0000** | 0,0075 | **3,3×** |
| ön plan oranı | 0,0033 | 0,0075 | 2,3× |

Ortalama mutlak fark sensör gürültüsünü tüm piksellere yayarak topluyor; eşik
gürültü tabanına sıkışıyor. **Piksel başına eşikten geçenleri saymak** gürültüyü
tamamen reddediyor (gürültü genliği < `PIXEL_TAU`).

`fg` (koşan arka plan modeline göre ön plan) ikinci ve farklı bir soruyu sorar:
"sahnede bir şey VAR mı?" — kare farkı yalnız "bir şey DEĞİŞTİ mi?" der. Fark
kritik: **yerde hareketsiz yatan kişi** hedef olay türlerimizden biri ve kare
farkı için görünmezdir. `BG_ALPHA` duran nesnenin ne kadar süre ön planda
kalacağını belirler (~1/α kare ≈ 50 sn @ 1 fps).

⚠ Hiçbir hareket ölçütü "duran kişi" ile "boş oda"yı ayıramaz — bu ayrım
dedektörün işidir (hafta 2). Bu katman kareyi eler, pencereyi değil.

TODO(hafta 2): pencere kabul kararını dedektöre devret; hareket yalnız
              dedektörün hangi karelerde koşacağını seçsin
TODO(hafta 2): sahne bölütleme (PySceneDetect-eşdeğeri basit eşik)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

GRAY_W, GRAY_H = 64, 48
_FRAME_BYTES = GRAY_W * GRAY_H

PIXEL_TAU = 18          # piksel değişim eşiği (0-255); sensör gürültüsünün üstü
BG_ALPHA = 0.02         # arka plan öğrenme hızı; düşük = duran nesne uzun süre görünür


@dataclass(frozen=True)
class MotionSample:
    """Tek örnekleme anının ucuz sinyalleri."""

    t: float
    changed: float      # ana sinyal — PIXEL_TAU'yu aşan piksellerin oranı
    fg: float           # varlık sinyali — arka plan modeline göre ön plan oranı
    mad: float          # eski ölçüt; telemetri ve karşılaştırma için tutuluyor

    @property
    def activity(self) -> float:
        """Kapı ve kare seçimi bunu kullanır: hareket VEYA varlık."""
        return max(self.changed, self.fg)


class FFmpegError(RuntimeError):
    """ffmpeg/ffprobe sıfırdan farklı döndü — çağıran tarafta olay akışına yazılır."""


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(err.decode("utf-8", "replace")[-400:])
    return out


async def probe_duration(video: Path) -> float:
    """Video süresi (sn)."""
    out = await _run(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video),
    )
    return float(out.decode().strip())


async def motion_profile(video: Path, base_fps: float = 1.0) -> list[MotionSample]:
    """Videoyu `base_fps` ile tarayıp örnek başına ucuz sinyalleri döndürür."""
    raw = await _run(
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"fps={base_fps},scale={GRAY_W}:{GRAY_H}",
        "-pix_fmt", "gray", "-f", "rawvideo", "-",
    )
    profile: list[MotionSample] = []
    prev: bytes | None = None
    background: list[float] | None = None
    for idx in range(0, len(raw) - _FRAME_BYTES + 1, _FRAME_BYTES):
        frame = raw[idx:idx + _FRAME_BYTES]
        t = (idx // _FRAME_BYTES) / base_fps

        if prev is None:
            mad = changed = 0.0            # ilk karenin öncesi yok
            background = [float(p) for p in frame]
        else:
            total = 0
            hits = 0
            for a, b in zip(frame, prev):
                d = a - b if a > b else b - a
                total += d
                if d > PIXEL_TAU:
                    hits += 1
            mad = total / (_FRAME_BYTES * 255)
            changed = hits / _FRAME_BYTES

        assert background is not None
        fg = sum(1 for a, b in zip(frame, background)
                 if abs(a - b) > PIXEL_TAU) / _FRAME_BYTES
        background = [b + BG_ALPHA * (a - b) for a, b in zip(frame, background)]

        profile.append(MotionSample(t=t, changed=changed, fg=fg, mad=mad))
        prev = frame
    return profile


def noise_floor(profile: list[MotionSample], percentile: float = 0.05) -> float:
    """Kaydın en sakin dilimindeki etkinlik — kameranın taban gürültüsü tahmini.

    Yalnız telemetri ve eşiğin YUKARI kalibrasyonu için; kendi başına eşik
    değildir (aşağıdaki uyarıya bak).
    """
    vals = sorted(s.activity for s in profile)
    if not vals:
        return 0.0
    return vals[min(int(len(vals) * percentile), len(vals) - 1)]


def adaptive_gate(profile: list[MotionSample], k: float = 4.0,
                  minimum: float = 0.004, ceiling: float = 0.010) -> float:
    """Ölü görüntüyü eleyen eşik — **tavanla sınırlı**.

    ⚠ Eşik sahnenin yoğunluğuyla ÖLÇEKLENMEZ. İlk sürüm eşiği yüzdelikle
    çarpıyordu; baştan sona hareketli kliplerde "taban" aslında sinyal olduğu
    için eşik 1,0'ın (mümkün olan maksimumun) üstüne çıkıp 12 gerçek olaylı
    pencereyi eledi (2026-08-03 ölçümü). Bu kapının işi yalnız ölü kaydı
    elemek; ölçüt zaten gürültüye dayanıklı olduğu için eşiğin küçük ve
    sınırlı kalması gerekir.

    Ölçülen güvenli bant: gürültülü ölü görüntü 0,0000 · saat damgalı ölü
    görüntü 0,0033 · en sakin gerçek pencere 0,0075 · en düşük gerçek olaylı
    pencere 0,027.
    """
    return min(max(noise_floor(profile) * k, minimum), ceiling)


async def grab_frame(video: Path, t: float, width: int = 512) -> bytes:
    """`t` anındaki tek kareyi JPEG olarak döndürür (VLM istemine gömülür)."""
    return await _run(
        "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"scale={width}:-2",
        "-f", "image2", "-c:v", "mjpeg", "-",
    )
