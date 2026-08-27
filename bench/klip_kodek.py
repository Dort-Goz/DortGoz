#!/usr/bin/env python3
"""VLM'e gönderilen klip için kodek ölçümü.

Her aday için üç sayı verir: kodlama süresi (pencere başına sıcak yol),
çıktı boyutu (base64 gövdesi olarak yüklenir) ve SSIM (küçük uzak nesnelerin
korunması). Kaynak, üretimdeki ölçekleme süzgecinden geçirilir.

    python bench/klip_kodek.py media/kamera1.mp4 --sure 30
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.pipeline.ingest import scale_filter

ADAYLAR: dict[str, list[str]] = {
    "mpeg4_q5 (mevcut)": ["-c:v", "mpeg4", "-q:v", "5"],
    "mpeg4_q2": ["-c:v", "mpeg4", "-q:v", "2"],
    "x264_ultrafast_crf23": ["-c:v", "libx264", "-preset", "ultrafast",
                             "-crf", "23", "-pix_fmt", "yuv420p"],
    "x264_superfast_crf23": ["-c:v", "libx264", "-preset", "superfast",
                             "-crf", "23", "-pix_fmt", "yuv420p"],
    "x264_veryfast_crf23": ["-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "23", "-pix_fmt", "yuv420p"],
    "x264_veryfast_crf28": ["-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "28", "-pix_fmt", "yuv420p"],
}


def _kodla(src: Path, out: Path, sure: float, genislik: int,
           kodek: list[str]) -> float:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "0", "-to", f"{sure:.3f}",
           "-i", str(src), "-map", "0:v:0", "-an", "-vf", scale_filter(genislik),
           *kodek, "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", str(out)]
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - t0


def _ssim(kodlanmis: Path, src: Path, sure: float, genislik: int) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "info", "-i", str(kodlanmis),
         "-ss", "0", "-to", f"{sure:.3f}", "-i", str(src),
         "-lavfi", f"[1:v]{scale_filter(genislik)}[ref];[0:v][ref]ssim",
         "-f", "null", "-"],
        capture_output=True, text=True)
    m = re.search(r"All:([0-9.]+)", proc.stderr)
    return float(m.group(1)) if m else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kaynak", type=Path)
    ap.add_argument("--sure", type=float, default=30.0)
    ap.add_argument("--genislik", type=int, default=540)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    tmp = Path("/tmp/dortgoz_kodek")
    tmp.mkdir(exist_ok=True)
    satirlar = []
    for ad, kodek in ADAYLAR.items():
        hedef = tmp / f"{ad.split()[0]}.mp4"
        try:
            sn = _kodla(a.kaynak, hedef, a.sure, a.genislik, kodek)
        except subprocess.CalledProcessError:
            print(f"{ad:24} KODLANAMADI (kodlayıcı yok)")
            continue
        boyut = hedef.stat().st_size
        satirlar.append({
            "aday": ad, "kodlama_sn": round(sn, 2),
            "boyut_kb": round(boyut / 1024),
            "base64_kb": round(boyut * 4 / 3 / 1024),
            "ssim": round(_ssim(hedef, a.kaynak, a.sure, a.genislik), 5),
        })
        r = satirlar[-1]
        print(f"{ad:24} {r['kodlama_sn']:6.2f} sn  {r['boyut_kb']:6d} KB  "
              f"base64 {r['base64_kb']:6d} KB  SSIM {r['ssim']:.5f}")

    if a.out:
        a.out.write_text(json.dumps(
            {"kaynak": str(a.kaynak), "sure": a.sure, "genislik": a.genislik,
             "adaylar": satirlar}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"yazıldı: {a.out}")


if __name__ == "__main__":
    main()
