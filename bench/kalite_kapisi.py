#!/usr/bin/env python3


from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dortgoz.config import settings
from dortgoz.pipeline.interpret import interpret_window

MEDIA = ROOT / "media"



def _win(dur: float) -> tuple[tuple[float, float], list[float]]:
    end = round(dur - 0.6, 1)
    step = end / 6.0
    return (0.0, end), [round(step * (i + 0.5), 1) for i in range(6)]


_DURATIONS = {
    "Fighting018_x264.mp4": 46.3,
    "Assault020_x264.mp4": 15.9,
    "Explosion048_x264.mp4": 15.3,
    "Arson049_x264.mp4": 12.8,
    "RoadAccidents052_x264.mp4": 12.0,
    "Stealing095_x264.mp4": 25.0,
    "Normal_Videos055_x264.mp4": 8.3,
    "Normal_Videos649_x264.mp4": 35.8,
    "Abuse005_x264.mp4": 31.6,
    "Burglary043_x264.mp4": 17.9,
}

PROBES: list[tuple[str, tuple[float, float], list[float]]] = [
    (name, *_win(dur)) for name, dur in _DURATIONS.items()
]


async def _one(name: str, window: tuple[float, float],
               keys: list[float], *, think: bool = False,
               nkeys: int = 0) -> dict:
    video = MEDIA / name
    if nkeys and nkeys != len(keys):
        span = window[1] - window[0]
        step = span / nkeys
        keys = [round(window[0] + step * (i + 0.5), 1) for i in range(nkeys)]
    stats: dict = {}
    t0 = time.perf_counter()
    try:
        rep = await interpret_window(video, window, keys, think=think,
                                     stats=stats)
    except Exception as exc:
        return {"klip": name, "hata": f"{type(exc).__name__}: {exc}"}
    return {
        "klip": name,
        "durum_p": stats.get("durum_p"),
        "anomaly_type": rep.anomaly_type,
        "olay_sayisi": len(rep.events),
        "siddet": sorted({e.severity_hint for e in rep.events}),
        "ozet": rep.summary[:160],
        "sure_s": round(time.perf_counter() - t0, 2),
    }


async def run(concurrency: int, *, think: bool = False,
              nkeys: int = 0) -> dict:
    missing = [p[0] for p in PROBES if not (MEDIA / p[0]).is_file()]
    if missing:
        print(f"HATA: eksik klip: {missing}", file=sys.stderr)
        raise SystemExit(2)

    sem = asyncio.Semaphore(max(1, concurrency))

    async def guarded(p):
        async with sem:
            return await _one(*p, think=think, nkeys=nkeys)

    t0 = time.perf_counter()
    rows = await asyncio.gather(*(guarded(p) for p in PROBES))
    return {
        "es_zamanlilik": concurrency,
        "max_inflight": settings.max_inflight,
        "think": think,
        "keyframes": nkeys or len(PROBES[0][2]),
        "think_budget": settings.interpret_think_budget,
        "duvar_s": round(time.perf_counter() - t0, 1),
        "problar": rows,
    }


def compare(a: dict, b: dict) -> None:
    ax = {r["klip"]: r for r in a["problar"]}
    bx = {r["klip"]: r for r in b["problar"]}
    print(f"\n{'klip':30} {'A durum_p':>11} {'B durum_p':>11} {'|dlog|':>8}  karar")
    dlogs, flips = [], 0
    for k in sorted(ax):
        ra, rb = ax[k], bx.get(k, {})
        pa, pb = ra.get("durum_p"), rb.get("durum_p")
        if not pa or not pb or pa <= 0 or pb <= 0:
            print(f"{k[:30]:30} {str(pa):>11} {str(pb):>11} {'-':>8}  ÖLÇÜLEMEDİ")
            continue
        d = abs(math.log(pa / pb))
        dlogs.append(d)
        same = (ra["anomaly_type"] == rb["anomaly_type"]
                and (ra["olay_sayisi"] > 0) == (rb["olay_sayisi"] > 0))
        if not same:
            flips += 1
        mark = "aynı" if same else (f"DEĞİŞTİ {ra['anomaly_type']}->{rb['anomaly_type']}"
                                    f" olay {ra['olay_sayisi']}->{rb['olay_sayisi']}")
        print(f"{k[:30]:30} {pa:11.5f} {pb:11.5f} {d:8.3f}  {mark}")
    if dlogs:
        dlogs.sort()
        print(f"\n|dlog| ortanca {dlogs[len(dlogs)//2]:.3f}  maks {dlogs[-1]:.3f}")
    print(f"karar değişimi: {flips}/{len(ax)}")
    print(f"duvar süresi: A={a['duvar_s']}s  B={b['duvar_s']}s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--think", action="store_true", help="düşünme açık")
    ap.add_argument("--keyframes", type=int, default=0,
                    help="pencere başına kare sayısı (varsayılan 6)")
    ap.add_argument("--out", type=Path, required=False)
    ap.add_argument("--compare", nargs=2, type=Path,
                    help="iki sonuç dosyasını karşılaştır (koşu yapmaz)")
    args = ap.parse_args()

    if args.compare:
        a = json.loads(args.compare[0].read_text(encoding="utf-8"))
        b = json.loads(args.compare[1].read_text(encoding="utf-8"))
        compare(a, b)
        return

    res = asyncio.run(run(args.concurrency, think=args.think,
                          nkeys=args.keyframes))
    for r in res["problar"]:
        p = r.get("durum_p")
        print(f"  {r['klip'][:30]:30} durum_p={p if p is None else round(p, 5)!s:>9} "
              f"{r.get('anomaly_type', '?'):12} olay={r.get('olay_sayisi', '?')} "
              f"{r.get('sure_s', '?')}s")
    print(f"\neş zamanlılık {res['es_zamanlilik']} · düşünme {res['think']} · "
          f"kare {res['keyframes']} · duvar {res['duvar_s']}s")
    if args.out:
        args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"yazıldı: {args.out}")


if __name__ == "__main__":
    main()
