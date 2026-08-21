#!/usr/bin/env python3
import json, math, os, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

USAGE = """vt.py — UCF-Crime dataset cleaning toolkit.

One CLI for every step of the clean: inspect a clip, measure a boundary, prove a
replay, render a recipe, verify the result, build the manifest.

Paths come from three constants that prep.sh rewrites per category, so the copy
that lives in a workspace needs no environment at all.

    vt.py sheet     <NAME>                       16-tile overview (head/body/tail/median)
    vt.py win       <NAME> <START> <WINDOW> [TAG] 20-frame zoom sheet of a window
    vt.py crop      <NAME> [START] [DUR]          cropdetect over a region
    vt.py detect    <NAME>                        black / freeze / scene / crop log
    vt.py loopmap   <NAME> [crop]                 exact-replay map
    vt.py period    <NAME> [crop]                 exact loop period at 30 fps
    vt.py zoomsweep <NAME>                        zoom-replay candidates
    vt.py zoomproof <NAME> <crop|none> <tA> <tB> [...]   side-by-side zoom proof
    vt.py badge     <NAME> <x> <y> <w> <h> <ref_t>       on/off times of a fixed overlay
    vt.py occupancy <NAME> <x> <y> <w> <h> <t0> <t1>     how often people are inside a box
    vt.py render    <NAME>                        render recipes/<NAME>.json into VOUT
    vt.py clean     <NAME>                        render + build the output check sheet
    vt.py manifest                                manifest, report, remapped annotations
    vt.py qa                                      dark head/tail, flat and dark stretches
    vt.py dips                                    interior dip-to-black scan of the outputs
"""


WORK = "PLACEHOLDER_WORK"
VSRC = "PLACEHOLDER_VSRC"
VOUT = "PLACEHOLDER_VOUT"


ANN = os.path.expanduser("~/datasets/Dort_Goz/UCF_Crimes/Temporal_Anomaly_Annotation.txt")
CATEGORY = os.path.basename(VSRC)
FONT_PATH = "/usr/share/fonts/liberation/LiberationSans-Bold.ttf"


def _font(sz):
    try:
        return ImageFont.truetype(FONT_PATH, sz)
    except Exception:
        return ImageFont.load_default()


def src(name):
    return f"{VSRC}/{name}.mp4"


def out(name):
    return f"{VOUT}/{name}.mp4"


def ff(args, **kw):
    return subprocess.run(["ffmpeg", "-nostdin", "-v", "error"] + args,
                          capture_output=True, **kw)


def probe(path, entries="format=duration"):
    cmd = ["ffprobe", "-v", "error"]
    if entries.startswith("stream="):
        cmd += ["-select_streams", "v:0"]
    cmd += ["-show_entries", entries, "-of", "default=nw=1", path]
    r = subprocess.run(cmd, capture_output=True, text=True).stdout
    d = dict(l.split("=", 1) for l in r.strip().splitlines() if "=" in l)
    return {k: v for k, v in d.items() if v not in ("N/A", "")}


def duration(path):
    return float(probe(path).get("duration", 0) or 0)


def gray_stream(path, vf):
    p = ff(["-i", path, "-map", "0:v:0", "-vf", vf + ",format=gray", "-f", "rawvideo", "-"])
    return p.stdout


def frames_gray(path, w, h, fps, crop=None, ss=None, t=None):
    pre = []
    if ss is not None:
        pre += ["-ss", f"{ss:.3f}"]
    args = pre + ["-i", path]
    if t is not None:
        args += ["-t", f"{t:.3f}"]
    vf = (f"crop={crop}," if crop and crop != "none" else "") + f"fps={fps},scale={w}:{h},format=gray"
    p = ff(args + ["-map", "0:v:0", "-vf", vf, "-f", "rawvideo", "-"])
    a = np.frombuffer(p.stdout, np.uint8)
    n = a.size // (w * h)
    return a[:n * w * h].reshape(n, h, w).astype(np.float32)


