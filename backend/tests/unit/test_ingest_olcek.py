"""Ölçekleme sözleşmesi: küçült, asla büyütme."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from dortgoz.pipeline.ingest import grab_clip, grab_frame, scale_filter

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe yok")


def _kaynak(path: Path, w: int, h: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={w}x{h}:rate=10:duration=2",
         "-c:v", "mpeg4", "-q:v", "3", str(path)], check=True)
    return path


def _boyut(data: bytes, tmp_path: Path, suffix: str) -> tuple[int, int]:
    out = tmp_path / f"cikti{suffix}"
    out.write_bytes(data)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = probe.split(",")[:2]
    return int(w), int(h)


@pytest.mark.asyncio
async def test_kucuk_kaynak_buyutulmez(tmp_path) -> None:
    src = _kaynak(tmp_path / "kucuk.mp4", 320, 240)
    assert await _clip_boyut(src, tmp_path, 540) == (320, 240)


@pytest.mark.asyncio
async def test_buyuk_kaynak_kucultulur(tmp_path) -> None:
    src = _kaynak(tmp_path / "buyuk.mp4", 1280, 720)
    w, h = await _clip_boyut(src, tmp_path, 540)
    assert w == 540 and h % 2 == 0 and abs(h - 304) <= 2


@pytest.mark.asyncio
async def test_kare_de_buyutulmez(tmp_path) -> None:
    src = _kaynak(tmp_path / "kare.mp4", 320, 240)
    assert _boyut(await grab_frame(src, 0.5, 512), tmp_path, ".jpg") == (320, 240)


async def _clip_boyut(src: Path, tmp_path: Path, width: int) -> tuple[int, int]:
    return _boyut(await grab_clip(src, 0.0, 1.5, width), tmp_path, ".mp4")


def test_suzgec_virgulu_tirnak_icinde_tutar() -> None:
    assert scale_filter(540) == "scale='min(540,iw)':-2:flags=lanczos"


@pytest.mark.asyncio
async def test_klip_h264_uretir(tmp_path) -> None:
    """Klip gerçekten H.264 çıkar ve geri çözülür."""
    src = _kaynak(tmp_path / "kodek.mp4", 640, 480)
    out = tmp_path / "klip.mp4"
    out.write_bytes(await grab_clip(src, 0.0, 1.5, 540))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        check=True, capture_output=True, text=True).stdout.strip()
    assert probe == "h264"


@pytest.mark.asyncio
@pytest.mark.parametrize("kodlayici,beklenen", [
    ("libx264", ["-preset", "veryfast"]),
    ("libopenh264", ["-b:v", "1200k"]),
    ("mpeg4", ["-q:v", "5"]),
])
async def test_kodlayici_yoksa_geri_duser(monkeypatch, kodlayici, beklenen) -> None:
    from dortgoz.pipeline.ingest import clip_codec
    from dortgoz.tools import context_clip

    monkeypatch.setattr(context_clip, "_video_encoder", kodlayici)
    secilen = await clip_codec()
    assert secilen[1] == kodlayici
    for bayrak in beklenen:
        assert bayrak in secilen


@pytest.mark.asyncio
async def test_toplu_kare_cikarma_tek_surecte(tmp_path) -> None:
    """Birden çok kare tek ffmpeg süreciyle, doğru sayıda ve ayrı ayrı gelir."""
    from dortgoz.pipeline.ingest import grab_frames

    src = _kaynak(tmp_path / "toplu.mp4", 320, 240)
    kareler = await grab_frames(src, [0.3, 0.9, 1.5], 320)
    assert len(kareler) == 3
    for jpeg in kareler.values():
        assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
    # testsrc her karede degisir: kareler birbirinden farkli olmali.
    assert len(set(kareler.values())) == 3


@pytest.mark.asyncio
async def test_toplu_kare_sayi_tutmazsa_bos_doner(tmp_path) -> None:
    """Sözleşme: sayı tutmazsa boş döner ki çağıran eski yola düşebilsin."""
    from dortgoz.pipeline.ingest import grab_frames

    src = _kaynak(tmp_path / "kisa.mp4", 320, 240)
    # Klip 2 sn; 9. saniye hicbir kare vermez.
    assert await grab_frames(src, [0.5, 9.0], 320) == {}


@pytest.mark.asyncio
async def test_perception_toplu_rgb(tmp_path) -> None:
    from dortgoz.pipeline import perception

    src = _kaynak(tmp_path / "rgb.mp4", 320, 240)
    frames = await perception.frames_rgb(src, [0.3, 0.9])
    assert frames is not None and len(frames) == 2
    boyut = perception.SIZE * perception.SIZE * 3
    assert all(len(f) == boyut for f in frames)


@pytest.mark.asyncio
async def test_grab_many_sirayi_ve_yinelemeyi_korur(tmp_path) -> None:
    """Çağıranın sırası korunur; aynı damga iki kez istenirse iki kez döner."""
    from dortgoz.pipeline.ingest import grab_many

    src = _kaynak(tmp_path / "sira.mp4", 320, 240)
    istenen = [1.2, 0.4, 1.2]
    kareler = await grab_many(src, istenen, 320)
    assert len(kareler) == 3
    assert kareler[0] == kareler[2] != kareler[1]


@pytest.mark.asyncio
async def test_grab_many_toplu_basarisizsa_kare_basina_duser(tmp_path) -> None:
    """Toplu yol boş dönse bile sonuç eksiksiz gelir."""
    from dortgoz.pipeline import ingest

    src = _kaynak(tmp_path / "geri.mp4", 320, 240)

    async def bos_toplu(*_a, **_k):
        return {}

    ingest_grab_frames = ingest.grab_frames
    ingest.grab_frames = bos_toplu
    try:
        kareler = await ingest.grab_many(src, [0.4, 1.2], 320)
    finally:
        ingest.grab_frames = ingest_grab_frames
    assert len(kareler) == 2 and all(k.startswith(b"\xff\xd8") for k in kareler)
