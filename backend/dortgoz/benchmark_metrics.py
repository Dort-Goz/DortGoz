"""JSONL benchmark artifact'larından saf agent ve evidence metrikleri."""

from __future__ import annotations

from collections import Counter
from typing import Any


def agent_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    terminals = Counter(str(record.get("terminal_status", "unknown")) for record in records)
    routes = Counter(
        str(step.get("action", "unknown"))
        for record in records
        for step in record.get("decision_trace", [])
    )
    tools = Counter(
        str(step.get("tool_name"))
        for record in records
        for step in record.get("decision_trace", [])
        if step.get("tool_name")
    )
    step_counts = [len(record.get("decision_trace", [])) for record in records]
    vlm_calls = [int(record.get("vlm_attempts", 0)) for record in records]
    recoveries = sum(
        action in {"RETRY_VLM_STRICT", "EXPAND_CONTEXT", "RUN_DENSE_ANALYSIS"}
        for action, count in routes.items()
        for _ in range(count)
    )
    return {
        "record_count": len(records),
        "terminal_counts": dict(sorted(terminals.items())),
        "route_counts": dict(sorted(routes.items())),
        "tool_counts": dict(sorted(tools.items())),
        "mean_steps": sum(step_counts) / len(step_counts) if step_counts else 0.0,
        "max_steps": max(step_counts, default=0),
        "mean_vlm_calls": sum(vlm_calls) / len(vlm_calls) if vlm_calls else 0.0,
        "recovery_actions": recoveries,
    }


def evidence_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("schema_valid", "timestamps_valid", "evidence_valid", "language_valid")
    totals = {key: sum(bool(record.get(key)) for record in records) for key in keys}
    unsupported = sum(bool(record.get("unsupported_critical_claim")) for record in records)
    permitted = sum(bool(record.get("permits_confirmation")) for record in records)
    count = len(records)
    return {
        "record_count": count,
        "valid_counts": totals,
        "valid_rates": {key: value / count if count else 0.0 for key, value in totals.items()},
        "unsupported_critical_claims": unsupported,
        "confirmation_permitted": permitted,
        "confirmation_rate": permitted / count if count else 0.0,
    }


def candidate_metrics(records: list[dict[str, Any]], *, tiou_threshold: float = 0.5) -> dict[str, Any]:
    if not 0 < tiou_threshold <= 1:
        raise ValueError("tiou_threshold 0 ile 1 arasında olmalı")
    ground_truth = candidates = hits = 0
    matched_ious: list[float] = []
    video_seconds = candidate_seconds = 0.0
    for record in records:
        truths = [(float(item["start_time"]), float(item["end_time"])) for item in record.get("ground_truth", [])]
        predicted = [(float(item["start_time"]), float(item["end_time"])) for item in record.get("candidates", [])]
        used: set[int] = set()
        ground_truth += len(truths)
        candidates += len(predicted)
        video_seconds += float(record.get("duration_seconds", 0))
        candidate_seconds += sum(end - start for start, end in predicted)
        for truth in truths:
            choices = [(index, _tiou(truth, prediction)) for index, prediction in enumerate(predicted) if index not in used]
            if choices and (best := max(choices, key=lambda item: item[1]))[1] >= tiou_threshold:
                used.add(best[0])
                hits += 1
                matched_ious.append(best[1])
    return {
        "video_count": len(records), "ground_truth_count": ground_truth, "candidate_count": candidates,
        "true_positive_intervals": hits, "false_negative_intervals": ground_truth - hits,
        "recall": hits / ground_truth if ground_truth else 0.0,
        "mean_tiou": sum(matched_ious) / len(matched_ious) if matched_ious else 0.0,
        "candidates_per_hour": candidates / (video_seconds / 3600) if video_seconds else 0.0,
        "vlm_time_ratio": candidate_seconds / video_seconds if video_seconds else 0.0,
    }


def _tiou(left: tuple[float, float], right: tuple[float, float]) -> float:
    overlap = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return overlap / union if union else 0.0


__all__ = ["agent_metrics", "candidate_metrics", "evidence_metrics"]
