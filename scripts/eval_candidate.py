"""JSONL screening sample'larından candidate interval raporu üretir."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.domain.candidate import ScreeningSample
from dortgoz.pipeline.candidate_intervals import IntervalConfig, build_candidate_intervals


def load_samples(path: Path) -> list[ScreeningSample]:
    samples: list[ScreeningSample] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            samples.append(ScreeningSample.model_validate(json.loads(raw)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"geçersiz screening sample satırı: {line_number}") from exc
    return samples


def evaluate(
    samples: list[ScreeningSample],
    *,
    analysis_id: str,
    video_id: str,
    duration_seconds: float,
) -> dict:
    candidates = build_candidate_intervals(
        samples,
        analysis_id=analysis_id,
        video_id=video_id,
        duration_seconds=duration_seconds,
        model_id=samples[0].source_model if samples else "unknown",
        config=IntervalConfig(),
    )
    return {
        "analysis_id": analysis_id,
        "video_id": video_id,
        "duration_seconds": duration_seconds,
        "sample_count": len(samples),
        "candidate_count": len(candidates),
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path)
    parser.add_argument("--analysis-id", default="candidate-eval")
    parser.add_argument("--video-id", default="candidate-video")
    parser.add_argument("--duration", type=float, required=True)
    args = parser.parse_args()
    payload = evaluate(
        load_samples(args.samples),
        analysis_id=args.analysis_id,
        video_id=args.video_id,
        duration_seconds=args.duration,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
