from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from dortgoz.config import settings
from dortgoz.pipeline import ingest, windowing
from dortgoz.pipeline.interpret import glance_window, interpret_window

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_long_feed import resolve_ucf  # noqa: E402  (veri seti yolu tek konvansiyondan)

RESULTS = Path(__file__).parent / "results"
THRESHOLDS = [0.01, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 0.90]


def clip_class(path: Path) -> str:
    return re.sub(r"_?\d+$", "", path.stem.replace("_x264", ""))


SYSTEM_OVERRIDE = ""
TIER_OVERRIDE = ""

ESCALATE_TAU = 0.0

PARALLEL = 1


async def measure_clip(path: Path) -> dict:
    duration = await ingest.probe_duration(path)
    profile = await ingest.motion_profile(path, settings.base_fps)
    out = {
        "clip": path.name,
        "class": clip_class(path),
        "anomaly": not clip_class(path).startswith("Normal"),
        "duration": duration,
        "windows": [],
    }
    plan: list[tuple[float, float, float, list[float] | None]] = []
    for start, end in windowing.windows(duration, settings.window_seconds):
        peak = windowing.window_motion(profile, start, end)
        keys = (None if peak < settings.motion_gate else
                windowing.select_keyframes(profile, start, end, settings.keyframes_per_window))
        plan.append((start, end, peak, keys))
    live_keys = [k for (_, _, _, k) in plan if k]

    live_idx = 0
    for start, end, peak, keys in plan:
        rec = {"start": start, "end": end, "motion": peak, "gated": keys is None}
        if not rec["gated"]:
            ingest.prefetch_frames(path, keys)
            if live_idx + 1 < len(live_keys):
                ingest.prefetch_frames(path, live_keys[live_idx + 1])
            live_idx += 1

            t0 = time.time()
            rec["glance_p"] = await glance_window(path, (start, end), keys[:2])
            rec["glance_s"] = time.time() - t0

            t0 = time.time()
            stats: dict = {}
            report = await interpret_window(path, (start, end), keys,
                                            system_prompt=SYSTEM_OVERRIDE,
                                            tier_prompt=TIER_OVERRIDE,
                                            stats=stats)
            rec["deep_s"] = time.time() - t0
            rec["n_events"] = len(report.events)
            rec["summary"] = report.summary
            rec["events"] = [e.model_dump() for e in report.events]
            if "durum_p" in stats:
                rec["durum_p"] = round(stats["durum_p"], 5)

            if (ESCALATE_TAU and not rec["n_events"]
                    and stats.get("durum_p", 0.0) >= ESCALATE_TAU):
                t0 = time.time()
                try:
                    esc = await interpret_window(path, (start, end), keys,
                                                 system_prompt=SYSTEM_OVERRIDE,
                                                 tier_prompt=TIER_OVERRIDE,
                                                 think=True)
                    rec["esc_summary"] = esc.summary
                    rec["esc_events"] = [e.model_dump() for e in esc.events]
                except Exception as exc:
                    rec["esc_error"] = str(exc)[:200]
                rec["esc_s"] = time.time() - t0
        out["windows"].append(rec)
    return out


def current_config() -> dict:
    return {
        "model": settings.main_model,
        "window_seconds": settings.window_seconds,
        "keyframes_per_window": settings.keyframes_per_window,
        "motion_gate": settings.motion_gate,
        "system_prompt_override": SYSTEM_OVERRIDE,
        "tier_prompt_override": TIER_OVERRIDE,
        "parallel": PARALLEL,
        "escalate_tau": ESCALATE_TAU,
        "interpret_effort": settings.interpret_effort,
        "second_opinion_model": settings.second_opinion_model,
        "second_opinion_effort": settings.second_opinion_effort,
        "second_opinion_motion": settings.second_opinion_motion,
    }


