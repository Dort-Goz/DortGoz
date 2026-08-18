#!/usr/bin/env python3
"""Görü profili hız probu — aynı pencere okumasını N kez yapıp llama.cpp
`timings` alanlarını raporlar (kare kodlama dahil PE, üretim t/s).

Profil varyantlarını KIYASLAMAK için: sunucu elle bir portta ayağa kaldırılır,
bu prob aynı iş yükünü her varyanta uygular. DörtGöz'ün GERÇEK yolunu kullanır
(interpret_window + GBNF şema), sentetik istek değil.

Kullanım:
  DORTGOZ_LLAMA_BASE_URL=http://127.0.0.1:9099/v1 \
  DORTGOZ_MAIN_MODEL=test uv run python ../bench/hiz_probe.py --klip ../media/Abuse005_x264.mp4
"""
import argparse, asyncio, json, statistics as st, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from dortgoz.config import settings                      # noqa: E402
from dortgoz.pipeline.interpret import interpret_window   # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klip", type=Path, required=True)
    ap.add_argument("--tekrar", type=int, default=3)
    ap.add_argument("--etiket", default="")
    ap.add_argument("--sure", type=float, default=20.0, help="pencere uzunluğu (klip kısa)")
    a = ap.parse_args()
    # ⚠ Her tekrar FARKLI pencere: aynı pencere tekrarlanırsa llama.cpp önek
    # önbelleği prompt'u servis eder ve PE ölçümü kare kodlamayı HİÇ içermez
    # (ilk sürümde 1,9 sn/okuma çıktı, gerçek koşuda 9,6 sn).
    olcum = []
    for i in range(a.tekrar + 1):          # ilk tur ısınma
        bas = i * a.sure
        pencere = (bas, bas + a.sure)
        kareler = [bas + a.sure * (j + 0.5) / 6 for j in range(6)]
        stats: dict = {}
        t0 = time.monotonic()
        await interpret_window(a.klip, pencere, kareler, stats=stats)
        sn = time.monotonic() - t0
        if i:
            olcum.append({"sn": sn, "ptok": stats.get("prompt_tokens"),
                          "ctok": stats.get("completion_tokens"),
                          "pp": stats.get("pp_tps"), "gen": stats.get("gen_tps")})
    med = lambda k: st.median([o[k] for o in olcum if o[k] is not None] or [0])
    print(json.dumps({"etiket": a.etiket or settings.main_model,
                      "sn": round(med("sn"), 2), "ptok": med("ptok"),
                      "ctok": med("ctok"), "img_pp_tps": round(med("pp"), 1),
                      "gen_tps": round(med("gen"), 1)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
