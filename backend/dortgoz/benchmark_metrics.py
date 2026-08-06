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


__all__ = ["agent_metrics", "evidence_metrics"]
