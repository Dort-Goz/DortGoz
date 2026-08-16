#!/usr/bin/env python3
"""Index a local UCF-Crime copy without copying media into the repository.

The generated manifest is benchmark-only because UCF-Crime has no verified
redistribution or training license in the project policy.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dortgoz.services.dataset_manifest import (  # noqa: E402
    build_ucf_crime_manifest,
    write_dataset_manifest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root",
        type=Path,
        help="UCF_Crimes veya doğrudan Videos dizini; yoksa DORTGOZ_UCF_DIR",
    )
    parser.add_argument(
        "--annotations-dir",
        type=Path,
        default=REPO_ROOT / "data" / "annotations" / "candidate",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "runs" / "datasets" / "ucf_crime_manifest.json",
    )
    args = parser.parse_args()
    dataset_root = args.dataset_root
    if dataset_root is None:
        configured = os.getenv("DORTGOZ_UCF_DIR", "").strip()
        if not configured:
            parser.error("--dataset-root veya DORTGOZ_UCF_DIR zorunludur")
        dataset_root = Path(configured)

    def progress(index: int, total: int, path: Path) -> None:
        if index == 1 or index == total or index % 25 == 0:
            print(f"[{index}/{total}] SHA-256 · {path.name}")

    manifest = build_ucf_crime_manifest(
        dataset_root,
        annotation_dir=args.annotations_dir,
        progress=progress,
    )
    target = write_dataset_manifest(args.output, manifest)
    splits = Counter(item.split.value for item in manifest.entries)
    print(f"\nmanifest: {target}")
    print(f"video: {len(manifest.entries)} · split: {dict(sorted(splits.items()))}")
    print(f"fingerprint: {manifest.dataset_fingerprint}")
    print("training_allowed: false · redistribution_allowed: false")


if __name__ == "__main__":
    main()
