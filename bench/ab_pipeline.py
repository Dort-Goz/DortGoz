"""A2 iki kollu kıyaslama — "hepsini derin oku" vs "ucuz bakış → derin oku".

Tek geçişte her pencere için HEM ucuz bakış (P(YES) + süre) HEM derin okuma
(rapor + süre) ölçülür. Kol B böylece herhangi bir eşik için sonradan yeniden
kurulabilir — eşik taraması için yeniden koşmak gerekmez.

    cd backend && uv run python ../bench/ab_pipeline.py            # ölç + raporla
    cd backend && uv run python ../bench/ab_pipeline.py --limit 4  # hızlı deneme
    cd backend && uv run python ../bench/ab_pipeline.py --analyze bench/results/ab_*.json

Etiket: klip adının sınıfı (UCF-Crime). Normal_* klipler negatif kontrol.
Bu klip-düzeyi zayıf etikettir; zamansal IoU için UCA anotasyonları gerekir
(açık iş #10) — o gelince aynı JSON üzerinden hesaplanabilir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from dortgoz.config import settings
from dortgoz.pipeline import ingest, windowing
from dortgoz.pipeline.interpret import glance_window, interpret_window

RESULTS = Path(__file__).parent / "results"
THRESHOLDS = [0.01, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 0.90]


def clip_class(path: Path) -> str:
    return re.sub(r"_?\d+$", "", path.stem.replace("_x264", ""))


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
    for start, end in windowing.windows(duration, settings.window_seconds):
        peak = windowing.window_motion(profile, start, end)
        rec = {"start": start, "end": end, "motion": peak, "gated": peak < settings.motion_gate}
        if not rec["gated"]:
            keys = windowing.select_keyframes(profile, start, end, settings.keyframes_per_window)

            t0 = time.time()
            rec["glance_p"] = await glance_window(path, (start, end), keys[:2])
            rec["glance_s"] = time.time() - t0

            t0 = time.time()
            report = await interpret_window(path, (start, end), keys)
            rec["deep_s"] = time.time() - t0
            rec["n_events"] = len(report.events)
            rec["summary"] = report.summary
            rec["events"] = [e.model_dump() for e in report.events]
        out["windows"].append(rec)
    return out


async def collect(clips: list[Path]) -> dict:
    data = {"clips": [], "config": {
        "model": settings.main_model,
        "window_seconds": settings.window_seconds,
        "keyframes_per_window": settings.keyframes_per_window,
        "motion_gate": settings.motion_gate,
    }}
    for i, path in enumerate(clips, 1):
        print(f"[{i}/{len(clips)}] {path.name} ...", end="", flush=True)
        try:
            rec = await measure_clip(path)
            data["clips"].append(rec)
            live = [w for w in rec["windows"] if not w["gated"]]
            print(f" {len(rec['windows'])} pencere, "
                  f"{sum(w.get('n_events', 0) for w in live)} olay, "
                  f"p(YES)={[round(w['glance_p'], 2) for w in live]}")
        except Exception as exc:
            print(f" HATA: {exc}")
            data["clips"].append({"clip": path.name, "error": str(exc)})
    return data


# ---- çözümleme ----

SEVERITY = ["dusuk", "orta", "yuksek", "kritik"]
ALARM_FLOOR = 1          # "orta" ve üstü gerçek olay sayılır

# Neden eşik: ilk koşuda (2026-08-03) normal kliplerin ürettiği 14 olayın
# TAMAMI "dusuk" çıktı (park eden araç, içeri giren motosikletli, yürüyen
# insanlar) — bunlar yanlış alarm değil, modelin istendiği gibi sahneyi
# betimlemesi. Anomali kliplerinde ise 40 orta + 25 yuksek + 5 kritik var.
# "Herhangi bir olay = yakalama" ölçütü bu yüzden yanıltıcıydı.


def _live_windows(data: dict) -> list[tuple[dict, dict]]:
    return [(c, w) for c in data["clips"] if "windows" in c
            for w in c["windows"] if not w["gated"]]


def window_severity(w: dict) -> int:
    """Penceredeki en yüksek olay şiddeti (-1 = olay yok)."""
    return max((SEVERITY.index(e["severity_hint"]) for e in w.get("events", [])),
               default=-1)


def analyze(data: dict) -> str:
    clips = [c for c in data["clips"] if "windows" in c]
    anomaly = [c for c in clips if c["anomaly"]]
    normal = [c for c in clips if not c["anomaly"]]
    footage = sum(c["duration"] for c in clips) / 60.0
    live = _live_windows(data)

    def detected(clip: dict, thr: float | None) -> bool:
        """Klipte, eşiği geçen bir pencerede `orta`+ şiddetinde olay bulundu mu?"""
        return any(not w["gated"] and window_severity(w) >= ALARM_FLOOR
                   and (thr is None or w["glance_p"] >= thr)
                   for w in clip["windows"])

    lines = [
        f"# A2 iki kollu kıyaslama",
        "",
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

    # Ortalama birim maliyet — kaskad öncülünün geçerliliği buna bakar
    if live:
        g_avg = glance_all / len(live)
        d_avg = deep_all / len(live)
        lines += ["", f"Ortalama: bakış {g_avg:.2f} sn · derin okuma {d_avg:.2f} sn · "
                      f"**oran {d_avg / g_avg:.1f}×** (kaskad deseni ~20× varsayar)"]

    # Bakışın ayırt ediciliği: P(YES) dağılımı, şiddet gruplarına göre
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

    # Şiddet dağılımı — ölçütün dayanağı
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
    ap.add_argument("--analyze", type=Path, help="kayıtlı JSON'u yeniden çözümle")
    args = ap.parse_args()

    if args.analyze:
        print(analyze(json.loads(args.analyze.read_text(encoding="utf-8"))))
        return

    # Yalnız UCF-Crime klipleri (`*_x264.mp4`) — sentetik/test videoları
    # (ör. surveillance_5min.mp4) değerlendirme setini kirletmemeli
    clips = sorted(settings.media_dir.glob("*_x264.mp4"))[: args.limit]
    if not clips:
        raise SystemExit("media/ altında klip yok")
    data = asyncio.run(collect(clips))

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = RESULTS / f"ab_{stamp}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    report = analyze(data)
    (RESULTS / f"ab_{stamp}.md").write_text(report, encoding="utf-8")
    print(f"\nham veri: {out}\n")
    print(report)


if __name__ == "__main__":
    main()
