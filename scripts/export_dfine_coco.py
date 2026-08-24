#!/usr/bin/env python3


from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dortgoz.repositories.sqlite import SqliteEventRepository
from dortgoz.services.coco_export import (
    export_verified_frames_to_coco,
    load_training_frame_reviews,
    training_reviews_from_samples,
)
from dortgoz.services.dataset_manifest import load_dataset_manifest
from dortgoz.services.training_selection import (
    TrainingSelectionError,
    load_training_selection_policy,
    select_training_samples,
    write_training_selection_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--annotations-dir", type=Path)
    source.add_argument("--event-store", type=Path)
    parser.add_argument("--frame-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--selection-policy",
        type=Path,
        help="SQLite örneklerini sabit bütçe ve çeşitlilik kurallarıyla seç",
    )
    parser.add_argument(
        "--selection-report",
        type=Path,
        help="seçim denetim raporu (varsayılan: output-dir/selection_report.json)",
    )
    args = parser.parse_args()
    if args.selection_policy is not None and args.event_store is None:
        parser.error("--selection-policy yalnız --event-store ile kullanılabilir")
    if args.selection_report is not None and args.selection_policy is None:
        parser.error("--selection-report için --selection-policy zorunludur")
    selection = None
    try:
        dataset_manifest = load_dataset_manifest(args.dataset_manifest)
        if args.event_store is not None:
            repository = SqliteEventRepository(args.event_store)
            try:
                samples = repository.list_training_samples()
                if args.selection_policy is not None:
                    selection = select_training_samples(
                        samples=samples,
                        dataset_manifest=dataset_manifest,
                        policy=load_training_selection_policy(args.selection_policy),
                    )
                    samples = selection.selected_samples
                reviews = training_reviews_from_samples(samples, dataset_manifest)
            finally:
                repository.close()
        else:
            reviews = load_training_frame_reviews(args.annotations_dir)
        result = export_verified_frames_to_coco(
            dataset_manifest=dataset_manifest,
            reviews=reviews,
            frame_root=args.frame_root,
            output_dir=args.output_dir,
            selection_report=selection.report if selection is not None else None,
        )
        if selection is not None:
            report_path = args.selection_report or args.output_dir / "selection_report.json"
            write_training_selection_report(report_path, selection.report)
    except (OSError, TrainingSelectionError, ValidationError, ValueError) as exc:
        parser.error(str(exc))
    print(f"COCO paket: {result.output_dir}")
    print(f"kare: {result.frame_count} · kutu: {result.box_count}")
    print(f"fingerprint: {result.export_fingerprint}")
    if selection is not None:
        print(
            "seçim: "
            f"{selection.report.counts.selected} seçildi · "
            f"{selection.report.counts.excluded} elendi · "
            f"{selection.report.selection_fingerprint}"
        )
    print("medya kopyalandı: hayır · D-FINE için --frame-root ayrıca verilmelidir")


if __name__ == "__main__":
    main()