def load_results(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    data: dict = {"clips": [], "config": {}}
    for line in text.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if "clip" in obj:
            data["clips"].append(obj)
        else:
            data["config"] = obj.get("config", {})
    return data


async def collect(clips: list[Path], out: Path) -> None:
    done: set[str] = set()
    if out.exists():
        prev = load_results(out)
        if prev["config"] and prev["config"]["model"] != settings.main_model:
            raise SystemExit(
                f"{out} kaydı {prev['config']['model']} ile başlamış, etkin model "
                f"{settings.main_model} — aynı dosyaya karıştırılmaz (yeni --out ver)")
        if (prev["config"].get("system_prompt_override", "") != SYSTEM_OVERRIDE
                or prev["config"].get("tier_prompt_override", "") != TIER_OVERRIDE):
            raise SystemExit(
                f"{out} kaydı farklı bir istem varyantıyla başlamış — "
                "aynı dosyaya karıştırılmaz (yeni --out ver)")
        if prev["config"].get("parallel", 1) != PARALLEL:
            raise SystemExit(
                f"{out} kaydı parallel={prev['config'].get('parallel', 1)} ile "
                "başlamış — zaman ölçüm tabanı karışmaz (yeni --out ver)")
        for alan in ("interpret_effort", "second_opinion_model",
                     "second_opinion_effort"):
            onceki = prev["config"].get(alan, "")
            simdi = getattr(settings, alan)
            if onceki != simdi:
                raise SystemExit(
                    f"{out} kaydı {alan}={onceki!r} ile başlamış, etkin değer "
                    f"{simdi!r} — aynı dosyaya karıştırılmaz (yeni --out ver)")
        done = {c["clip"] for c in prev["clips"] if "error" not in c}
        if done:
            print(f"devam: {len(done)} klip önceki koşudan tamam, atlanıyor")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"config": current_config()},
                                  ensure_ascii=False) + "\n", encoding="utf-8")

    t_start = time.time()
    todo = [p for p in clips if p.name not in done]
    sem = asyncio.Semaphore(PARALLEL)
    write_lock = asyncio.Lock()
    finished = 0

    async def one(path: Path) -> None:
        nonlocal finished
        async with sem:
            try:
                rec = await measure_clip(path)
                live = [w for w in rec["windows"] if not w["gated"]]
                note = (f"{len(rec['windows'])} pencere, "
                        f"{sum(w.get('n_events', 0) for w in live)} olay, "
                        f"p(YES)={[round(w['glance_p'], 2) for w in live]}")
            except Exception as exc:
                rec, note = {"clip": path.name, "error": str(exc)}, f"HATA: {exc}"
            async with write_lock:
                finished += 1
                print(f"[{finished}/{len(todo)}] {path.name} ... {note}"
                      f"  [geçen {time.time() - t_start:.0f} sn]", flush=True)
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    await asyncio.gather(*(one(p) for p in todo))


SEVERITY = ["dusuk", "orta", "yuksek", "kritik"]
ALARM_FLOOR = 1


def _live_windows(data: dict) -> list[tuple[dict, dict]]:
    return [(c, w) for c in data["clips"] if "windows" in c
            for w in c["windows"] if not w["gated"]]


def window_severity(w: dict) -> int:
    return max((SEVERITY.index(e["severity_hint"]) for e in w.get("events", [])),
               default=-1)


