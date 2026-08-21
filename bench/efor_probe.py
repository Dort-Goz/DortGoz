#!/usr/bin/env python3

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.config import settings                      # noqa: E402
from dortgoz.pipeline.interpret import interpret_window   # noqa: E402
from dortgoz.pipeline.thinking import EFFORT_LADDER, EFFORT_OFF  # noqa: E402


async def bir_kademe(klip: Path, pencere: tuple[float, float],
                     kareler: list[float], kademe: str) -> dict:
    stats: dict = {}
    timing: dict = {}
    t0 = time.monotonic()
    hata = ""
    rapor = None
    try:
        rapor = await interpret_window(
            klip, pencere, kareler, effort=kademe, stats=stats, timing=timing)
    except Exception as exc:
        hata = f"{type(exc).__name__}: {exc}"[:200]
    return {
        "kademe": kademe or "(aile varsayılanı)",
        "saniye": round(time.monotonic() - t0, 1),
        "olay": len(rapor.events) if rapor else None,
        "ozet": (rapor.summary[:110] if rapor else ""),
        "siddet": [e.severity_hint for e in rapor.events] if rapor else [],
        "durum_p": stats.get("durum_p"),
        "uretilen_token": stats.get("completion_tokens"),
        "gen_tps": stats.get("gen_tps"),
        "hata": hata,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klip", type=Path, required=True)
    ap.add_argument("--baslangic", type=float, default=0.0)
    ap.add_argument("--sure", type=float, default=30.0)
    ap.add_argument("--kare", type=int, default=6)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).parent / "results" / "efor_probe.json")
    a = ap.parse_args()

    pencere = (a.baslangic, a.baslangic + a.sure)
    kareler = [a.baslangic + a.sure * (i + 0.5) / a.kare for i in range(a.kare)]
    print(f"model={settings.main_model} klip={a.klip.name} "
          f"pencere={pencere[0]:.0f}-{pencere[1]:.0f} sn kare={a.kare}", flush=True)

    sonuclar = []
    for kademe in ("", EFFORT_OFF, *EFFORT_LADDER):
        r = await bir_kademe(a.klip, pencere, kareler, kademe)
        sonuclar.append(r)
        print(f"  {r['kademe']:20s} {r['saniye']:6.1f} sn · olay {r['olay']} "
              f"· tok {r['uretilen_token']} · {r['hata'] or 'tamam'}", flush=True)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"model": settings.main_model,
                                 "klip": a.klip.name, "sonuclar": sonuclar},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
