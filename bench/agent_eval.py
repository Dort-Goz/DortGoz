"""Canonical agent JSONL artifact'ından route/tool/recovery metrikleri üretir.

Her satır terminal candidate state'inin ``model_dump(mode='json')`` görünümüdür.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.benchmark_metrics import agent_metrics


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    print(json.dumps(agent_metrics(load_jsonl(args.artifact)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
