#!/usr/bin/env python3
"""Ajan/diyalog katmanı probu — düşünme kademesi araç çağırmayı bozuyor mu?

Diyalog katmanında düşünme KAPALIYDI: bütçesiz düşünme 700 token tavanını
`reasoning_content` ile doldurup `content`i boş bırakıyordu. Bütçe bunu çözmeli.
Ama düşünme + araç çağırma birlikte doğrulanmadı — bu prob iki soruyu ölçer:

  1. `content` BOŞ gelir mi (eski arıza geri mi geliyor)?
  2. Araç çağrısı hâlâ yapılandırılmış `tool_calls` olarak mı dönüyor?

Yarışma puanının %20'si "Otonomi ve Zekâ (diyalog davranışı dahil)" olduğu için
bu iki soru doğrudan puana bakar.

Kullanım:
  cd backend && uv run python ../bench/ajan_efor_probe.py [--model qwen3.8-27b-vision-dg]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.agent import tools                       # noqa: E402
from dortgoz.agent.llm import create_chat, main_client  # noqa: E402
from dortgoz.config import settings                   # noqa: E402
from dortgoz.pipeline.thinking import EFFORT_LADDER, thinking_extra  # noqa: E402

# İki senaryo: biri düz Türkçe yanıt ister, biri araç çağırmayı ZORLAR.
SENARYOLAR = [
    ("düz yanıt", "Merhaba, şu an sistemde kaç kamera akışı izleniyor ve "
                  "son durumu bir cümleyle özetler misin?"),
    ("araç çağrısı", "12. saniyedeki olaya git ve o anı işaretle."),
]


async def tek(model: str, kademe: str, soru: str) -> dict:
    client = main_client()
    t0 = time.monotonic()
    try:
        resp = await create_chat(
            client, model=model,
            messages=[{"role": "user", "content": soru}],
            max_tokens=2200 if kademe else 700,
            temperature=0.3,
            tools=tools.TOOLS, parallel_tool_calls=False,
            extra_body=thinking_extra(think=False, effort=kademe,
                                      budget=settings.agent_think_budget),
        )
    except Exception as exc:
        return {"kademe": kademe or "kapalı", "hata": f"{type(exc).__name__}: {exc}"[:160]}
    msg = resp.choices[0].message
    icerik = msg.content or ""
    cagrilar = [tc.function.name for tc in (msg.tool_calls or [])]
    return {
        "kademe": kademe or "kapalı",
        "saniye": round(time.monotonic() - t0, 1),
        "icerik_uzunluk": len(icerik),
        "BOS_ICERIK": (not icerik.strip()) and not cagrilar,
        "arac": cagrilar,
        "finish": resp.choices[0].finish_reason,
        "ornek": icerik[:120].replace("\n", " "),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=settings.main_model)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).parent / "results" / "ajan_efor_probe.json")
    a = ap.parse_args()
    print(f"model={a.model} · araç sayısı={len(tools.TOOLS)}", flush=True)

    hepsi = []
    for ad, soru in SENARYOLAR:
        print(f"\n--- senaryo: {ad} ---", flush=True)
        for kademe in ("", *EFFORT_LADDER):
            r = await tek(a.model, kademe, soru)
            r["senaryo"] = ad
            hepsi.append(r)
            if "hata" in r:
                print(f"  {r['kademe']:8s} HATA {r['hata']}", flush=True)
            else:
                bayrak = "⚠ BOŞ İÇERİK" if r["BOS_ICERIK"] else "tamam"
                print(f"  {r['kademe']:8s} {r['saniye']:6.1f} sn · içerik {r['icerik_uzunluk']:4d} "
                      f"· araç {r['arac'] or '—'} · {bayrak}", flush=True)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"model": a.model, "sonuclar": hepsi},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n-> {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
