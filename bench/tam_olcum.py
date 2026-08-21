#!/usr/bin/env python3
"""31 klipte uçtan uca ölçüm: yakalama, yanlış alarm, kategori, huni, hız.

Aynı komut her makinede aynı tabloyu üretir; makineler arası KIYASLANABİLİR.

    # backend ayakta olmalı (DORTGOZ_MOCK=0)
    cd backend && uv run python ../bench/tam_olcum.py

    # koşuyu tekrar yapmadan yalnız çözümle:
    cd backend && uv run python ../bench/tam_olcum.py --analiz-et
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media"
RUNS = ROOT / "runs"

# UCF klip ailesi -> beklenen kategori (events.py taksonomisi)
AILE = {
    "Abuse": "kavga", "Arrest": "bilinmeyen", "Arson": "yangin",
    "Assault": "saldiri", "Burglary": "hirsizlik", "Explosion": "patlama",
    "Fighting": "kavga", "RoadAccidents": "arac_kazasi", "Robbery": "hirsizlik",
    "Shooting": "silahli_olay", "Shoplifting": "hirsizlik",
    "Stealing": "hirsizlik", "Vandalism": "vandalizm",
}
MAX_ESZAMAN = 25


def aile(video: str) -> str:
    base = video.split("_x264")[0]
    if base.startswith("Normal"):
        return "Normal"
    return "".join(c for c in base if not c.isdigit())


async def kostur(base: str, videos: list[str], timeout: float) -> float:
    import websockets

    ws_url = base.replace("http", "ws", 1) + "/ws"
    t0 = time.monotonic()
    for grup in [videos[i:i + MAX_ESZAMAN]
                 for i in range(0, len(videos), MAX_ESZAMAN)]:
        done: set[str] = set()
        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
            feeds = {f"OLC-{i}": v for i, v in enumerate(grup)}
            for feed, video in feeds.items():
                await ws.send(json.dumps({"kind": "start_run", "video": video,
                                          "feed": feed, "mode": ""}))
            print(f"  {len(grup)} klip gönderildi…")
            son = time.monotonic() + timeout
            while len(done) < len(feeds) and time.monotonic() < son:
                try:
                    ev = json.loads(await asyncio.wait_for(
                        ws.recv(), timeout=max(1.0, son - time.monotonic())))
                except TimeoutError:
                    break
                f, p = ev.get("feed", ""), ev.get("payload", {})
                if (f in feeds and p.get("type") == "run_status"
                        and p.get("state") in ("done", "error")):
                    done.add(f)
            print(f"  {len(done)}/{len(feeds)} bitti")
    return time.monotonic() - t0


def topla(baslangic: float) -> list[dict]:
    """Her klip için EN SON koşuyu al."""
    en_son: dict[str, Path] = {}
    for f in RUNS.glob("*.jsonl"):
        if "canli-" in f.name or f.stat().st_mtime < baslangic:
            continue
        meta = f.with_suffix("").with_suffix(".meta.json")
        meta = f.parent / (f.stem + ".meta.json")
        try:
            video = json.loads(meta.read_text(encoding="utf-8")).get("video", "")
        except (OSError, ValueError):
            continue
        if not video or "/" in video:
            continue
        if video not in en_son or f.stat().st_mtime > en_son[video].stat().st_mtime:
            en_son[video] = f
    out = []
    for video, f in en_son.items():
        m: dict = {}
        inc: list[str] = []
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            p = json.loads(line).get("payload", {})
            if p.get("type") == "run_metrics":
                m = p
            elif p.get("type") == "incident_update":
                inc.append(p.get("anomaly_type"))
        out.append({"video": video, "metrics": m, "incidents": inc})
    return out


def video_suresi(videos: list[str]) -> float:
    tot = 0.0
    for v in videos:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(MEDIA / v)],
            capture_output=True, text=True).stdout.strip()
        try:
            tot += float(out)
        except ValueError:
            pass
    return tot


def rapor(rows: list[dict], duvar: float) -> dict:
    anom = [r for r in rows if aile(r["video"]) != "Normal"]
    norm = [r for r in rows if aile(r["video"]) == "Normal"]
    hit = [r for r in anom if r["incidents"]]
    fa = [r for r in norm if r["incidents"]]
    ok = [r for r in hit if AILE.get(aile(r["video"])) in r["incidents"]]

    agg: dict[str, float] = {}
    for r in rows:
        for k, v in r["metrics"].items():
            if isinstance(v, (int, float)):
                agg[k] = agg.get(k, 0) + v

    video_sn = video_suresi([r["video"] for r in rows])
    dagilim = Counter(x for r in hit for x in r["incidents"])

    print(f"\n{'='*58}\nKLİP: {len(rows)}  ({len(anom)} anomali / {len(norm)} normal)")
    print(f"{'='*58}")
    print(f"  YAKALAMA      {len(hit)}/{len(anom)}  (%{100*len(hit)/max(len(anom),1):.0f})")
    print(f"  YANLIŞ ALARM  {len(fa)}/{len(norm)}")
    print(f"  KATEGORİ      {len(ok)}/{len(hit)}  (%{100*len(ok)/max(len(hit),1):.0f})")
    print(f"  dağılım       {dict(dagilim.most_common())}")

    print("\n--- SÜZGEÇ HUNİSİ ---")
    print(f"  pencere                  {agg.get('windows_seen',0):>6.0f}")
    print(f"  taramadan geçen          {agg.get('windows_screened',0):>6.0f}"
          f"   (elenen {agg.get('windows_skipped_before_vlm',0):.0f})")
    print(f"  D-FINE kurtarma          {agg.get('dfine_rescue_count',0):>6.0f}")
    print(f"  VLM çağrısı              {agg.get('qwen_calls',0):>6.0f}"
          f"   (ikinci görüş {agg.get('second_pass_calls',0):.0f})")
    print(f"  olay güncellemesi        {sum(len(r['incidents']) for r in rows):>6.0f}")

    print("\n--- HIZ ---")
    islem = agg.get("total_runtime_ms", 0) / 1000
    print(f"  video                    {video_sn/60:>6.1f} dk")
    print(f"  duvar süresi             {duvar/60:>6.1f} dk")
    if duvar > 0:
        print(f"  AKIŞ HIZI                {video_sn/duvar:>6.2f}x gerçek zaman")
    for k, lab in (("siglip_total_ms", "tarama"), ("dfine_total_ms", "dedektör"),
                   ("qwen_total_ms", "VLM")):
        v = agg.get(k, 0) / 1000
        print(f"  {lab:24} {v:>6.0f} sn  (%{100*v/max(islem,1e-9):.0f})")
    if agg.get("qwen_calls"):
        print(f"  VLM çağrı başına         "
              f"{agg['qwen_total_ms']/1000/agg['qwen_calls']:>6.1f} sn")

    return {
        "klip": len(rows), "anomali": len(anom), "normal": len(norm),
        "yakalama": len(hit), "yanlis_alarm": len(fa), "kategori_dogru": len(ok),
        "dagilim": dict(dagilim), "metrikler": agg,
        "video_sn": video_sn, "duvar_sn": duvar,
        "kacirilan": sorted(r["video"] for r in anom if not r["incidents"]),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--timeout", type=float, default=2400.0)
    ap.add_argument("--out", type=Path, default=ROOT / "bench" / "results" / "tam_olcum.json")
    ap.add_argument("--analiz-et", action="store_true",
                    help="yeni koşu yapma, mevcut koşuları çözümle")
    args = ap.parse_args()

    videos = sorted(p.name for p in MEDIA.glob("*.mp4"))
    if not videos:
        print("media/ boş — scripts/fetch_ucf_samples.py çalıştırın", file=sys.stderr)
        return 2
    print(f"{len(videos)} klip bulundu")

    if args.analiz_et:
        baslangic, duvar = 0.0, 0.0
    else:
        baslangic = time.time() - 1
        duvar = await kostur(args.base, videos, args.timeout)

    rows = topla(baslangic)
    if not rows:
        print("koşu bulunamadı", file=sys.stderr)
        return 1
    ozet = rapor(rows, duvar)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"ozet": ozet, "klipler": rows},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nyazıldı: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
