#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
import urllib.request
from pathlib import Path

PROBE_TIMEOUT = 12.0
PARALLEL = 6
DELDOT_CATALOGUE = "https://tmc.deldot.gov/json/videocamera.json"

SAHNE = [
    ("yaya-merkez", r"MAIN ST|MARKET ST|KING ST|UNION ST|FRONT ST|THE GREEN|DOWNTOWN"),
    ("kampus", r"COLLEGE|UNIVERSITY|CAMPUS|SCHOOL"),
    ("otopark", r"MALL|SHOPPING|RETAIL|CENTER DR|CENTRE"),
    ("sahil", r"BEACH|BOARDWALK|OCEAN|REHOBOTH|BETHANY|DEWEY|FENWICK|INLET"),
    ("aktarma", r"TRANSIT|TERMINAL|STATION|AIRPORT|FERRY"),
    ("kopru", r"BRIDGE|CANAL|SPAN"),
    ("gise", r"TOLL|PLAZA|WEIGH"),
    ("park", r"PARK\b|TRAIL|MARINA|PIER|RIVERFRONT"),
]


def sahne_of(title: str) -> str:
    for label, pattern in SAHNE:
        if re.search(pattern, title, re.I):
            return label
    return "yol"


def slug(text: str, used: set[str]) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()[:40] or "kamera"
    base, i = s, 2
    while s in used:
        s, i = f"{base}-{i}", i + 1
    used.add(s)
    return s


def deldot_candidates() -> list[dict]:
    with urllib.request.urlopen(DELDOT_CATALOGUE, timeout=30) as response:
        body = json.load(response)
    out = []
    for cam in body.get("videoCameras", []):
        url = (cam.get("urls") or {}).get("m3u8")
        if not url or not cam.get("enabled") or cam.get("status") != "Active":
            continue
        out.append({"url": url, "desc": cam.get("title", "").strip()})
    return out


async def probe(url: str) -> dict | None:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
        "-of", "json", "-i", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    streams = json.loads(out or b"{}").get("streams") or []
    return streams[0] if streams else None


def parse_fps(raw: str | None) -> float:
    if not raw or "/" not in raw:
        return 0.0
    num, den = raw.split("/", 1)
    try:
        return float(num) / float(den) if float(den) else 0.0
    except ValueError:
        return 0.0


async def pull(url: str, seconds: float, fps: float) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-stats",
        "-i", url, "-t", str(seconds), "-an", "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    loop = asyncio.get_running_loop()
    began = loop.time()
    try:
        _, err = await asyncio.wait_for(proc.communicate(), seconds * 3 + 20)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"frames": 0, "ratio": 0.0, "warnings": 999, "elapsed": 0.0}
    text = err.decode("utf-8", "replace")
    frames = [int(m) for m in re.findall(r"frame=\s*(\d+)", text)]
    got = max(frames) if frames else 0
    expected = max(1.0, fps * seconds)
    warnings = len([line for line in text.splitlines()
                    if line.strip() and not line.startswith("frame=")])
    return {"frames": got, "ratio": min(1.0, got / expected),
            "warnings": warnings, "elapsed": round(loop.time() - began, 1)}


def score(pulled: dict) -> float:
    return round(pulled["ratio"] * 100 - min(pulled["warnings"], 20) * 2, 1)


def pick_varied(rows: list[dict], want: int) -> list[dict]:
    by_scene: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: -r["score"]):
        by_scene.setdefault(row["sahne"], []).append(row)
    order = sorted(by_scene, key=lambda s: (s == "yol", -by_scene[s][0]["score"]))
    picked: list[dict] = []
    while len(picked) < want and any(by_scene.values()):
        for scene in order:
            if not by_scene[scene]:
                continue
            picked.append(by_scene[scene].pop(0))
            if len(picked) >= want:
                break
    return picked


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("candidates", type=Path, nargs="?")
    ap.add_argument("--deldot", action="store_true",
                    help="adayları DelDOT kamera kataloğundan çek")
    ap.add_argument("--want", type=int, default=12)
    ap.add_argument("--probe-limit", type=int, default=120)
    ap.add_argument("--pull-seconds", type=float, default=20.0,
                    help="her aday bu kadar saniye gerçekten çekilir")
    ap.add_argument("--min-score", type=float, default=70.0)
    ap.add_argument("--out", type=Path, default=Path("config/live_feeds.json"))
    args = ap.parse_args()

    if args.deldot:
        cands = deldot_candidates()
    elif args.candidates:
        cands = json.load(args.candidates.open())
    else:
        ap.error("bir aday dosyası verin ya da --deldot kullanın")
    cands.sort(key=lambda c: sahne_of(c.get("desc", "")) == "yol")
    print(f"{len(cands)} aday · {args.pull_seconds:.0f} sn çekim testi", flush=True)

    sem = asyncio.Semaphore(PARALLEL)
    rows: list[dict] = []
    scanned = 0

    async def check(c: dict) -> None:
        nonlocal scanned
        async with sem:
            info = await probe(c["url"])
            if not info or info.get("codec_name") not in {"h264", "hevc"}:
                scanned += 1
                return
            fps = parse_fps(info.get("avg_frame_rate")) or 15.0
            pulled = await pull(c["url"], args.pull_seconds, fps)
        scanned += 1
        row = {
            "desc": c.get("desc", ""),
            "url": c["url"],
            "codec": info["codec_name"],
            "res": f"{info.get('width')}x{info.get('height')}",
            "fps": round(fps, 1),
            "sahne": sahne_of(c.get("desc", "")),
            "score": score(pulled),
            "ratio": round(pulled["ratio"], 2),
            "warnings": pulled["warnings"],
        }
        rows.append(row)
        mark = "✔" if row["score"] >= args.min_score else "·"
        print(f"  {mark} {row['score']:5.1f} {row['sahne']:12} "
              f"{row['res']:>9} {row['desc'][:44]}", flush=True)

    await asyncio.gather(*(check(c) for c in cands[:args.probe_limit]))

    keep = [r for r in rows if r["score"] >= args.min_score]
    print(f"\n{scanned} aday tarandı → {len(rows)} yayın verdi "
          f"→ {len(keep)} kararlı", flush=True)

    chosen = pick_varied(keep, args.want)
    used: set[str] = set()
    feeds = [{
        "name": slug(r["desc"] or r["sahne"], used),
        "url": r["url"],
        "desc": r["desc"],
        "codec": r["codec"],
        "res": r["res"],
        "sahne": r["sahne"],
    } for r in chosen]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(feeds, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    scenes = ", ".join(sorted({f["sahne"] for f in feeds}))
    print(f"→ {args.out} · {len(feeds)} akış · sahneler: {scenes}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
