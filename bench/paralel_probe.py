#!/usr/bin/env python3
import argparse, asyncio, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from dortgoz.pipeline.interpret import interpret_window  # noqa: E402


async def bir(klip: Path, bas: float, sure: float) -> float:
    t0 = time.monotonic()
    await interpret_window(klip, (bas, bas + sure),
                           [bas + sure * (j + 0.5) / 6 for j in range(6)])
    return time.monotonic() - t0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klip", type=Path, required=True)
    ap.add_argument("--esz", type=int, default=1, help="aynı anda kaç pencere")
    ap.add_argument("--toplam", type=int, default=6)
    ap.add_argument("--sure", type=float, default=10.0)
    ap.add_argument("--etiket", default="")
    a = ap.parse_args()

    await bir(a.klip, 0.0, a.sure)
    sem = asyncio.Semaphore(a.esz)

    async def gorev(i: int) -> float:
        async with sem:
            return await bir(a.klip, (i % 6) * a.sure, a.sure)

    t0 = time.monotonic()
    sureler = await asyncio.gather(*(gorev(i) for i in range(a.toplam)))
    duvar = time.monotonic() - t0
    print(json.dumps({"etiket": a.etiket, "esz": a.esz, "pencere": a.toplam,
                      "duvar_sn": round(duvar, 1),
                      "pencere_dk": round(a.toplam / duvar * 60, 1),
                      "ort_gecikme_sn": round(sum(sureler) / len(sureler), 2)},
                     ensure_ascii=False))

asyncio.run(main())
