from __future__ import annotations

import asyncio
import contextvars
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

GRAY_W, GRAY_H = 64, 48
_FRAME_BYTES = GRAY_W * GRAY_H

PIXEL_TAU = 18
BG_ALPHA = 0.02


@dataclass(frozen=True)
class MotionSample:

    t: float
    changed: float
    fg: float
    mad: float
    grid: bytes = b""

    @property
    def activity(self) -> float:
        return max(self.changed, self.fg)


class FFmpegError(RuntimeError):
    pass


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(err.decode("utf-8", "replace")[-400:])
    return out


async def probe_duration(video: Path) -> float:
    out = await _run(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video),
    )
    return float(out.decode().strip())

_hwaccel: list[str] | None = None


async def hwaccel_args() -> list[str]:
    """VAAPI çözümleme argümanları; kullanılamıyorsa boş liste.

    Bir kez arar ve önbelleğe alır. `disable_hwaccel()` çalışma anındaki bir
    başarısızlıktan sonra kalıcı olarak yazılıma düşürür.
    """
    global _hwaccel
    if _hwaccel is not None:
        return _hwaccel
    mode = settings.hwaccel.strip().casefold()
    # Varsayılan kapalı. "auto" bilerek desteklenmez: bozuk bir VAAPI yığınında
    # arıza "hata" değil "asılı kalma" olduğu için otomatik seçim güvenli değildir.
    if mode != "vaapi":
        _hwaccel = []
        return _hwaccel
    device = settings.hwaccel_device.strip()
    if not device or not Path(device).exists():
        _hwaccel = []
        return _hwaccel
    try:
        listed = await _run("ffmpeg", "-v", "quiet", "-hwaccels")
    except (FFmpegError, FileNotFoundError):
        _hwaccel = []
        return _hwaccel
    if b"vaapi" not in listed:
        _hwaccel = []
        return _hwaccel
    _hwaccel = ["-hwaccel", "vaapi", "-hwaccel_device", device]
    return _hwaccel


def disable_hwaccel() -> None:
    global _hwaccel
    _hwaccel = []


async def _run_decode(head: list[str], tail: list[str]) -> bytes:
    """Çözümlemeyi donanımda dener, başarısız olursa kalıcı olarak yazılıma düşer.

    Başlığı (`-v error` gibi) ve gerisini (`-i ...`) ayrı alır çünkü `-hwaccel`
    girdiden ÖNCE gelmelidir. Özel bir süzgeç gerekmez: çıkış biçimi
    verilmediğinde ffmpeg kareleri sistem belleğine indirir ve mevcut yazılım
    süzgeçleri aynen çalışır.
    """
    hw = await hwaccel_args()
    if hw:
        try:
            return await asyncio.wait_for(
                _run(*head, *hw, *tail),
                timeout=settings.hwaccel_timeout_seconds)
        except (FFmpegError, TimeoutError) as exc:
            disable_hwaccel()
            log.warning("donanım çözümleme kapatıldı, yazılıma düşüldü: %s",
                        f"{type(exc).__name__}: {str(exc)[:140]}")
    return await _run(*head, *tail)


def scale_filter(width: int) -> str:
    """Kaynaktan BÜYÜTME yapmayan tek ölçekleme süzgeci.

    Eski süzgeç her klibi `width` piksele zorluyordu. 320x240 bir kaynak 540
    piksele büyütülüyordu: yeni bilgi gelmez, yalnız bant genişliği ve çözümleme
    süresi artar. `min(width,iw)` klibi kendi çözünürlüğünde bırakır.
    `force_original_aspect_ratio` burada etkisizdi; yükseklik zaten `-2` ile
    en-boy oranından gelir. `lanczos` küçültmede uzak ve küçük nesneleri
    varsayılan bikübikten daha iyi korur; her çağrı aynı süzgeci kullanır.
    Tırnaklar zorunludur: min() içindeki virgül aksi halde süzgeç ayracı sanılır.
    """
    return f"scale='min({width},iw)':-2:flags=lanczos"


# H.264 her yerde çözülür ve ölçümde mpeg4'ü her eksende yener. Kodlayıcı yoksa
# eski mpeg4 yolu durur; klip üretimi hiçbir kurulumda kesilmez.
# Ölçüm (bench/klip_kodek.py, 2026-08-27, 720p canlı segment): mpeg4 -q:v 5
# 0.70 sn / 2075 KB / SSIM 0.9247; libx264 veryfast crf23 0.40 sn / 1222 KB /
# SSIM 0.9499. Daha hızlı, daha küçük ve daha ayrıntılıdır.
_KODEK = {
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p"],
    # libopenh264 `-preset`/`-crf` desteklemez; bit hızı ile sürülür.
    "libopenh264": ["-c:v", "libopenh264", "-b:v", "1200k", "-pix_fmt", "yuv420p"],
    "mpeg4": ["-c:v", "mpeg4", "-q:v", "5"],
}


