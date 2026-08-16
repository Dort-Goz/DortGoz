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

from dortgoz.repositories.sqlite import SqliteEventRepository  # noqa: E402
from dortgoz.services.coco_export import (  # noqa: E402
    export_verified_frames_to_coco,
    load_training_frame_reviews,
    training_reviews_from_samples,
)
from dortgoz.services.dataset_manifest import load_dataset_manifest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--annotations-dir", type=Path)
    source.add_argument("--event-store", type=Path)
    parser.add_argument("--frame-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        dataset_manifest = load_dataset_manifest(args.dataset_manifest)
        if args.event_store is not None:
            repository = SqliteEventRepository(args.event_store)
            try:
                reviews = training_reviews_from_samples(
                    repository.list_training_samples(), dataset_manifest
                )
            finally:
                repository.close()
        else:
            reviews = load_training_frame_reviews(args.annotations_dir)
        result = export_verified_frames_to_coco(
            dataset_manifest=dataset_manifest,
            reviews=reviews,
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
