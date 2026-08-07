"""VLM ikili-doğruluk probu — llama.cpp yükseltmeleri/rebase'leri için regresyon kapısı.

Stealing095_x264.mp4 (0-25 sn) üzerinde üç deterministik interpret_window çağrısı
yapar ve dal olasılığını (durum_p) bilinen referanslarla karşılaştırır. Üretim
sıcaklığı örneklemeyi etkiler ama durum_p ilk dal token'ının ham olasılık
kütlesidir — aynı ikili + aynı sunucu bayraklarıyla koşudan koşuya kararlıdır
(2026-08-07'de sunucu yeniden başlatmaları arasında doğrulandı).

Referans değerler (sunucu bayrakları: -fa on -ctk/v q8_0 -b 4096 -ub 1024
--mtmd-batch-max-tokens 2048, model Qwen3.6-35B-A3B-UD-IQ3_XXS + mmproj-F16):

| prob    | üretim (master, solo yol) | zincirli ikili (mtmd-xslot 1d21773) |
|---------|---------------------------|--------------------------------------|
| 1-kare  | 0.0010                    | 0.0010  (solo yol — HER ikili AYNEN vermeli) |
| 2-kare  | 0.0011                    | 0.0012                               |
| 6-kare  | 0.0021                    | 0.0022                               |

Çok-kare değerleri toplu kodlama/zincir decode'un FP-sırası kaymasıyla ikili
nesline göre oynar; |Δlog| ~0,2 sınıfı normaldir. **|Δlog| > 0,7 (≈2×) her iki
referansa karşı da → FAIL** — yapısal bir hata (sıra karışması, konum hatası,
yanlış embedding) FP kaymasından böyle ayrılır (yapısal hatalar 10-1000× saptırır
ya da özet metnini bozar; 2026-08-07 çapraz-eşleşme matrisi ölçümü).

Kullanım:
    cd backend && uv run python ../bench/vlm_probe.py                    # model sunucusu üzerinden
    DORTGOZ_LLAMA_BASE_URL=http://127.0.0.1:8099/v1 DORTGOZ_MAIN_MODEL=x \
        uv run python ../bench/vlm_probe.py                              # elle sunucu
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
from pathlib import Path

from dortgoz.pipeline.interpret import interpret_window

VIDEO = Path(__file__).resolve().parents[1] / "media" / "Stealing095_x264.mp4"

# (ad, anahtar kareler, {referans_adı: durum_p})
PROBES = [
    ("1-kare", [14.0],
     {"uretim": 0.0010, "zincirli": 0.0010}),
    ("2-kare", [10.0, 18.0],
     {"uretim": 0.0011, "zincirli": 0.0012}),
    ("6-kare", [2.0, 6.0, 10.0, 14.0, 18.0, 22.0],
     {"uretim": 0.0021, "zincirli": 0.0022}),
]

FAIL_DLOG = 0.7  # her iki referansa da bundan uzaksa yapısal hata say


async def main() -> int:
    if not VIDEO.is_file():
        print(f"HATA: {VIDEO} yok — media/ altına Stealing095_x264.mp4 gerekir")
        return 2

    worst = "PASS"
    for name, keys, refs in PROBES:
        stats: dict = {}
        t0 = time.perf_counter()
        rep = await interpret_window(VIDEO, (0.0, 25.0), keys, stats=stats)
        dt = time.perf_counter() - t0
        p = stats.get("durum_p")
        if p is None or p <= 0:
            print(f"{name}: durum_p YOK — FAIL")
            worst = "FAIL"
            continue

        dlogs = {ref: abs(math.log(p / v)) for ref, v in refs.items()}
        best_ref, best_d = min(dlogs.items(), key=lambda kv: kv[1])
        if best_d < 0.05:
            verdict = f"PASS ({best_ref} referansıyla birebir)"
        elif best_d < FAIL_DLOG:
            verdict = f"WARN (en yakın {best_ref}, |dlog|={best_d:.2f} — FP kayması sınıfı)"
            if worst == "PASS":
                worst = "WARN"
        else:
            verdict = f"FAIL (|dlog|={best_d:.2f} > {FAIL_DLOG} — yapısal hata şüphesi)"
            worst = "FAIL"

        print(f"{name}: durum_p={p:.4f}  sure={dt:.1f}s  {verdict}")
        print(f"    ozet={rep.summary!r}")

    print(f"\nSONUC: {worst}")
    return 0 if worst == "PASS" else (1 if worst == "WARN" else 2)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
