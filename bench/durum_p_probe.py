#!/usr/bin/env python3
"""durum_p MTP altında hayatta kalıyor mu? (-dg profilinin VARLIK SEBEBİ bu)

durum_p, `"durum": "` sonrasındaki AKIŞ-İÇİ token'ın top_logprobs'unu okur —
yani MTP'nin taslaktan kabul ettiği token tipinin ta kendisi. İlk token her zaman
hedef modelden gelir, o yüzden basit bir logprobs sondası YANILTIR.
"""
import argparse, asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from dortgoz.pipeline.interpret import interpret_window  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klip", type=Path, required=True)
    ap.add_argument("--etiket", default="")
    ap.add_argument("--esz", type=int, default=1, help="1=sıralı, >1=GERÇEK eşzamanlı")
    a = ap.parse_args()
    async def bir(i: int) -> dict:
        bas = i * 20.0
        stats: dict = {}
        rapor = await interpret_window(a.klip, (bas, bas + 20.0),
                                       [bas + 20.0 * (j + 0.5) / 6 for j in range(6)],
                                       stats=stats)
        return {"pencere": bas, "durum_p": stats.get("durum_p"),
                "olay": len(rapor.events)}

    if a.esz > 1:                      # gerçek eşzamanlılık: yığınlama etkisi ölçülür
        cikti = list(await asyncio.gather(*(bir(i) for i in range(3))))
    else:
        cikti = [await bir(i) for i in range(3)]
    print(json.dumps({"etiket": a.etiket, "sonuc": cikti,
                      "durum_p_BOS": sum(1 for c in cikti if c["durum_p"] is None)},
                     ensure_ascii=False))

asyncio.run(main())
