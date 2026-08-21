#!/usr/bin/env bash
set -euo pipefail

C="$1"
WORKROOT="${2:-$HOME/datasets/Dort_Goz/_clean_work}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WORK="$WORKROOT/$C"
VSRC="$HOME/datasets/Dort_Goz/UCF_Crimes/Videos/$C"
VOUT="$HOME/datasets/Dort_Goz/UCF_Crimes/Videos/${C}_new"

[ -d "$VSRC" ] || { echo "no such category: $VSRC" >&2; exit 1; }
mkdir -p "$WORK"/{recipes,review,check,win,det} "$VOUT"

sed -e "s#^WORK = \"PLACEHOLDER_WORK\"#WORK = \"$WORK\"#" \
    -e "s#^VSRC = \"PLACEHOLDER_VSRC\"#VSRC = \"$VSRC\"#" \
    -e "s#^VOUT = \"PLACEHOLDER_VOUT\"#VOUT = \"$VOUT\"#" \
    "$HERE/vt.py" > "$WORK/vt.py"
chmod +x "$WORK/vt.py"
grep -q PLACEHOLDER "$WORK/vt.py" && { echo "path substitution failed" >&2; exit 1; }

cd "$WORK"
ls "$VSRC"/*.mp4 | sed 's#.*/##;s#\.mp4##' > names.txt
N=$(wc -l < names.txt)
echo "[$C] $N clips -> $WORK"

xargs -a names.txt -P 8 -I{} python3 vt.py sheet {} >/dev/null 2>&1 || true
echo "[$C] review sheets: $(ls review | wc -l)/$N"

xargs -a names.txt -P 8 -I{} python3 vt.py detect {} >/dev/null 2>&1 || true
python3 - <<'PY'
import re, glob, os, json
out = []
for p in sorted(glob.glob('det/*.log')):
    b = os.path.basename(p)[:-4]
    t = open(p).read()
    m = re.findall(r'CROP crop=(\d+):(\d+):(\d+):(\d+)', t)
    iss = []
    if m:
        w, h, x, y = [int(v) for v in m[0]]
        if (w, h) != (320, 240):
            iss.append({"type": "pillarbox", "region": f"{x},{y},{w},{h}",
                        "start_s": 0, "end_s": 0, "desc": "cropdetect"})
    out.append({"video": b, "issues": iss})
json.dump(out, open('triage.json', 'w'), indent=1)
print(f"  seeded triage.json with {len(out)} crop hints")
PY

xargs -a names.txt -P 6 -I{} python3 vt.py loopmap {} > loopmap_all.txt 2>&1 || true
echo "[$C] loopmap: $(grep -c 'duplicated=' loopmap_all.txt)/$N"

xargs -a names.txt -P 5 -I{} python3 vt.py zoomsweep {} > zoomsweep_all.txt 2>&1 || true
echo "[$C] zoomsweep: $(grep -cE 'clean|candidate run' zoomsweep_all.txt)/$N"

python3 - <<'PY'
import subprocess, os, glob, json
V = os.path.dirname(os.path.realpath(glob.glob('det/*.log')[0])) if False else None
names = [l.strip() for l in open('names.txt')]
import re
VSRC = re.search(r'^VSRC = "(.*)"$', open('vt.py').read(), re.M).group(1)
d = {}
for b in names:
    d[b] = round(float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         f"{VSRC}/{b}.mp4"], capture_output=True, text=True).stdout or 0), 1)
json.dump(d, open("durations.json", "w"), indent=1)
G = 14
groups = [[] for _ in range(G)]
load = [0.0] * G
for k, v in sorted(d.items(), key=lambda kv: -kv[1]):
    i = load.index(min(load)); groups[i].append(k); load[i] += v
groups = [g for g in groups if g]
json.dump(groups, open("batches.json", "w"), indent=1)
print(f"  {len(d)} clips, {sum(d.values())/60:.1f} min, {len(groups)} batches")
PY

echo "[$C] PREP DONE"
