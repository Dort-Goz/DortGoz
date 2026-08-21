#!/usr/bin/env python3
import json, os, re, subprocess, sys, glob

cat = sys.argv[1]
workroot = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/datasets/Dort_Goz/_clean_work")
work = f"{workroot}/{cat}"
vt = f"{work}/vt.py"
names = [os.path.basename(p)[:-5] for p in sorted(glob.glob(f"{work}/recipes/*.json"))]

def measure(n):
    out = subprocess.run(["python3", vt, "fpspad", n], capture_output=True, text=True).stdout
    m = re.search(r"PADDED k=(\d+)\s+container ([\d.]+) fps -> true ([\d.]+)", out)
    return (int(m.group(1)), float(m.group(3))) if m else None

from concurrent.futures import ThreadPoolExecutor
res = dict(zip(names, ThreadPoolExecutor(8).map(measure, names)))
padded = 0
for n, r in res.items():
    p = f"{work}/recipes/{n}.json"
    rs = json.load(open(p))
    for rec in rs:
        if r:
            k, true_fps = r
            rec["decimate_k"] = k
            rec["flags"] = sorted(set((rec.get("flags") or []) + ["fps_padded"]))
            note = (f" FRAME PADDING: the source holds every real frame for {k} container frames "
                    f"(true capture rate {true_fps:g} fps behind a 30 fps container). The duplicates are "
                    f"dropped in the output and the timestamps restamped at {true_fps:g} fps, so wall-clock "
                    f"timing is unchanged and motion measures become meaningful.")
            if "FRAME PADDING:" not in rec.get("notes", ""):
                rec["notes"] = (rec.get("notes", "") + note).strip()
            padded += 1
        else:
            rec.pop("decimate_k", None)
    json.dump(rs, open(p, "w"), indent=1)
print(f"{cat}: {padded}/{len(names)} clips marked for decimation")
