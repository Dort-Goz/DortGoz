#!/usr/bin/env python3
import json, os, sys

cat = sys.argv[1]
workroot = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/datasets/Dort_Goz/_clean_work")
work = f"{workroot}/{cat}"
man = os.path.expanduser(f"~/datasets/Dort_Goz/UCF_Crimes/Videos/{cat}_new/_manifest.json")

os.makedirs(f"{work}/recipes", exist_ok=True)
entries = json.load(open(man))
n = 0
for e in entries:
    r = {
        "video": e["video"],
        "keep": e.get("keep"),
        "crop": e.get("crop"),
        "masks": e.get("masks") or [],
        "removed": e.get("removed") or [],
        "notes": e.get("notes", ""),
        "flags": e.get("flags") or [],
    }
    if e.get("segment_crops"):
        r["segment_crops"] = e["segment_crops"]
    if e.get("decimate_k", 1) > 1:
        r["decimate_k"] = e["decimate_k"]
    if e.get("dropped"):
        r["drop"] = True


    if len(r["keep"] or []) > 1 and not r["crop"] and not r.get("segment_crops"):
        print(f"  WARNING {e['video']}: {len(r['keep'])} keep intervals but no crop recorded — "
              f"if the output is not {e['src']['resolution']}, its per-segment crops were lost "
              f"(output is {e['out']['resolution']}); re-derive them before re-rendering")
    json.dump([r], open(f"{work}/recipes/{e['video']}.json", "w"), indent=1)
    n += 1
print(f"{cat}: wrote {n} recipes to {work}/recipes")
