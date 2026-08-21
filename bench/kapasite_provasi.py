#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import functools
import time

print = functools.partial(print, flush=True)
from pathlib import Path

import httpx
import websockets


async def run(base: str, feeds: int, timeout: float, out: Path) -> int:
    ws_url = base.replace("http", "ws", 1) + "/ws"
    async with httpx.AsyncClient(base_url=base) as http:
        videos = (await http.get("/api/videos")).json()
    pool = [v for v in videos if v.lower().startswith("kamera")] or videos
    if not pool:
        print("media/ boş — önce scripts/make_long_feed.py çalıştırın")
        return 2

    stats: dict[str, dict] = {}
    t0 = time.monotonic()

    async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"kind": "sync", "from_seq": 0}))
        for i in range(feeds):
            feed = f"KAM-{i + 1}"
            video = pool[i % len(pool)]
            stats[feed] = {"video": video, "states": [], "speeds": [],
                           "first_report": None, "done_at": None, "error": ""}
            await ws.send(json.dumps({"kind": "start_run", "video": video,
                                      "feed": feed, "mode": ""}))
        print(f"{feeds} akış gönderildi ({len(pool)} farklı kayıt) — izleniyor…")

        deadline = t0 + timeout
        while time.monotonic() < deadline:
            active = [f for f, s in stats.items()
                      if s["done_at"] is None and not s["error"]]
            if not active:
                break
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=max(1.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            event = json.loads(raw)
            feed, p = event.get("feed", ""), event.get("payload", {})
            if feed not in stats:
                continue
            s, kind = stats[feed], p.get("type")
            now = round(time.monotonic() - t0, 1)
            if kind == "run_status":
                state = p.get("state")
                if not s["states"] or s["states"][-1][1] != state:
                    s["states"].append((now, state))
                if p.get("speed"):
                    s["speeds"].append(round(p["speed"], 2))
                if state == "done":
                    s["done_at"] = now
                    print(f"  ✔ {feed} bitti @{now}s "
                          f"(son hız {s['speeds'][-1] if s['speeds'] else '?'}×)")
                elif state == "error":
                    s["error"] = p.get("detail", "?")
                    print(f"  ✖ {feed} HATA @{now}s: {s['error']}")
            elif kind == "window_report" and s["first_report"] is None:
                s["first_report"] = now

    wall = round(time.monotonic() - t0, 1)
    done = sum(1 for s in stats.values() if s["done_at"] is not None)
    errs = sum(1 for s in stats.values() if s["error"])
    stuck = feeds - done - errs
    firsts = sorted(s["first_report"] for s in stats.values() if s["first_report"])
    speeds = [x for s in stats.values() for x in s["speeds"]]
    summary = {
        "feeds": feeds, "wall_s": wall, "done": done, "error": errs,
        "bitmedi": stuck,
        "ilk_sonuc_min_maks_sn": [firsts[0], firsts[-1]] if firsts else None,
        "hiz_medyan": sorted(speeds)[len(speeds) // 2] if speeds else None,
        "hiz_min": min(speeds) if speeds else None,
    }
    out.write_text(json.dumps({"ozet": summary, "akislar": stats},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nÖZET: {json.dumps(summary, ensure_ascii=False)}\n→ {out}")
    return 0 if errs == 0 and done > 0 else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--feeds", type=int, default=24)
    ap.add_argument("--timeout", type=float, default=7200.0,
                    help="toplam bekleme (sn); dolunca eldeki veriyle raporlar")
    ap.add_argument("--out", type=Path, default=Path("prova.json"))
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.base, args.feeds, args.timeout, args.out)))


if __name__ == "__main__":
    main()
