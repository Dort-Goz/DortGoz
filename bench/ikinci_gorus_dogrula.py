#!/usr/bin/env python3

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from dortgoz.pipeline.interpret import (
    SYSTEM_TR_GENIS, SYSTEM_TR_IKINCI, interpret_window)
from dortgoz.pipeline.windowing import select_keyframes
from dortgoz.pipeline import ingest
from dortgoz.config import settings
from make_long_feed import resolve_ucf

SEVERITY = ["dusuk", "orta", "yuksek", "kritik"]
ALARM_FLOOR = 1


def tirmanacaklar(yol: Path, hareket: float) -> list[dict]:
    secim = []
    for satir in yol.read_text(encoding="utf-8").splitlines():
        if not satir.strip():
            continue
        o = json.loads(satir)
        if "clip" not in o:
            continue
        for w in o.get("windows", []):
            if w.get("gated"):
                continue
            if w.get("n_events", 0) == 0 and (w.get("motion") or 0) >= hareket:
                secim.append({"clip": o["clip"], "anomaly": o.get("anomaly"),
                              "start": w["start"], "end": w["end"],
                              "motion": w.get("motion")})
    return secim


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--birincil", type=Path, required=True)
    ap.add_argument("--model", default="qwen3.8-27b-vision-dg")
    ap.add_argument("--efor", default="", help="'' = düşünmesiz (ölçülen en iyi)")
    ap.add_argument("--hareket", type=float, default=0.30)
    ap.add_argument("--istem", choices=["varsayilan", "genis", "ikinci"],
                    default="varsayilan", help="ikinci görüş sistem istemi")
    ap.add_argument("--esz", type=int, default=4,
                    help="eşzamanlı pencere (profil -np ile uyumlu olmalı)")
    ap.add_argument("--ucf", type=Path)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).parent / "results" / "ikinci_gorus_testsplit.jsonl")
    a = ap.parse_args()

    videolar = resolve_ucf(a.ucf)
    secim = tirmanacaklar(a.birincil, a.hareket)

    bitmis = set()
    if a.out.exists():
        for satir in a.out.read_text(encoding="utf-8").splitlines():
            if satir.strip():
                r = json.loads(satir)
                bitmis.add((r["clip"], r["start"]))
    kalan = [s for s in secim if (s["clip"], s["start"]) not in bitmis]
    print(f"birincil={a.birincil.name} · ölçüt: olaysız & hareket>={a.hareket}\n"
          f"{len(secim)} pencere tırmanacak · {len(bitmis)} bitmiş · {len(kalan)} kaldı\n"
          f"model={a.model} efor={a.efor or 'düşünmesiz'}", flush=True)

    t0 = time.time()
    profiller: dict[str, list] = {}
    profil_gorevleri: dict[str, asyncio.Task] = {}
    yaz_kilidi = asyncio.Lock()
    sem = asyncio.Semaphore(a.esz)
    sayac = {"n": 0}

    async def isle(s: dict) -> None:
      async with sem:
        adaylar = list(videolar.rglob(s["clip"]))
        if not adaylar:
            kayit = {**s, "hata": "klip diskte yok"}
        else:
            video = adaylar[0]
            try:
                if s["clip"] not in profil_gorevleri:
                    profil_gorevleri[s["clip"]] = asyncio.create_task(
                        ingest.motion_profile(video, settings.base_fps))
                profiller[s["clip"]] = await profil_gorevleri[s["clip"]]
                kareler = select_keyframes(profiller[s["clip"]], s["start"],
                                           s["end"], settings.keyframes_per_window)
                ts = time.monotonic()
                rapor = await interpret_window(
                    video, (s["start"], s["end"]), kareler, model=a.model,
                    effort=a.efor,
                    system_prompt={"genis": SYSTEM_TR_GENIS,
                                   "ikinci": SYSTEM_TR_IKINCI}.get(a.istem, ""))
                sev = max((SEVERITY.index(e.severity_hint) for e in rapor.events),
                          default=-1)
                kayit = {**s, "n_events": len(rapor.events), "severity": sev,
                         "alarm": sev >= ALARM_FLOOR,
                         "deep_s": round(time.monotonic() - ts, 2),
                         "summary": rapor.summary[:200]}
            except Exception as exc:
                kayit = {**s, "hata": f"{type(exc).__name__}: {exc}"[:200]}
        async with yaz_kilidi:
            with a.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
            sayac["n"] += 1
            n = sayac["n"]
            if n % 10 == 0 or n == len(kalan):
                hiz = (time.time() - t0) / n
                print(f"[{n}/{len(kalan)}] {hiz:.1f} sn/pencere (duvar) · "
                      f"ETA {(len(kalan)-n)*hiz/60:.0f} dk", flush=True)

    await asyncio.gather(*(isle(x) for x in kalan))

    puanla(a.birincil, a.out, a.hareket)


def puanla(birincil: Path, ikinci: Path, hareket: float) -> None:
    klipler: dict[str, dict] = {}
    for satir in birincil.read_text(encoding="utf-8").splitlines():
        if not satir.strip():
            continue
        o = json.loads(satir)
        if "clip" not in o:
            continue
        olayli = any(w.get("n_events", 0) > 0 for w in o.get("windows", [])
                     if not w.get("gated"))
        klipler[o["clip"]] = {"anomaly": o.get("anomaly"), "birincil": olayli,
                              "ikinci": False}
    ek_gpu = 0.0
    for satir in ikinci.read_text(encoding="utf-8").splitlines():
        if not satir.strip():
            continue
        r = json.loads(satir)
        ek_gpu += r.get("deep_s", 0.0)
        if r.get("alarm") and r["clip"] in klipler:
            klipler[r["clip"]]["ikinci"] = True

    anom = [k for k in klipler.values() if k["anomaly"]]
    norm = [k for k in klipler.values() if not k["anomaly"]]
    b_yak = sum(1 for k in anom if k["birincil"])
    t_yak = sum(1 for k in anom if k["birincil"] or k["ikinci"])
    b_fa = sum(1 for k in norm if k["birincil"])
    t_fa = sum(1 for k in norm if k["birincil"] or k["ikinci"])
    print("\n## Tırmandırma doğrulaması — TAM test bölmesi\n")
    print(f"| Kol | yakalama ({len(anom)} anomali) | yanlış alarm ({len(norm)} normal) |")
    print("|---|---|---|")
    print(f"| birincil (35B) | {b_yak} | {b_fa} |")
    print(f"| + ikinci görüş (hareket≥{hareket}) | **{t_yak}** (+{t_yak-b_yak}) | "
          f"**{t_fa}** (+{t_fa-b_fa}) |")
    print(f"\nEk GPU maliyeti: {ek_gpu:.0f} sn ({ek_gpu/60:.1f} dk)")


if __name__ == "__main__":
    asyncio.run(main())
