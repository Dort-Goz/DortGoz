#!/usr/bin/env python3
"""Canlı gecikme kaldıraçları: pencere süresi, klip genişliği, klip fps.

Her yapılandırmayı aynı kaynakta N kez koşar ve ortanca duvar süresini verir.
EVREN kararlı değildir; tek koşuya bakmayın, bu yüzden varsayılan 3 tekrardır.

    python bench/gecikme_probe.py media/kamera1.mp4 --tekrar 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.config import settings
from dortgoz.pipeline.interpret import interpret_window

# (etiket, pencere_sn, genislik)
YAPILANDIRMALAR = [
    ("30sn @540 (mevcut)", 30.0, 540),
    ("20sn @540", 20.0, 540),
    ("15sn @540", 15.0, 540),
    ("10sn @540", 10.0, 540),
    ("30sn @448", 30.0, 448),
    ("30sn @384", 30.0, 384),
    ("15sn @448", 15.0, 448),
]


async def _bir(klip: Path, sure: float) -> tuple[float, int, str]:
    keys = [sure * (j + 0.5) / 6 for j in range(6)]
    t0 = time.perf_counter()
    rapor = await interpret_window(klip, (0.0, sure), keys)
    return time.perf_counter() - t0, len(rapor.events), rapor.anomaly_type


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kaynak", type=Path)
    ap.add_argument("--tekrar", type=int, default=3)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    await _bir(a.kaynak, 10.0)  # ısınma
    satirlar = []
    for etiket, sure, genislik in YAPILANDIRMALAR:
        settings.video_input_width = genislik
        olcumler = []
        kararlar = []
        for _ in range(a.tekrar):
            try:
                sn, olay, tip = await _bir(a.kaynak, sure)
            except Exception as exc:
                print(f"{etiket:22} HATA {type(exc).__name__}: {str(exc)[:60]}")
                break
            olcumler.append(sn)
            kararlar.append(f"{tip}/{olay}")
        if not olcumler:
            continue
        satir = {"etiket": etiket, "pencere_sn": sure, "genislik": genislik,
                 "ortanca_sn": round(st.median(olcumler), 2),
                 "min_sn": round(min(olcumler), 2), "maks_sn": round(max(olcumler), 2),
                 "kararlar": kararlar}
        satirlar.append(satir)
        print(f"{etiket:22} ortanca {satir['ortanca_sn']:6.2f} sn  "
              f"(min {satir['min_sn']:.2f} maks {satir['maks_sn']:.2f})  {kararlar}")

    if a.out:
        a.out.write_text(json.dumps({"kaynak": str(a.kaynak), "tekrar": a.tekrar,
                                     "satirlar": satirlar}, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        print(f"yazıldı: {a.out}")


asyncio.run(main())
