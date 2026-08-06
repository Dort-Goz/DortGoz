"""Pencere-düzeyi ZAMANSAL doğruluk — ab_pipeline JSONL'i + resmî anomali sınırları.

Klip-düzeyi zayıf etiket ("klipte olay var mıydı") yerine gerçek soru: alarm
üreten pencereler anomalinin GERÇEK zaman aralığıyla örtüşüyor mu? Referans:
`Temporal_Anomaly_Annotation.txt` (orijinal UCF-Crime yazarları; kare indeksli,
yalnız test bölmesi; lisanssız → repo dışında, yerel veri kopyasının yanında —
bkz. data/uca/README.md).

    cd backend && uv run python ../bench/temporal_eval.py --results ../bench/results/ab_testsplit_96k.jsonl

Alarm ölçütü ab_pipeline ile AYNI: pencerede `orta`+ şiddetinde olay (ALARM_FLOOR).
Kare→saniye çevrimi videonun kendi fps'iyle yapılır (ffprobe, önbellekli).
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from pathlib import Path

from ab_pipeline import ALARM_FLOOR, clip_class, load_results, window_severity
from make_long_feed import resolve_ucf  # ab_pipeline sys.path'i zaten kurar


def video_fps(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    num, _, den = out.partition("/")
    return float(num) / float(den or 1)


def load_ground_truth(videos_dir: Path) -> dict[str, list[tuple[float, float]]]:
    """Klip adı → saniye cinsinden anomali aralıkları (normal kliplerde boş)."""
    ann = videos_dir.parent / "Temporal_Anomaly_Annotation.txt"
    if not ann.is_file():
        raise SystemExit(
            f"{ann} yok — indir: data/uca/README.md içindeki bağlantı")
    gt: dict[str, list[tuple[float, float]]] = {}
    for line in ann.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        name, cls = parts[0], parts[1]
        frames = [int(p) for p in parts[2:6]]
        spans = [(frames[i], frames[i + 1]) for i in (0, 2) if frames[i] >= 0]
        if spans:  # normal kliplerde aralık yok → fps problamaya gerek yok
            fps = video_fps(videos_dir / cls / name)
            gt[name] = [(f0 / fps, f1 / fps) for f0, f1 in spans]
        else:
            gt[name] = []
    return gt


def overlaps(w0: float, w1: float, spans: list[tuple[float, float]]) -> bool:
    return any(w0 < t1 and t0 < w1 for t0, t1 in spans)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--ucf", type=Path)
    args = ap.parse_args()

    videos = resolve_ucf(args.ucf)
    gt = load_ground_truth(videos)
    data = load_results(args.results)
    clips = [c for c in data["clips"] if "windows" in c]

    # pencere sayaçları
    tp = fp_anom = fn_windows = 0        # anomali klipleri, pencere düzeyi
    fp_normal = 0                        # normal kliplerde alarm penceresi
    normal_hours = sum(c["duration"] for c in clips if not c["anomaly"]) / 3600
    events_total = events_hit = 0        # GT aralığı düzeyi (asıl yakalama)
    delays: list[float] = []             # GT başlangıcı → ilk alarm penceresi
    per_class: dict[str, list[int]] = {} # sınıf → [aralık, yakalanan]
    skipped = 0

    for c in clips:
        name = c["clip"] if c["clip"].endswith(".mp4") else c["clip"] + ".mp4"
        spans = gt.get(name)
        if spans is None:
            skipped += 1
            continue
        alarms = [(w["start"], w["end"]) for w in c["windows"]
                  if not w["gated"] and window_severity(w) >= ALARM_FLOOR]
        if not c["anomaly"]:
            fp_normal += len(alarms)
            continue
        for w in c["windows"]:
            if w["gated"]:
                continue
            hit_gt = overlaps(w["start"], w["end"], spans)
            alarm = window_severity(w) >= ALARM_FLOOR
            if alarm and hit_gt:
                tp += 1
            elif alarm:
                fp_anom += 1
            elif hit_gt:
                fn_windows += 1
        cls = clip_class(Path(name))
        stat = per_class.setdefault(cls, [0, 0])
        for t0, t1 in spans:
            events_total += 1
            stat[0] += 1
            hits = [a0 for a0, a1 in alarms if a0 < t1 and t0 < a1]
            if hits:
                events_hit += 1
                stat[1] += 1
                delays.append(max(0.0, min(hits) - t0))

    prec = tp / (tp + fp_anom + fp_normal) if tp else 0.0
    rec_w = tp / (tp + fn_windows) if tp else 0.0
    print(f"# Zamansal değerlendirme — {args.results.name}")
    print(f"- Klip: {len(clips) - skipped} (GT'siz atlanan: {skipped})")
    print(f"- **GT aralığı yakalama: {events_hit}/{events_total}**"
          f" ({100 * events_hit / max(events_total, 1):.0f}%)")
    print(f"- Pencere düzeyi: TP {tp} · FP(anomali klipte) {fp_anom} · "
          f"FP(normal klipte) {fp_normal} · FN {fn_windows} → "
          f"kesinlik {prec:.2f} · duyarlılık {rec_w:.2f}")
    print("  ⚠ FP(anomali klipte) GT kapsam gevşekliğini de sayar (ör. Arrest030: "
          "GT yalnız tutuklama anını işaretler, model ATM soygununun tamamını "
          "doğru raporlar) — birincil yanlış alarm metriği NORMAL kliplerdir.")
    if normal_hours:
        print(f"- Normal kayıtta yanlış alarm: {fp_normal / normal_hours:.1f} pencere/saat "
              f"({normal_hours:.1f} saat normal görüntü)")
    if delays:
        print(f"- Tespit gecikmesi (GT başlangıcı → ilk alarm penceresi): "
              f"ortanca {statistics.median(delays):.0f} sn · maks {max(delays):.0f} sn")
    print()
    print("| Sınıf | GT aralığı | yakalanan |")
    print("|---|---|---|")
    for cls in sorted(per_class):
        n, hit = per_class[cls]
        print(f"| {cls} | {n} | {hit} |")

    # GT süresine göre yakalama — "kısa olay 30 sn pencerede kayboluyor mu"
    # sorusunun ölçümü (kare seçimi ~5 sn aralıklı: 2-3 sn'lik olay 0-1 kareye düşer)
    buckets = [(0, 5), (5, 15), (15, 30), (30, float("inf"))]
    counts = {b: [0, 0] for b in buckets}
    for c in clips:
        name = c["clip"] if c["clip"].endswith(".mp4") else c["clip"] + ".mp4"
        spans = gt.get(name) or []
        alarms = [(w["start"], w["end"]) for w in c["windows"]
                  if not w["gated"] and window_severity(w) >= ALARM_FLOOR]
        for t0, t1 in spans:
            for b in buckets:
                if b[0] <= t1 - t0 < b[1]:
                    counts[b][0] += 1
                    counts[b][1] += any(a0 < t1 and t0 < a1 for a0, a1 in alarms)
    print()
    print("| GT olay süresi | aralık | yakalanan |")
    print("|---|---|---|")
    for (lo, hi), (n, hit) in counts.items():
        label = f"{lo:.0f}–{hi:.0f} sn" if hi != float("inf") else f"≥{lo:.0f} sn"
        print(f"| {label} | {n} | {hit} |")


if __name__ == "__main__":
    main()
