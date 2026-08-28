from __future__ import annotations

import argparse
import collections
import json
import random
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

QUOTA = {
    "Abuse": 1, "Arrest": 1, "Arson": 3, "Assault": 1, "Burglary": 4,
    "Explosion": 6, "Fighting": 1, "RoadAccidents": 7, "Robbery": 1,
    "Shooting": 7, "Shoplifting": 6, "Stealing": 1, "Vandalism": 1,
    "Normal_Videos": 40,
}


def family(stem: str) -> str:
    match = re.match(r"([A-Za-z_]*[A-Za-z])[0-9_]*", stem)
    base = match.group(1).rstrip("_") if match else stem
    return "Normal_Videos" if base.startswith("Normal_Videos") else base


def developed_stems(skip_results: tuple[str, ...]) -> set[str]:
    used: set[str] = set()
    annotations = ROOT / "data" / "annotations" / "candidate"
    if annotations.is_dir():
        used.update(path.stem for path in annotations.glob("*.json"))
    media = ROOT / "media"
    if media.is_dir():
        used.update(path.stem for path in media.glob("*.mp4"))
    results = ROOT / "bench" / "results"
    if results.is_dir():
        for record in results.glob("*.jsonl"):
            if any(fnmatch(record.name, pattern) for pattern in skip_results):
                continue
            for line in record.read_text(encoding="utf-8", errors="ignore").splitlines():
                if '"clip"' not in line:
                    continue
                try:
                    clip = json.loads(line).get("clip")
                except ValueError:
                    continue
                if isinstance(clip, str):
                    used.add(Path(clip).stem)
    return used


def duration(path: Path) -> float:
    try:
        output = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        return float(output)
    except (OSError, ValueError, subprocess.SubprocessError):
        return float("inf")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ucf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--min-seconds", type=float, default=10.0)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument("--skip-results", nargs="*", default=["evren_holdout_*"])
    args = parser.parse_args()

    split = args.ucf / "Anomaly_Detection_splits" / "Anomaly_Train.txt"
    videos = args.ucf / "Videos"
    train = {Path(line.strip()).stem for line in split.read_text().splitlines() if line.strip()}
    used = developed_stems(tuple(args.skip_results))
    clean = sorted(train - used)
    print(f"eğitim bölmesi {len(train)} · geliştirmede kullanılan {len(train & used)} · "
          f"dokunulmamış {len(clean)}")

    index = {path.stem: path for path in videos.rglob("*.mp4")}
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for stem in clean:
        buckets[family(stem)].append(stem)

    rng = random.Random(args.seed)
    picked: list[tuple[str, str, float]] = []
    for name, base in sorted(QUOTA.items()):
        quota = base * args.scale
        pool = list(buckets.get(name, []))
        rng.shuffle(pool)
        taken = 0
        for stem in pool:
            if taken >= quota:
                break
            path = index.get(stem)
            if path is None:
                continue
            seconds = duration(path)
            if args.min_seconds <= seconds <= args.max_seconds:
                picked.append((stem, name, round(seconds, 1)))
                taken += 1
        if taken < quota:
            print(f"EKSİK {name}: {taken}/{quota}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(f"{stem}.mp4" for stem, _, _ in picked) + "\n", encoding="utf-8")
    total = sum(item[2] for item in picked)
    print(f"seçilen {len(picked)} klip · {total / 60:.0f} dk · tohum {args.seed}")
    print(sorted(collections.Counter(item[1] for item in picked).items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