async def clip_codec() -> list[str]:
    from ..tools.context_clip import browser_video_encoder

    return _KODEK[await browser_video_encoder()]


async def grab_clip(video: Path, start: float, end: float, width: int = 720) -> bytes:
    if start < 0 or end <= start:
        raise ValueError("video aralığı geçersiz")
    return await _run_decode(
        ["ffmpeg", "-nostdin", "-v", "error"],
        ["-ss", f"{start:.3f}", "-to", f"{end:.3f}",
         "-i", str(video), "-map", "0:v:0", "-an",
         "-vf", scale_filter(width),
         *await clip_codec(), "-f", "mp4",
         "-movflags", "frag_keyframe+empty_moov", "-"],
    )


async def motion_profile(video: Path, base_fps: float = 1.0) -> list[MotionSample]:
    raw = await _run_decode(
        ["ffmpeg", "-v", "error"],
        ["-i", str(video),
         "-vf", f"fps={base_fps},scale={GRAY_W}:{GRAY_H}",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
    )
    return _motion_samples(raw, base_fps)


def _motion_samples(raw: bytes, base_fps: float) -> list[MotionSample]:
    """Piksel matematiği. Saf Python — bilerek.

    ⚠ numpy ile vektörleştirmeyi denemeyin: ölçüldü ve İKİ KAT YAVAŞ çıktı
    (2026-08-27, arcpcl, 33 kare: saf Python 14.7 ms, numpy 31.5 ms). Kare
    64x48 = 3072 pikseldir; bu boyutta numpy'ın çağrı başına masrafı kazancı
    yer. Zaten darboğaz burada değildir: aynı klipte ffmpeg çözümlemesi 480 ms,
    bu döngü 15 ms'dir (%3).
    """
    profile: list[MotionSample] = []
    prev: bytes | None = None
    background: list[float] | None = None
    for idx in range(0, len(raw) - _FRAME_BYTES + 1, _FRAME_BYTES):
        frame = raw[idx:idx + _FRAME_BYTES]
        t = (idx // _FRAME_BYTES) / base_fps

        if prev is None:
            mad = changed = 0.0
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

        profile.append(MotionSample(t=t, changed=changed, fg=fg, mad=mad,
                                    grid=frame))
        prev = frame
    return profile


def noise_floor(profile: list[MotionSample], percentile: float = 0.05) -> float:
    vals = sorted(s.activity for s in profile)
    if not vals:
        return 0.0
    return vals[min(int(len(vals) * percentile), len(vals) - 1)]


def adaptive_gate(profile: list[MotionSample], k: float = 4.0,
                  minimum: float = 0.004, ceiling: float = 0.010) -> float:
    return min(max(noise_floor(profile) * k, minimum), ceiling)


async def _grab_frame_ffmpeg(video: Path, t: float, width: int) -> bytes:
    last_err: FFmpegError | None = None
    for attempt_t in (t, max(0.0, t - 1.0), max(0.0, t - 2.5)):
        try:
            out = await _run(
                "ffmpeg", "-v", "error", "-ss", f"{attempt_t:.3f}", "-i", str(video),
                "-frames:v", "1", "-vf", scale_filter(width),
                "-f", "image2", "-c:v", "mjpeg", "-",
            )
            if out:
                return out
        except FFmpegError as exc:
            last_err = exc
    raise last_err or FFmpegError(f"kare alınamadı: t={t:.3f} {video.name}")


JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


def _split_jpegs(blob: bytes) -> list[bytes]:
    frames: list[bytes] = []
    start = blob.find(JPEG_SOI)
    while start != -1:
        end = blob.find(JPEG_EOI, start + 2)
        if end == -1:
            break
        frames.append(blob[start:end + 2])
        start = blob.find(JPEG_SOI, end + 2)
    return frames


async def grab_frames(video: Path, timestamps: list[float],
                      width: int = 512) -> dict[float, bytes]:
    """İstenen kareleri TEK ffmpeg süreciyle çıkarır.

    Kare başına ayrı süreç açmak pahalıdır: ölçüm (2026-08-27, 15 sn 720p klip)
    dört kare için ayrı süreçlerle 0.734 sn CPU, tek süreçle 0.025 sn verdi.

    `select` istenen anın etrafındaki pencereden birden çok kare döndürebilir.
    Bu yüzden sayı tutmazsa sonuç kullanılmaz; çağıran eski kare-başına yola
    düşer. Hız için doğruluktan ödün verilmez.
    """
    wanted = sorted({round(float(t), 3) for t in timestamps if t >= 0})
    if not wanted:
        return {}
    eps = 0.04
    expr = "+".join(
        f"between(t\\,{max(0.0, t - eps):.3f}\\,{t + eps:.3f})" for t in wanted)
    try:
        blob = await _run_decode(
            ["ffmpeg", "-nostdin", "-v", "error"],
            ["-i", str(video),
             "-vf", f"select='{expr}',{scale_filter(width)}",
             "-fps_mode", "passthrough", "-f", "image2pipe", "-c:v", "mjpeg", "-"],
        )
    except FFmpegError:
        return {}
    frames = _split_jpegs(blob)
    if len(frames) != len(wanted):
        return {}
    return dict(zip(wanted, frames))


async def grab_many(video: Path, timestamps: list[float],
                    width: int = 512) -> list[bytes]:
    """İstenen sırada kare listesi: önce tek süreç, tutmazsa kare-başına.

    Çağıranın sırasını ve yinelenen zaman damgalarını korur.
    """
    if not timestamps:
        return []
    batch = await grab_frames(video, timestamps, width)
    keys = [round(float(t), 3) for t in timestamps]
    if batch and all(k in batch for k in keys):
        return [batch[k] for k in keys]
    return list(await asyncio.gather(
        *(grab_frame(video, t, width) for t in timestamps)))


_frame_tasks: dict[tuple[str, float, int], asyncio.Task] = {}
_FRAME_TASKS_MAX = 128


def _frame_task(video: Path, t: float, width: int) -> asyncio.Task:
    key = (str(video), round(t, 3), width)
    task = _frame_tasks.pop(key, None)
    loop = asyncio.get_running_loop()
    stale = task is not None and (
        (task.done() and (task.cancelled() or task.exception() is not None))
        or (not task.done() and task.get_loop() is not loop)
    )
    if task is None or stale:
        task = loop.create_task(_grab_frame_ffmpeg(video, t, width))
        task.add_done_callback(
            lambda tk: tk.exception() if not tk.cancelled() else None)
    _frame_tasks[key] = task
    while len(_frame_tasks) > _FRAME_TASKS_MAX:
        _frame_tasks.pop(next(iter(_frame_tasks)))
    return task


def prefetch_frames(video: Path, ts: list[float], width: int = 512) -> None:
    for t in ts:
        _frame_task(video, t, width)

_clip_tasks: dict[tuple[str, float, float, int, str], asyncio.Task] = {}
_CLIP_TASKS_MAX = 4

_clip_owner_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "dortgoz_clip_owner", default=""
)


def set_clip_owner(owner: str) -> None:
    _clip_owner_var.set(owner)


def _clip_owner() -> str:
    return _clip_owner_var.get()


def clip_task(video: Path, start: float, end: float, width: int) -> asyncio.Task:
    key = (str(video), round(start, 3), round(end, 3), width, _clip_owner())
    task = _clip_tasks.pop(key, None)
    loop = asyncio.get_running_loop()
    stale = task is not None and (
        (task.done() and (task.cancelled() or task.exception() is not None))
        or (not task.done() and task.get_loop() is not loop)
    )
    if task is None or stale:
        task = loop.create_task(grab_clip(video, start, end, width))
        task.add_done_callback(
            lambda tk: tk.exception() if not tk.cancelled() else None)
    _clip_tasks[key] = task
    while len(_clip_tasks) > _CLIP_TASKS_MAX:
        _clip_tasks.pop(next(iter(_clip_tasks)))
    return task

def prefetch_clip(video: Path, start: float, end: float, width: int) -> None:
    if end > start:
        clip_task(video, start, end, width)

async def shared_clip(video: Path, start: float, end: float, width: int) -> bytes:
    return await asyncio.shield(clip_task(video, start, end, width))


async def drain_clip_tasks() -> None:
    loop = asyncio.get_running_loop()
    owner = _clip_owner()
    matched = {
        key: task for key, task in _clip_tasks.items()
        if task.get_loop() is loop and key[-1] == owner
    }
    for key in matched:
        _clip_tasks.pop(key, None)
    tasks = [task for task in matched.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

async def drain_frame_tasks(video: Path) -> None:


    loop = asyncio.get_running_loop()
    tasks = [
        task
        for (video_path, _timestamp, _width), task in _frame_tasks.items()
        if video_path == str(video) and task.get_loop() is loop and not task.done()
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def grab_frame(video: Path, t: float, width: int = 512) -> bytes:
    return await asyncio.shield(_frame_task(video, t, width))