def frame_rgb(path, t, w, h, vf_extra=""):
    vf = (vf_extra + "," if vf_extra else "") + f"scale={w}:{h}"
    p = ff(["-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-map", "0:v:0",
            "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    a = np.frombuffer(p.stdout, np.uint8)
    return a[:w * h * 3].reshape(h, w, 3) if a.size >= w * h * 3 else np.zeros((h, w, 3), np.uint8)


def cmd_sheet(name, vdir=None, sheetdir=None):
    vdir = vdir or VSRC
    sheetdir = sheetdir or f"{WORK}/review"
    os.makedirs(sheetdir, exist_ok=True)
    f = f"{vdir}/{name}.mp4"
    d = duration(f)
    W, H = 320, 240
    ts = [0.0, 0.35, 0.9, 1.8] + list(np.linspace(d * 0.10, d * 0.90, 8)) \
         + [max(0, d - 2.5), max(0, d - 1.2), max(0, d - 0.25)]
    tiles = [(f"t={t:.1f}", frame_rgb(f, t, W, H)) for t in ts]
    n = min(160, max(20, int(d)))
    A = frames_gray(f, W, H, n / max(d, 0.1))
    med = np.median(A, 0).astype(np.uint8)
    tiles.append(("MEDIAN(bg)", np.stack([med] * 3, -1)))
    sd = np.clip(A.std(0) * 4, 0, 255).astype(np.uint8)
    tiles.append(("STD(motion)", np.stack([sd] * 3, -1)))
    F = _font(14)
    pad, lab, cols, rows = 3, 18, 4, 4
    im = Image.new("RGB", (cols * (W + pad) + pad, rows * (H + lab + pad) + pad), (20, 20, 20))
    dr = ImageDraw.Draw(im)
    for i, (lb, arr) in enumerate(tiles[:16]):
        r, c = divmod(i, cols)
        x, y = pad + c * (W + pad), pad + r * (H + lab + pad)
        dr.text((x + 2, y + 1), f"{i+1:02d} {lb}", font=F, fill=(255, 220, 60))
        im.paste(Image.fromarray(arr), (x, y + lab))
    dr.text((im.width - 150, 3), f"{name} {d:.0f}s", font=F, fill=(120, 255, 120))
    p = f"{sheetdir}/{name}.jpg"
    im.save(p, quality=88)
    print(p)


def cmd_win(name, start, window, tag="w"):
    start, window = float(start), float(window)
    o = f"{WORK}/win"
    os.makedirs(o, exist_ok=True)
    r = 20.0 / max(window, 0.1)
    p = f"{o}/{name}_{tag}.jpg"
    ff(["-y", "-ss", f"{start:.3f}", "-t", f"{window:.3f}", "-i", src(name), "-map", "0:v:0",
        "-vf", (f"fps={r},scale=480:360:flags=neighbor,"
                f"drawtext=fontfile={FONT_PATH}:text='%{{pts\\:hms}}+{start}':x=4:y=4:"
                f"fontsize=20:fontcolor=yellow:box=1:boxcolor=black@0.7,"
                f"tile=5x4:margin=2:padding=2"),
        "-frames:v", "1", "-q:v", "3", p])
    print(p)


def cmd_crop(name, start=None, dur=None):
    args = []
    if start is not None:
        args += ["-ss", str(start)]
    args += ["-i", src(name)]
    if dur is not None:
        args += ["-t", str(dur)]
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "info"] + args + ["-map", "0:v:0",
                        "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
                       capture_output=True, text=True)
    hits = [l.split("crop=")[1].split()[0] for l in p.stderr.splitlines() if "crop=" in l]
    print("crop=" + hits[-1] if hits else "crop=none")


def cmd_detect(name):
    os.makedirs(f"{WORK}/det", exist_ok=True)
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "info", "-i", src(name), "-map", "0:v:0",
                        "-vf", ("blackdetect=d=0.15:pix_th=0.10,freezedetect=n=-55dB:d=1.0,"
                                "scdet=threshold=8,cropdetect=limit=24:round=2:reset=0"),
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    e = p.stderr
    import re
    lines = [f"== {name}"]
    lines += ["BLACK " + m for m in re.findall(r'black_start:[\d.]+ black_end:[\d.]+ black_duration:[\d.]+', e)]
    lines += ["FREEZE " + a + " " + b for a, b in re.findall(r'freeze_(start|duration|end): *([-\d.]+)', e)]
    lines += ["SCENE " + m for m in re.findall(r'lavfi\.scd\.time: [\d.]+', e)]
    cr = re.findall(r'crop=[\d:]+', e)
    if cr:
        lines.append("CROP " + cr[-1])
    open(f"{WORK}/det/{name}.log", "w").write("\n".join(lines) + "\n")
    print(f"{WORK}/det/{name}.log")


def _crop_from_triage(name):
    try:
        tri = {v["video"]: v for v in json.load(open(f"{WORK}/triage.json"))}
    except Exception:
        return None
    for i in tri.get(name, {}).get("issues", []):
        if i.get("type") == "pillarbox" and i.get("region"):
            try:
                x, y, w, h = [int(float(t)) for t in i["region"].split(",")]
            except Exception:
                continue
            if w > 40 and h > 40:
                return f"{w-w%2}:{h-h%2}:{x}:{y}"
    return None


def cmd_loopmap(name, crop=None):
    FPS, W, H = 10, 132, 90
    crop = crop or _crop_from_triage(name)
    X = frames_gray(src(name), W, H, FPS, crop).reshape(-1, W * H)
    n = len(X)
    if n < 40:
        print(f"{name}: too short")
        return
    adj = np.append(np.abs(np.diff(X, axis=0)).mean(1), 0)
    adj[-1] = adj[-2] if n > 1 else 0
    k = FPS
    loc = np.array([np.median(adj[max(0, i - k):i + k + 1]) for i in range(n)])
    MIN = int(2 * FPS)
    step = 1 if n < 3000 else 3
    bd = np.full(n, 1e9); bl = np.zeros(n)
    idx = list(range(MIN, n, step))
    for i in idx:
        d = np.abs(X[:i - MIN + 1] - X[i]).mean(1)
        j = int(d.argmin()); bd[i] = d[j]; bl[i] = (i - j) / FPS
    dup = {i: bd[i] <= max(0.6, loc[i] * 1.2) for i in idx}
    runs, cur = [], None
    for i in idx:
        if dup[i]:
            if cur is None:
                cur = [i, i, bl[i]]
            elif abs(bl[i] - cur[2]) < 0.8:
                cur[1] = i
            else:
                runs.append(cur); cur = [i, i, bl[i]]
        else:
            if cur: runs.append(cur)
            cur = None
    if cur: runs.append(cur)
    runs = [r for r in runs if (r[1] - r[0]) / FPS >= 2.0]
    tot = sum((e - s) / FPS for s, e, _ in runs)
    print(f"{name}: {n/FPS:.1f}s  crop={crop or 'none'}  median frame-diff={np.median(adj):.2f}  duplicated={tot:.1f}s")
    for s, e, lag in runs:
        print(f"    t={s/FPS:6.1f}-{e/FPS:6.1f}s  repeats source t={s/FPS-lag:6.1f}s  (lag {lag:.1f}s)")
    for lag in sorted({round(r[2], 1) for r in runs}):
        L = int(round(lag * FPS))
        if L < MIN or L >= n:
            continue
        i = np.arange(L, n)
        best = np.minimum.reduce([np.abs(X[i - L + o] - X[i]).mean(1) for o in (-1, 0, 1) if 0 <= L - o < n])
        hit = best <= np.maximum(0.6, loc[i] * 1.2)
        print(f"    lag {lag:.1f}s over the whole file: {hit.mean()*100:.0f}% of frames from t={L/FPS:.1f}s repeat what came {lag:.1f}s earlier")


def cmd_period(name, crop=None):
    FPS, W, H = 30, 160, 90
    X = frames_gray(src(name), W, H, FPS, crop).reshape(-1, W * H)
    n = len(X)
    adj = float(np.median(np.abs(np.diff(X, axis=0)).mean(1)))
    res = sorted((float(np.abs(X[L:] - X[:-L]).mean()), L) for L in range(int(4 * FPS), n - int(4 * FPS)))
    base = float(np.median([r[0] for r in res])) if res else 1.0
    print(f"{name}: {n/FPS:.2f}s  adjacent-frame diff {adj:.2f}  typical far-apart diff {base:.2f}")
    for m, L in res[:8]:
        print(f"    period {L:5d} frames = {L/FPS:7.3f}s   mean diff {m:.2f}   ({m/base*100:.0f}% of typical)")


GW, GH, DW, DH = 320, 180, 40, 24
SCALES = (0.72, 0.58, 0.46, 0.36)


def _desc(img):
    ys = (np.arange(DH + 1) * img.shape[0] // DH)
    xs = (np.arange(DW + 1) * img.shape[1] // DW)
    v = np.add.reduceat(np.add.reduceat(img, ys[:-1], axis=0), xs[:-1], axis=1)
    v = v / np.outer(np.diff(ys), np.diff(xs))
    v = v - v.mean()
    s = v.std()
    return (v / (s if s > 1e-3 else 1)).ravel()


def cmd_zoomsweep(name):
    MAX_SAMPLES, OFF = 300, 5
    crop = _crop_from_triage(name)
    d = duration(src(name))
    fps = min(2.0, MAX_SAMPLES / max(d, 1))
    F = frames_gray(src(name), GW, GH, fps, crop)
    n = len(F)
    if n < 8:
        print(f"{name}: too short")
        return
    times = np.arange(n) / fps
    full = np.stack([_desc(f) for f in F])
    crops, owner, scal = [], [], []
    for j, f in enumerate(F):
        for s in SCALES:
            w, h = int(GW * s), int(GH * s)
            for oy in np.linspace(0, GH - h, OFF).astype(int):
                for ox in np.linspace(0, GW - w, OFF).astype(int):
                    crops.append(_desc(f[oy:oy + h, ox:ox + w])); owner.append(j); scal.append(s)
    C = np.stack(crops); owner = np.array(owner); scal = np.array(scal)
    M = full @ C.T / (DW * DH)
    far = np.abs(times[:, None] - times[owner][None, :]) >= 3.0
    M = np.where(far, M, -9)
    best = M.max(1); arg = M.argmax(1)
    med = np.nanmedian(np.where(far, M, np.nan), axis=1)
    spec = best - med
    mb = float(np.median(best)); mad = float(np.median(np.abs(best - mb))) or 0.01
    thr = max(0.62, mb + 2.5 * mad)
    flag = (spec > 0.25) & ((best > 0.80) | (best > thr))
    runs, cur = [], None
    for i in range(n):
        if flag[i]:
            cur = [i, i] if cur is None else [cur[0], i]
        else:
            if cur and times[cur[1]] - times[cur[0]] >= 3.0:
                runs.append(tuple(cur))
            cur = None
    if cur and times[cur[1]] - times[cur[0]] >= 3.0:
        runs.append(tuple(cur))
    if runs:
        print(f"{name} ({d:.0f}s, {n} samples, crop={crop or 'none'}, baseline {mb:.2f}, threshold {thr:.2f}): {len(runs)} candidate run(s)")
        for a, b in runs:
            print(f"    t={times[a]:6.1f}-{times[b]:6.1f}s  is a ~x{1/np.mean(scal[arg[a:b+1]]):.1f} zoom of "
                  f"t={times[owner[arg[a]]]:.1f}-{times[owner[arg[b]]]:.1f}s   mean corr {best[a:b+1].mean():.2f}")
    else:
        print(f"{name} ({d:.0f}s): clean  (baseline {mb:.2f}, threshold {thr:.2f})")


def cmd_zoomproof(name, crop, *ts):
    pairs = [(float(ts[i]), float(ts[i + 1])) for i in range(0, len(ts) - 1, 2)]
    path = src(name) if os.path.exists(src(name)) else out(name)

    def g(t, extra=""):
        vf = (f"crop={crop}," if crop and crop != "none" else "") + f"scale={GW}:{GH}"
        if extra:
            vf += f",{extra},scale={GW}:{GH}"
        p = ff(["-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-map", "0:v:0",
                "-vf", vf + ",format=gray", "-f", "rawvideo", "-"])
        a = np.frombuffer(p.stdout, np.uint8)
        return a[:GW * GH].reshape(GH, GW).astype(np.float32) if a.size >= GW * GH else np.zeros((GH, GW), np.float32)

    rows = []
    for tA, tB in pairs:
        A, B = g(tA), g(tB)
        da = _desc(A); best = (-9, 1.0, 0, 0)
        for s in np.arange(0.30, 1.01, 0.04):
            w, h = int(GW * s), int(GH * s)
            for oy in range(0, GH - h + 1, max(1, (GH - h) // 8 or 1)):
                for ox in range(0, GW - w + 1, max(1, (GW - w) // 8 or 1)):
                    v = float(da @ _desc(B[oy:oy + h, ox:ox + w]) / (DW * DH))
                    if v > best[0]:
                        best = (v, s, ox, oy)
        v, s, ox, oy = best
        rows.append((tA, tB, v, s, ox, oy))
        print(f"  t={tA:.2f}s vs t={tB:.2f}s : best corr {v:.3f} at scale {s:.2f} (x{1/s:.2f} zoom), offset ({ox},{oy})")
    F = _font(15); lab = 20
    im = Image.new("RGB", (2 * (GW + 4) + 4, len(rows) * (GH + lab + 4) + 4), (20, 20, 20))
    dr = ImageDraw.Draw(im)
    for r, (tA, tB, v, s, ox, oy) in enumerate(rows):
        y = 4 + r * (GH + lab + 4)
        w, h = int(GW * s), int(GH * s)
        pre = (f"crop={crop}," if crop and crop != "none" else "") + f"scale={GW}:{GH}"
        dr.text((6, y + 2), f"t={tA:.2f}s (as shown)", font=F, fill=(255, 220, 60))
        dr.text((GW + 10, y + 2), f"t={tB:.2f}s, x{1/s:.2f} crop  corr {v:.2f}", font=F, fill=(120, 255, 120))
        im.paste(Image.fromarray(frame_rgb(path, tA, GW, GH, pre)), (4, y + lab))
        im.paste(Image.fromarray(frame_rgb(path, tB, GW, GH, f"{pre},crop={w}:{h}:{ox}:{oy}")), (GW + 8, y + lab))
    p = f"{WORK}/proof_{name}.jpg"
    im.save(p, quality=92)
    print(p)


def cmd_badge(name, x, y, w, h, ref_t, fps=4.0):
    x, y, w, h, ref_t, fps = int(x), int(y), int(w), int(h), float(ref_t), float(fps)
    p = ff(["-i", src(name), "-map", "0:v:0",
            "-vf", f"crop={w}:{h}:{x}:{y},fps={fps},format=gray", "-f", "rawvideo", "-"])
    a = np.frombuffer(p.stdout, np.uint8)
    n = a.size // (w * h)
    X = a[:n * w * h].reshape(n, -1).astype(np.float32)
    Xn = (X - X.mean(1, keepdims=True)) / (X.std(1, keepdims=True) + 1e-3)
    ref = Xn[min(int(ref_t * fps), n - 1)]
    c = Xn @ ref / (w * h)
    on = c > 0.75
    runs, s = [], None
    for i, v in enumerate(list(on) + [False]):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if (i - s) / fps >= 0.5:
                runs.append((s / fps, (i - 1) / fps))
            s = None
    print(f"{name} region {w}x{h}@({x},{y}) ref t={ref_t}s : overlay present "
          f"{sum(b-a for a, b in runs):.1f}s of {n/fps:.1f}s")
    for a_, b_ in runs:
        print(f"    {a_:7.1f}-{b_:7.1f}s")


def cmd_occupancy(name, x, y, w, h, t0, t1, fps=10.0):
    x, y, w, h, t0, t1 = int(x), int(y), int(w), int(h), float(t0), float(t1)
    F = frames_gray(src(name), 320, 240, fps, None, ss=t0, t=t1 - t0)
    ref = np.median(F, axis=0)
    d = np.abs(F[:, y:y + h, x:x + w] - ref[y:y + h, x:x + w])
    frac = (d > 45).reshape(len(F), -1).mean(1)
    print(f"{name} box {w}x{h}@({x},{y}) over {t0}-{t1}s: people occupancy "
          f"max {frac.max()*100:.1f}%  mean {frac.mean()*100:.1f}%  "
          f"frames>10%: {(frac>0.10).sum()}/{len(F)}")
    print("  VERDICT: " + ("safe to mask" if (frac > 0.10).mean() < 0.15 else
                           "DO NOT MASK — people are in the box too often; flag overlay_kept"))


def build_filter(r, cfps=30.0):
    keep = r.get("keep") or []
    segc = r.get("segment_crops")
    k = int(r.get("decimate_k") or 1)
    ofps = cfps / k if k > 1 else cfps
    pre = []
    for m in r.get("masks") or []:
        f = f"delogo=x={m['x']}:y={m['y']}:w={m['w']}:h={m['h']}"
        if m.get("from") is not None or m.get("to") is not None:
            a = m.get("from") or 0
            b = m.get("to") if m.get("to") is not None else 1e6
            f += f":enable='between(t,{a:.3f},{b:.3f})'"
        pre.append(f)

    if not segc or len({c for c in segc if c}) <= 1:
        parts = list(pre)
        if keep:
            parts.append("select='" + "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep) + "'")
        if k > 1:
            parts.append(f"select='not(mod(n\\,{k}))'")
        if keep or k > 1:
            parts.append(f"setpts=N/{ofps:.6f}/TB")
        if r.get("crop"):
            parts.append(f"crop={r['crop']}")
        parts.append("format=yuv420p")
        return ",".join(parts), False

    boxes = [c or r.get("crop") for c in segc]
    dims = [tuple(int(v) for v in c.split(":")[:2]) if c else (320, 240) for c in boxes]
    W = max(w for w, _ in dims) + (max(w for w, _ in dims) % 2)
    H = max(h for _, h in dims) + (max(h for _, h in dims) % 2)
    pre_s = ",".join(pre)
    g = [(f"[0:v]{pre_s}," if pre_s else "[0:v]") + f"split={len(keep)}" + "".join(f"[v{i}]" for i in range(len(keep)))]
    for i, ((a, b), c) in enumerate(zip(keep, boxes)):
        ch = f"[v{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS"
        if c:
            ch += f",crop={c}"
        w, h = (int(v) for v in c.split(":")[:2]) if c else (320, 240)
        if (w, h) != (W, H):
            ch += f",pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"
        g.append(ch + f",format=yuv420p[s{i}]")
    tail = "".join(f"[s{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=0"
    if k > 1:
        tail += f",select='not(mod(n\\,{k}))',setpts=N/{ofps:.6f}/TB"
    g.append(tail + "[out]")
    return ";".join(g), True


def cmd_render(name, crf=18, preset="medium"):
    rs = json.load(open(f"{WORK}/recipes/{name}.json"))
    r = next(x for x in rs if x["video"] == name)
    if r.get("drop"):
        print(f"{name}: drop=true, no output written")
        return
    os.makedirs(VOUT, exist_ok=True)
    sp = probe(src(r.get("src", name)), "stream=avg_frame_rate")
    try:
        n_, d_ = sp.get("avg_frame_rate", "30/1").split("/")
        cfps = float(n_) / float(d_) if float(d_) else 30.0
    except Exception:
        cfps = 30.0
    fl, complex_ = build_filter(r, cfps)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src(r.get("src", name))]
    cmd += (["-filter_complex", fl, "-map", "[out]"] if complex_ else ["-map", "0:v:0", "-vf", fl])

    cmd += ["-an", "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
            "-fps_mode", "passthrough", "-movflags", "+faststart", out(name)]
    subprocess.run(cmd, check=True)
    print(out(name))


def cmd_clean(name):
    cmd_render(name)
    if os.path.exists(out(name)):
        cmd_sheet(name, vdir=VOUT, sheetdir=f"{WORK}/check")
        s, o = duration(src(name)), duration(out(name))
        d = probe(out(name), "stream=width,height")
        print(f"{name}: src {s:.2f}s -> out {o:.2f}s  {d.get('width')}x{d.get('height')}")


def kept_frames(keep, nsrc=None, fps=30.0):
    r = []
    for a, b in keep:
        f0 = int(math.ceil(a * fps - 1e-6))
        f1 = int(math.floor(b * fps + 1e-6))
        if nsrc:
            f1 = min(f1, nsrc - 1)
        if f1 >= f0:
            r.append((f0, f1))
    return r


def map_frame(f, ranges):
    n = 0
    for f0, f1 in ranges:
        if f < f0:
            return None
        if f <= f1:
            return n + (f - f0)
        n += f1 - f0 + 1
    return None


def nearest_kept(f, ranges):
    m = map_frame(f, ranges)
    if m is not None:
        return m, 0
    best = None
    for f0, f1 in ranges:
        for cand in (f0, f1):
            if best is None or abs(cand - f) < abs(best - f):
                best = cand
    return (map_frame(best, ranges), best - f) if best is not None else (None, None)


def cmd_manifest():
    import glob
    recipes = {}
    for p in sorted(glob.glob(f"{WORK}/recipes/*.json")):
        for r in json.load(open(p)):
            recipes[r["video"]] = r
    entries = []
    for name in sorted(recipes):
        r = recipes[name]
        ps = probe(src(r.get("src", name)), "stream=width,height,nb_frames,avg_frame_rate")
        ps.update(probe(src(r.get("src", name))))
        po = {}
        if os.path.exists(out(name)):
            po = probe(out(name), "stream=width,height,nb_frames")
            po.update(probe(out(name)))
        nsrc = int(ps.get("nb_frames") or 0)
        try:
            n_, d_ = ps.get("avg_frame_rate", "30/1").split("/")
            sfps = float(n_) / float(d_) if float(d_) else 30.0
        except Exception:
            sfps = 30.0
        ranges = kept_frames(r.get("keep") or [[0, float(ps.get("duration", 0) or 0)]], nsrc, sfps)
        k = int(r.get("decimate_k") or 1)
        kept_n = sum(f1 - f0 + 1 for f0, f1 in ranges)
        expected = math.ceil(kept_n / k) if k > 1 else kept_n
        actual = int(po.get("nb_frames") or 0)


        slack = (len(ranges) if r.get("segment_crops") else 0) + (len(ranges) if k > 1 else 0)
        entries.append({
            "video": name, "dropped": bool(r.get("drop")),
            "src": {"duration_s": round(float(ps.get("duration", 0) or 0), 3),
                    "resolution": f"{ps.get('width')}x{ps.get('height')}", "frames": nsrc,
                    "fps": round(sfps, 4)},
            "out": {"duration_s": round(float(po.get("duration", 0) or 0), 3),
                    "resolution": f"{po.get('width')}x{po.get('height')}", "frames": actual},
            "keep": r.get("keep"), "kept_frame_ranges": ranges,
            "frame_count_matches": abs(expected - actual) <= slack,
            "crop": r.get("crop"), "segment_crops": r.get("segment_crops"),
            "decimate_k": k, "out_true_fps": round(sfps / k, 3),
            "masks": r.get("masks") or [], "removed": r.get("removed") or [],
            "flags": r.get("flags") or [], "notes": r.get("notes", ""),
        })
    os.makedirs(VOUT, exist_ok=True)
    json.dump(entries, open(f"{VOUT}/_manifest.json", "w"), indent=1)

    ann_out = []
    if os.path.exists(ANN):
        for line in open(ANN):
            parts = line.split()
            if not parts:
                continue
            vid = parts[0].replace(".mp4", "")
            if vid not in recipes:
                continue
            e = next(x for x in entries if x["video"] == vid)
            nums, moved_any = [], False
            for v in parts[2:6]:
                iv = int(v)
                if iv < 0:
                    nums.append(-1); continue
                m, moved = nearest_kept(iv, e["kept_frame_ranges"])
                if m is not None and e["decimate_k"] > 1:
                    m = m // e["decimate_k"]
                nums.append(-1 if m is None else m)
                moved_any = moved_any or bool(moved)
            ann_out.append({"video": vid, "label": parts[1],
                            "src": [int(v) for v in parts[2:6]], "out": nums, "clamped": moved_any})
        if ann_out:
            with open(f"{VOUT}/_Temporal_Anomaly_Annotation_{CATEGORY}.txt", "w") as fh:
                for a in ann_out:
                    fh.write(f"{a['video']}.mp4  {a['label']}  " + "  ".join(str(x) for x in a["out"]) + "\n")
            json.dump(ann_out, open(f"{VOUT}/_annotation_remap.json", "w"), indent=1)

    live = [e for e in entries if not e["dropped"]]
    ts = sum(e["src"]["duration_s"] for e in entries)
    to = sum(e["out"]["duration_s"] for e in entries)
    bad = [e["video"] for e in live if not e["frame_count_matches"]]
    L = [f"# {CATEGORY}_new — cleaning report\n",
         f"{len(live)} clips written ({len(entries)-len(live)} dropped). "
         f"Total {ts/60:.1f} min -> {to/60:.1f} min ({ts-to:.0f} s removed).\n",
         f"Frame-count check: {len(live)-len(bad)}/{len(live)} exact"
         + (f" — mismatched: {', '.join(bad)}" if bad else "") + "\n",
         "\n## Per clip\n",
         "| clip | src | out | crop | masks | removed | flags |", "|---|---|---|---|---|---|---|"]
    for e in entries:
        rem = "; ".join(f"{r['start_s']:.1f}-{r['end_s']:.1f}s {r['why']}" for r in e["removed"]) or "-"
        mk = "; ".join(m["why"][:60] for m in e["masks"]) or "-"
        cr = e["crop"] or ("per-segment" if e["segment_crops"] else "-")
        if e["decimate_k"] > 1:
            cr += f" / decim x{e['decimate_k']}"
        outcol = "DROPPED" if e["dropped"] else f"{e['out']['duration_s']:.1f}s {e['out']['resolution']}"
        L.append(f"| {e['video']} | {e['src']['duration_s']:.1f}s {e['src']['resolution']} "
                 f"| {outcol} | {cr} | {mk} | {rem} | {', '.join(e['flags']) or '-'} |")
    if ann_out:
        L += ["\n## Temporal annotations, remapped\n",
              "| clip | source frames | new frames | clamped onto a cut edge |", "|---|---|---|---|"]
        for a in ann_out:
            L.append(f"| {a['video']} | {a['src']} | {a['out']} | {'yes' if a['clamped'] else 'no'} |")
    open(f"{VOUT}/_rapor.md", "w").write("\n".join(L) + "\n")
    print(f"{len(live)} clips  {ts/60:.1f} min -> {to/60:.1f} min")
    print(f"frame-count exact: {len(live)-len(bad)}/{len(live)}" + (f"  BAD: {bad}" if bad else ""))
    print(f"wrote {VOUT}/_manifest.json, _rapor.md" + (f", _Temporal_Anomaly_Annotation_{CATEGORY}.txt" if ann_out else ""))


def cmd_qa():
    import glob
    print(f"{'clip':<28}{'dur':>8}{'res':>10}{'headL':>7}{'tailL':>7}{'dark_s':>8}{'flat_s':>8}  notes")
    bad = []
    for f in sorted(glob.glob(f"{VOUT}/*.mp4")):
        b = os.path.basename(f)[:-4]
        d = duration(f)
        p = probe(f, "stream=width,height")
        w, h = int(p.get("width", 0)), int(p.get("height", 0))
        A = frames_gray(f, 48, 36, 4).reshape(-1, 48 * 36)
        lum, sd = A.mean(1), A.std(1)
        dark, flat = (lum < 20).sum() / 4.0, (sd < 6).sum() / 4.0
        n = []
        if w % 2 or h % 2: n.append("ODD_DIMS")
        if lum[0] < 25: n.append("DARK_FIRST_FRAME")
        if lum[-1] < 25: n.append("DARK_LAST_FRAME")
        if dark > 1.0: n.append(f"DARK_{dark:.1f}s")
        if flat > 2.0: n.append(f"FLAT_{flat:.1f}s")
        if n: bad.append((b, n))
        print(f"{b:<28}{d:>7.1f}s{f'{w},{h}':>10}{lum[0]:>7.0f}{lum[-1]:>7.0f}{dark:>8.1f}{flat:>8.1f}  {' '.join(n)}")
    print("\nflagged:", bad or "none")


def cmd_dips():
    import glob
    found = False
    for f in sorted(glob.glob(f"{VOUT}/*.mp4")):
        b = os.path.basename(f)[:-4]
        A = frames_gray(f, 48, 36, 10).reshape(-1, 48 * 36)
        lum = A.mean(1); n = len(lum)
        med = float(np.median(lum))
        m = lum < med * 0.35
        runs, s = [], None
        for i, v in enumerate(list(m) + [False]):
            if v and s is None: s = i
            elif not v and s is not None:
                if i - s >= 3: runs.append((s / 10.0, (i - 1) / 10.0))
                s = None
        runs = [r for r in runs if r[0] > 0.4 and r[1] < n / 10.0 - 0.4]
        if runs:
            found = True
            print(f"{b} (median lum {med:.0f}, {n/10:.1f}s): " + ", ".join(f"{a:.1f}-{c:.1f}s" for a, c in runs))
    if not found:
        print("no interior dip-to-black in any output")


def cmd_fpspad(name, path=None):
    path = path or src(name)
    fps_s = probe(path, "stream=avg_frame_rate").get("avg_frame_rate", "30/1")
    n_, d_ = fps_s.split("/")
    cfps = float(n_) / float(d_) if float(d_) else 30.0
    d = duration(path)

    diffs = []
    for frac in (0.25, 0.5, 0.75):
        ss = max(0.0, d * frac - 5)
        A = frames_gray(path, 160, 120, cfps, None, ss=ss, t=min(10.0, d)).reshape(-1, 160 * 120)
        if len(A) > 3:
            diffs.append(np.abs(np.diff(A, axis=0)).mean(1))
    if not diffs:
        print(f"{name}: too short"); return
    best = None
    for k in (2, 3, 4, 5, 6):
        ratios = []
        for dv in diffs:
            m = [dv[i::k].mean() for i in range(k) if len(dv[i::k])]
            if len(m) < k or max(m) <= 1e-6:
                continue
            ratios.append(max(m) / max(min(m), 1e-6))
        if len(ratios) == len(diffs) and min(ratios) > 8.0:
            best = (k, min(ratios)); break
    if best:
        k, r = best
        print(f"{name}: PADDED k={k}  container {cfps:.2f} fps -> true {cfps/k:.2f} fps  "
              f"(quietest residue is {r:.0f}x below the loudest, in all 3 windows)")
    else:
        print(f"{name}: not padded  (container {cfps:.2f} fps)")


CMDS = {"sheet": cmd_sheet, "win": cmd_win, "crop": cmd_crop, "detect": cmd_detect,
        "fpspad": cmd_fpspad,
        "loopmap": cmd_loopmap, "period": cmd_period, "zoomsweep": cmd_zoomsweep,
        "zoomproof": cmd_zoomproof, "badge": cmd_badge, "occupancy": cmd_occupancy,
        "render": cmd_render, "clean": cmd_clean, "manifest": cmd_manifest,
        "qa": cmd_qa, "dips": cmd_dips}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(USAGE)
        sys.exit(1)
    CMDS[sys.argv[1]](*sys.argv[2:])
