#!/usr/bin/env python3
"""Canlı akış adaylarını ffprobe ile tarar, çalışanlardan live_feeds.json üretir.

Girdi: aday listesi JSON — `[{"desc": "...", "url": "https://….m3u8"}, …]`
(ör. OpenTrafficCamMap'ten süzülmüş; MIT lisanslı, kamu DOT kameraları).
Çıktı: `config/live_feeds.json` biçiminde doğrulanmış liste + tarama raporu.

    python scripts/canli_kaynak_tara.py adaylar.json --want 25 --out config/live_feeds.json

Akışlar İNDİRİLMEZ; yalnız üstveri okunur (codec/çözünürlük, ~her aday birkaç
sn). Kamu yayını geliştirme/prova içindir; kayıtlar repoya girmez (veri
politikası) ve final hava boşluklu — aynı hat yerel RTSP ile çalışır.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
from pathlib import Path

PROBE_TIMEOUT = 12.0
PARALLEL = 10


def slug(text: str, used: set[str]) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()[:40] or "kamera"
    base, i = s, 2
    while s in used:
        s, i = f"{base}-{i}", i + 1
    used.add(s)
    return s


async def probe(url: str) -> dict | None:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height",
        "-of", "json", "-i", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    streams = json.loads(out or b"{}").get("streams") or []
    return streams[0] if streams else None


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("candidates", type=Path)
    ap.add_argument("--want", type=int, default=25)
    ap.add_argument("--probe-limit", type=int, default=120,
                    help="en çok bu kadar aday denenir")
    ap.add_argument("--out", type=Path, default=Path("config/live_feeds.json"))
    args = ap.parse_args()

    cands = json.load(args.candidates.open())
    sem = asyncio.Semaphore(PARALLEL)
    good: list[dict] = []
    used: set[str] = set()
    done = 0

    async def check(c: dict) -> None:
        nonlocal done
        if len(good) >= args.want:
            return
        async with sem:
            if len(good) >= args.want:
                return
            info = await probe(c["url"])
        done += 1
        if info and info.get("codec_name") in {"h264", "hevc"}:
            good.append({
                "name": slug(c.get("desc") or c.get("region", "kamera"), used),
                "url": c["url"],
                "desc": c.get("desc", ""),
                "codec": info["codec_name"],
                "res": f"{info.get('width')}x{info.get('height')}",
            })
            print(f"  ✔ {good[-1]['name']} ({good[-1]['res']})", flush=True)

    await asyncio.gather(*(check(c) for c in cands[:args.probe_limit]))
    print(f"\n{done} aday tarandı → {len(good)} çalışıyor", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(good[:args.want], ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"→ {args.out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
