from dortgoz.benchmark_metrics import agent_metrics, candidate_metrics, evidence_metrics


def test_agent_metrics_counts_routes_tools_and_recovery() -> None:
    result = agent_metrics([
        {"terminal_status": "confirmed", "vlm_attempts": 1, "decision_trace": [{"action": "RUN_VLM", "tool_name": "vlm_tool"}, {"action": "CONFIRM_EVENT"}]},
        {"terminal_status": "human_review", "vlm_attempts": 2, "decision_trace": [{"action": "RETRY_VLM_STRICT", "tool_name": "vlm_tool"}, {"action": "REQUEST_HUMAN_REVIEW"}]},
    ])
    assert result["terminal_counts"] == {"confirmed": 1, "human_review": 1}
    assert result["tool_counts"] == {"vlm_tool": 2}
    assert result["recovery_actions"] == 1
    assert result["mean_vlm_calls"] == 1.5


def test_evidence_metrics_reports_confirmation_gate() -> None:
    result = evidence_metrics([
        {"schema_valid": True, "timestamps_valid": True, "evidence_valid": True, "language_valid": True, "permits_confirmation": True},
        {"schema_valid": False, "timestamps_valid": False, "evidence_valid": False, "language_valid": True, "unsupported_critical_claim": True, "permits_confirmation": False},
    ])
    assert result["confirmation_rate"] == 0.5
    assert result["unsupported_critical_claims"] == 1
    assert result["valid_counts"]["schema_valid"] == 1


def test_candidate_metrics_reports_recall_fn_tiou_and_vlm_ratio() -> None:
    result = candidate_metrics([{
        "duration_seconds": 100,
        "ground_truth": [{"start_time": 10, "end_time": 20}, {"start_time": 50, "end_time": 60}],
        "candidates": [{"start_time": 10, "end_time": 20}, {"start_time": 70, "end_time": 80}],
    }])
    assert result["recall"] == 0.5
    assert result["false_negative_intervals"] == 1
    assert result["mean_tiou"] == 1.0
    assert result["vlm_time_ratio"] == 0.2
