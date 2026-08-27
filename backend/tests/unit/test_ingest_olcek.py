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
