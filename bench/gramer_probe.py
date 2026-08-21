#!/usr/bin/env python3
import argparse, asyncio, json, statistics as st, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from dortgoz.agent.llm import call_stats, create_chat, main_client   # noqa: E402
from dortgoz.config import settings                                  # noqa: E402
from dortgoz.pipeline import interpret as I                          # noqa: E402


async def tur(klip: Path, bas: float, sure: float, gramer: bool) -> dict:
    refs = I.build_frame_references([bas + sure * (j + 0.5) / 6 for j in range(6)])
    icerik = await I._frame_parts(klip, refs, captured_frames={}, frame_width=512)
    gorev = I.TASK_TR.replace("{start}", f"{bas:.0f}").replace("{end}", f"{bas+sure:.0f}")
    icerik.append({"type": "text", "text": gorev})
    sistem = I.SYSTEM_TR + "\n\n" + I.TIER_TR
    kw = {}
    if gramer:
        kw["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "window_report", "strict": True,
            "schema": I.tier_schema([f.frame_id for f in refs])}}
    t0 = time.monotonic()
    resp = await create_chat(main_client(), model=settings.main_model,
                             messages=[{"role": "system", "content": sistem},
                                       {"role": "user", "content": icerik}],
                             max_tokens=settings.interpret_max_tokens, temperature=0,
                             extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                             **kw)
    sn = time.monotonic() - t0
    s = call_stats(resp)
    return {"gramer": gramer, "sn": round(sn, 2), "ctok": s.get("completion_tokens"),
            "gen_tps": s.get("gen_tps"), "pp_tps": s.get("pp_tps")}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klip", type=Path, required=True)
    ap.add_argument("--tekrar", type=int, default=3)
    a = ap.parse_args()
    for gramer in (True, False):
        o = []
        for i in range(a.tekrar):
            o.append(await tur(a.klip, i * 20.0, 20.0, gramer))
        print(json.dumps({
            "gramer": gramer,
            "gen_tps_ortanca": round(st.median([x["gen_tps"] for x in o if x["gen_tps"]]), 1),
            "ctok_ortanca": st.median([x["ctok"] for x in o]),
            "sn_ortanca": round(st.median([x["sn"] for x in o]), 2)}, ensure_ascii=False),
            flush=True)

asyncio.run(main())
