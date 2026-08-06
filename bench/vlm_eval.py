"""Etiketli local VLM JSONL artifact'ından karar, evidence ve latency KPI'ları.

Her satır en az ``expected_positive`` ve ``predicted_status`` taşır. Zaman,
validator ve latency alanları varsa ek metriklere girer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.benchmark_metrics import vlm_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.artifact.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(json.dumps(vlm_metrics(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
