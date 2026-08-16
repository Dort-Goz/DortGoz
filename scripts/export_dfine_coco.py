#!/usr/bin/env python3
"""Export human-verified local frames as D-FINE-compatible COCO JSON.

No image or video is copied. The generated JSON refers to files below
``--frame-root``. The dataset manifest must explicitly allow local training.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dortgoz.services.coco_export import (  # noqa: E402
    export_verified_frames_to_coco,
    load_training_frame_reviews,
)
from dortgoz.services.dataset_manifest import load_dataset_manifest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--annotations-dir", type=Path, required=True)
    parser.add_argument("--frame-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = export_verified_frames_to_coco(
            dataset_manifest=load_dataset_manifest(args.dataset_manifest),
            reviews=load_training_frame_reviews(args.annotations_dir),
            frame_root=args.frame_root,
            output_dir=args.output_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(f"COCO paket: {result.output_dir}")
    print(f"kare: {result.frame_count} · kutu: {result.box_count}")
    print(f"fingerprint: {result.export_fingerprint}")
    print("medya kopyalandı: hayır · D-FINE için --frame-root ayrıca verilmelidir")


if __name__ == "__main__":
    main()
