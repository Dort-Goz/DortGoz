from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evren_quality import summarize


def wilson(hit: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = hit / total
    denom = 1 + z * z / total
    center = p + z * z / (2 * total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return ((center - half) / denom * 100, (center + half) / denom * 100)


def load(path: Path) -> tuple[dict, list[dict]]:
    config: dict = {}
    clips: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("type") == "config":
            config = row
        elif row.get("type") == "clip":
            clips.append(row)
    return config, clips


def speed(clips: list[dict]) -> float:
    wall = sum(row.get("wall_seconds", 0.0) for row in clips)
    return sum(row.get("duration", 0.0) for row in clips) / wall if wall else 0.0


def pct(hit: int, total: int) -> str:
    return f"{hit}/{total} = %{hit / total * 100:.1f}" if total else "-"


def band(values: list[float]) -> str:
    return f"%{min(values):.1f}-%{max(values):.1f}" if values else "-"


def report(path: Path) -> str:
    config, clips = load(path)
    repeats = sorted({row.get("repeat", 0) for row in clips})
    lines = [
        f"# Kör holdout bandı — {path.name}",
        "",
        f"- Kod: `{config.get('code_revision', '?')}`",
        f"- Giriş genişliği: {config.get('video_input_width', '?')} · "
        f"eşzamanlı iş: {config.get('parallel', '?')} · "
        f"pencere: {config.get('window_seconds', '?')} sn",
        f"- Screening eşikleri: {config.get('candidate_start_threshold', '?')} / "
        f"{config.get('candidate_continue_threshold', '?')}",
        f"- config_hash: `{config.get('config_hash', '?')}`",
        "",
        "| koşu | klip | yakalama | yanlış alarm | kategori | kanıt (teknik) | "
        "tek akış | terminal hata |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    recalls: list[float] = []
    alarms: list[float] = []
    for repeat in repeats:
        rows = [row for row in clips if row.get("repeat", 0) == repeat]
        item = summarize(rows)
        recalls.append(item["detected"] / item["anomaly_records"] * 100)
        alarms.append(item["false_alarm"] / item["normal_records"] * 100)
        technical = item["evidence_technical_valid_rate"]
        lines.append(
            f"| r{repeat + 1} | {item['records']} | "
            f"{pct(item['detected'], item['anomaly_records'])} | "
            f"{pct(item['false_alarm'], item['normal_records'])} | "
            f"{item['category_correct']} | "
            f"{'%' + format(technical * 100, '.1f') if technical else '-'} | "
            f"{speed(rows):.2f}× | {item['terminal_failures']} |"
        )
    total = summarize(clips)
    distinct = len({row["clip"] for row in clips})
    anomaly_clips = len({row["clip"] for row in clips if row["anomaly"]})
    normal_clips = distinct - anomaly_clips
    recall_lo, recall_hi = wilson(
        round(sum(recalls) / len(recalls) / 100 * anomaly_clips), anomaly_clips
    )
    alarm_lo, alarm_hi = wilson(
        round(sum(alarms) / len(alarms) / 100 * normal_clips), normal_clips
    )
    lines += [
        "",
        f"**Koşu bandı ({len(repeats)} tekrar):** yakalama {band(recalls)} · "
        f"yanlış alarm {band(alarms)}.",
        "",
        f"**Örnekleme aralığı (Wilson %95, ayrık klip sayısına göre):** "
        f"yakalama %{sum(recalls) / len(recalls):.1f} "
        f"[%{recall_lo:.1f}-%{recall_hi:.1f}] (n={anomaly_clips}) · "
        f"yanlış alarm %{sum(alarms) / len(alarms):.1f} "
        f"[%{alarm_lo:.1f}-%{alarm_hi:.1f}] (n={normal_clips}).",
        "",
        f"Toplam video: {sum(row.get('duration', 0.0) for row in clips) / 3600 / len(repeats):.2f} "
        f"saat · toplam iş süresi {total['wall_seconds'] / 3600:.2f} saat · "
        f"terminal hata {total['terminal_failures']}/{total['records']}.",
        "",
        "Kaçırılan klipler (tüm tekrarlarda birleşik):",
        "",
    ]
    lines += [f"- `{clip}`" for clip in sorted(set(total["missed"]))] or ["- yok"]
    return "\n".join(lines) + "\n"


def self_test() -> int:
    low, high = wilson(36, 40)
    assert (round(low, 1), round(high, 1)) == (76.9, 96.0), (low, high)
    assert wilson(0, 0) == (0.0, 0.0)
    rows = [
        {"type": "clip", "clip": "a.mp4", "class": "Abuse", "anomaly": True, "repeat": 0,
         "duration": 60.0, "wall_seconds": 30.0, "terminal": "done", "errors": [],
         "incidents": [{"anomaly_type": "kavga", "risk": "orta"}], "metrics": {}},
        {"type": "clip", "clip": "b.mp4", "class": "Normal", "anomaly": False, "repeat": 0,
         "duration": 60.0, "wall_seconds": 30.0, "terminal": "done", "errors": [],
         "incidents": [], "metrics": {}},
        {"type": "clip", "clip": "a.mp4", "class": "Abuse", "anomaly": True, "repeat": 1,
         "duration": 60.0, "wall_seconds": 60.0, "terminal": "done", "errors": [],
         "incidents": [], "metrics": {}},
        {"type": "clip", "clip": "b.mp4", "class": "Normal", "anomaly": False, "repeat": 1,
         "duration": 60.0, "wall_seconds": 60.0, "terminal": "done", "errors": [],
         "incidents": [], "metrics": {}},
    ]
    first = summarize([row for row in rows if row["repeat"] == 0])
    assert (first["detected"], first["anomaly_records"]) == (1, 1), first
    second = summarize([row for row in rows if row["repeat"] == 1])
    assert second["detected"] == 0, second
    assert abs(speed([rows[0], rows[1]]) - 2.0) < 1e-9
    assert abs(speed([rows[2], rows[3]]) - 1.0) < 1e-9
    print("bant_ozet self-test tamam")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path, nargs="?")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.jsonl is None:
        parser.error("jsonl yolu gerekli")
    text = report(args.jsonl)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