def analyze(data: dict) -> str:
    clips = [c for c in data["clips"] if "windows" in c]
    anomaly = [c for c in clips if c["anomaly"]]
    normal = [c for c in clips if not c["anomaly"]]
    footage = sum(c["duration"] for c in clips) / 60.0
    live = _live_windows(data)

    def detected(clip: dict, thr: float | None) -> bool:
        return any(not w["gated"] and window_severity(w) >= ALARM_FLOOR
                   and (thr is None or w["glance_p"] >= thr)
                   for w in clip["windows"])

    lines = [
        f"# A2 iki kollu kıyaslama",
        "",
        *(["- ⚠ parallel>1 koşusu: GPU-sn sütunları duvar saatidir, sunucu "
           "kuyruğu beklemesi içerir — birim maliyet için parallel=1 koşusu esas"]
          if data["config"].get("parallel", 1) > 1 else []),
        f"- Klip: {len(clips)} ({len(anomaly)} anomali, {len(normal)} normal)",
        f"- Görüntü süresi: {footage:.1f} dk · işlenen pencere: {len(live)}"
        f" (eleme sonrası; {sum(len(c['windows']) for c in clips) - len(live)} pencere hareket kapısında düştü)",
        f"- Model: {data['config']['model']}",
        "",
        "| Kol | GPU-sn | GPU-sn / dk görüntü | derin okunan pencere | yakalama (anomali) | yanlış alarm (normal) |",
        "|---|---|---|---|---|---|",
    ]

    deep_all = sum(w["deep_s"] for _, w in live)
    hit_a = sum(detected(c, None) for c in anomaly)
    fa_a = sum(detected(c, None) for c in normal)
    lines.append(
        f"| **A — hepsini derin oku** | {deep_all:.0f} | {deep_all/footage:.1f} | "
        f"{len(live)}/{len(live)} | {hit_a}/{len(anomaly)} | {fa_a}/{len(normal)} |"
    )

    glance_all = sum(w["glance_s"] for _, w in live)
    for thr in THRESHOLDS:
        passed = [w for _, w in live if w["glance_p"] >= thr]
        cost = glance_all + sum(w["deep_s"] for w in passed)
        hit = sum(detected(c, thr) for c in anomaly)
        fa = sum(detected(c, thr) for c in normal)
        lines.append(
            f"| B — bakış, eşik {thr:.2f} | {cost:.0f} | {cost/footage:.1f} | "
            f"{len(passed)}/{len(live)} | {hit}/{len(anomaly)} | {fa}/{len(normal)} |"
        )

    if live:
        g_avg = glance_all / len(live)
        d_avg = deep_all / len(live)
        lines += ["", f"Ortalama: bakış {g_avg:.2f} sn · derin okuma {d_avg:.2f} sn · "
                      f"**oran {d_avg / g_avg:.1f}×** (kaskad deseni ~20× varsayar)"]

    def bucket(w):
        s = window_severity(w)
        return "orta+ olaylı" if s >= ALARM_FLOOR else ("yalnız dusuk" if s >= 0 else "olaysız")

    lines += ["", "## Bakışın ayırt ediciliği", "",
              "| Pencere grubu | n | ortanca P(YES) | min | maks |", "|---|---|---|---|---|"]
    groups: dict[str, list[float]] = {}
    for _, w in live:
        groups.setdefault(bucket(w), []).append(w["glance_p"])
    for name in ("orta+ olaylı", "yalnız dusuk", "olaysız"):
        vals = sorted(groups.get(name, []))
        if vals:
            lines.append(f"| {name} | {len(vals)} | {vals[len(vals)//2]:.3f} | "
                         f"{vals[0]:.3f} | {vals[-1]:.3f} |")
    real = groups.get("orta+ olaylı", [])
    if real:
        lines.append("")
        lines.append(f"- **Kaçırmamak için gereken eşik ≤ {min(real):.3f}** — "
                     f"gerçek olaylı pencerelerin en düşük P(YES) değeri. Dağılımlar "
                     f"örtüştüğü için bu eşik neredeyse tüm pencereleri geçirir.")

    lines += ["", "## Şiddet dağılımı", "", "| Klip türü | " +
              " | ".join(SEVERITY) + " |", "|---|" + "---|" * len(SEVERITY)]
    for label, group in (("anomali", anomaly), ("normal", normal)):
        counts = [sum(1 for c in group for w in c["windows"]
                      for e in w.get("events", []) if e["severity_hint"] == s)
                  for s in SEVERITY]
        lines.append(f"| {label} | " + " | ".join(str(n) for n in counts) + " |")

    lines += ["", "## Sınıf bazında yakalama (kol A)", "",
              "| Sınıf | klip | yakalanan | toplam olay |", "|---|---|---|---|"]
    for cls in sorted({c["class"] for c in clips}):
        group = [c for c in clips if c["class"] == cls]
        hits = sum(detected(c, None) for c in group)
        evs = sum(w.get("n_events", 0) for c in group for w in c["windows"] if not w["gated"])
        lines.append(f"| {cls} | {len(group)} | {hits} | {evs} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="yalnız ilk N klip")
    ap.add_argument("--analyze", type=Path, help="kayıtlı JSON/JSONL'i yeniden çözümle")
    ap.add_argument("--split", choices=["test", "train"],
                    help="media/ yerine resmî UCF-Crime bölmesini koş")
    ap.add_argument("--ucf", type=Path, help="UCF-Crime kopyasının yeri (bkz. make_long_feed)")
    ap.add_argument("--out", type=Path,
                    help="çıktı JSONL (varsa kaldığı yerden devam eder)")
    ap.add_argument("--sysprompt", type=Path,
                    help="istem A/B: SYSTEM_TR yerine bu dosyanın içeriği")
    ap.add_argument("--tierprompt", type=Path,
                    help="istem A/B: TIER_TR yerine bu dosyanın içeriği")
    ap.add_argument("--escalate", type=float, default=0.0, metavar="TAU",
                    help="olagan + durum_p ≥ TAU pencereyi düşünmeli çağrıyla yeniden sorgula")
    ap.add_argument("--clips", type=Path,
                    help="dosyadaki klipleri koş (satırda bir ad; UCF ağacında çözülür)")
    ap.add_argument("--parallel", type=int, default=1,
                    help="aynı anda N klip (⚠ N>1'de süreler kuyruk beklemesi içerir)")
    args = ap.parse_args()

    global SYSTEM_OVERRIDE, TIER_OVERRIDE, PARALLEL, ESCALATE_TAU
    if args.sysprompt:
        SYSTEM_OVERRIDE = args.sysprompt.read_text(encoding="utf-8").strip()
    if args.tierprompt:
        TIER_OVERRIDE = args.tierprompt.read_text(encoding="utf-8").strip()
    PARALLEL = max(1, args.parallel)
    ESCALATE_TAU = args.escalate

    if args.analyze:
        print(analyze(load_results(args.analyze)))
        return

    if args.clips:
        videos = resolve_ucf(args.ucf)
        index = {p.name: p for p in videos.rglob("*.mp4")}
        names = [l.strip() for l in args.clips.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
        missing = [n for n in names if n not in index]
        if missing:
            raise SystemExit(f"{len(missing)} klip UCF ağacında yok (ilk: {missing[0]})")
        clips = [index[n] for n in names]
    elif args.split:
        videos = resolve_ucf(args.ucf)
        split_file = (videos.parent / "Anomaly_Detection_splits"
                      / f"Anomaly_{args.split.capitalize()}.txt")
        clips = [videos / line.strip()
                 for line in split_file.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        missing = [c for c in clips if not c.is_file()]
        if missing:
            print(f"⚠ {len(missing)} bölme klibi diskte yok, atlanıyor "
                  f"(ilk: {missing[0].name})")
            clips = [c for c in clips if c.is_file()]
    else:
        clips = sorted(settings.media_dir.glob("*_x264.mp4"))
        if not clips:
            raise SystemExit("media/ altında klip yok")
    clips = clips[: args.limit]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or RESULTS / f"ab_{stamp}.jsonl"
    asyncio.run(collect(clips, out))

    report = analyze(load_results(out))
    out.with_suffix(".md").write_text(report, encoding="utf-8")
    print(f"\nham veri: {out}\n")
    print(report)


if __name__ == "__main__":
    main()
