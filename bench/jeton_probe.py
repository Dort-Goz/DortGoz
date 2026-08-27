#!/usr/bin/env python3
"""VLM istem jetonu neye bağlı? Genişlik ve pencere süresi taraması.

Çağrı süresinin neredeyse tamamı istem işlemedir (ölçüm 2026-08-27: 4454 jeton
/ 2410 tok/sn ~= 1.85 sn, ölçülen çağrı 1.88 sn). Bu yüzden gecikmeyi düşürmenin
yolu görsel jeton sayısını düşürmektir. Bu prob jetonu DOĞRUDAN ölçer; duvar
süresi EVREN yüküne göre oynadığı için tek başına yanıltır.

    python bench/jeton_probe.py media/kamera1.mp4 --tekrar 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.config import settings
from dortgoz.pipeline.interpret import interpret_window

# (etiket, pencere_sn, genislik)
YAPILANDIRMALAR = [
    ("15sn @540 (mevcut)", 15.0, 540),
    ("15sn @448", 15.0, 448),
    ("15sn @384", 15.0, 384),
    ("15sn @320", 15.0, 320),
    ("10sn @540", 10.0, 540),
    ("30sn @540", 30.0, 540),
]


async def _bir(klip: Path, sure: float) -> dict:
    stats: dict = {}
    keys = [sure * (j + 0.5) / 6 for j in range(6)]
    rapor = await interpret_window(klip, (0.0, sure), keys, stats=stats)
    return {"pt": stats.get("prompt_tokens"), "ct": stats.get("completion_tokens"),
            "olay": len(rapor.events), "tip": rapor.anomaly_type}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kaynak", type=Path)
    ap.add_argument("--tekrar", type=int, default=2)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    await _bir(a.kaynak, 10.0)  # ısınma
    satirlar = []
    taban = None
    for etiket, sure, genislik in YAPILANDIRMALAR:
        settings.video_input_width = genislik
        pts, cts, kararlar = [], [], []
        for _ in range(a.tekrar):
            try:
                r = await _bir(a.kaynak, sure)
            except Exception as exc:
                print(f"{etiket:22} HATA {type(exc).__name__}: {str(exc)[:60]}")
                break
            if r["pt"]:
                pts.append(r["pt"])
                cts.append(r["ct"] or 0)
            kararlar.append(f"{r['tip']}/{r['olay']}")
        if not pts:
            continue
        pt = st.median(pts)
        if taban is None:
            taban = pt
        satirlar.append({"etiket": etiket, "pencere_sn": sure, "genislik": genislik,
                         "istem_jetonu": pt, "uretim_jetonu": st.median(cts),
                         "tabana_oran": round(pt / taban, 3), "kararlar": kararlar})
        print(f"{etiket:22} istem {pt:7.0f} tok  uretim {st.median(cts):4.0f}  "
              f"taban x{pt / taban:5.2f}  {kararlar}")

    if a.out:
        a.out.write_text(json.dumps({"kaynak": str(a.kaynak), "satirlar": satirlar},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"yazıldı: {a.out}")


asyncio.run(main())
