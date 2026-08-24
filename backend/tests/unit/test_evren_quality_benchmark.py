from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "evren_quality", ROOT / "bench" / "evren_quality.py"
)
assert SPEC is not None and SPEC.loader is not None
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)


def row(name: str, anomaly: bool, incidents: list[dict], model_calls: dict[str, int]):
    return {
        "type": "clip",
        "clip": name,
        "class": BENCH.family(Path(name)),
        "anomaly": anomaly,
        "repeat": 0,
        "duration": 30,
        "wall_seconds": 5,
        "terminal": "done",
        "windows": [],
        "incidents": incidents,
        "errors": [],
        "metrics": {
            "evidence_validation_count": len(incidents),
            "evidence_valid_count": len(incidents),
            "model_calls": model_calls,
        },
    }


def test_summary_counts_recall_false_alarm_category_and_models() -> None:
    summary = BENCH.summarize([
        row(
            "Fighting018_x264.mp4",
            True,
            [{"anomaly_type": "kavga"}],
            {"primary:vlm": 2, "incident_review:llm-large": 1},
        ),
        row("Normal_Videos_885_x264.mp4", False, [], {"primary:vlm": 1}),
        row(
            "Normal_Videos_878_x264.mp4",
            False,
            [{"anomaly_type": "saldiri", "risk": "orta"}],
            {"primary:vlm": 1},
        ),
        row(
            "Normal_Videos_010_x264.mp4",
            False,
            [{"anomaly_type": "normal", "risk": "orta"}],
            {"primary:vlm": 1},
        ),
    ])

    assert summary["detected"] == 1
    assert summary["false_alarm"] == 1
    assert summary["category_correct"] == 1
    assert summary["review_normalized"] == 1
    assert summary["evidence_technical_valid_rate"] == 1.0
    assert summary["evidence_automatic_valid_rate"] == 1.0
    assert summary["model_calls"] == {
        "incident_review:llm-large": 1,
        "primary:vlm": 5,
    }
