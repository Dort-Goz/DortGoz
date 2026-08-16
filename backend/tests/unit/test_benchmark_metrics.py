from dortgoz.benchmark_metrics import (
    agent_metrics,
    agreement_rate,
    binary_cohens_kappa,
    candidate_metrics,
    e2e_metrics,
    event_has_valid_evidence,
    evidence_count,
    evidence_metrics,
    evidence_precision,
    evidence_set_recall,
    grounding_metrics,
    raw_binary_agreement,
    temporal_absolute_error,
    vlm_metrics,
)


def test_evidence_precision_and_event_valid_evidence() -> None:
    chosen = {1, 3}
    valid = {2, 3}
    assert evidence_precision(chosen, valid) == 0.5
    assert event_has_valid_evidence(chosen, valid) is True
    assert evidence_precision(set(), valid) == 0.0
    assert event_has_valid_evidence({1}, valid) is False


def test_evidence_count_and_diagnostic_set_recall() -> None:
    chosen = [1, 3]
    assert evidence_count(chosen) == 2
    assert evidence_set_recall(chosen, {1, 2, 3, 4}) == 0.5
    assert evidence_set_recall(chosen, set()) is None


def test_temporal_absolute_error_uses_peak_or_nearest_interval_boundary() -> None:
    assert temporal_absolute_error(12, gt_start=10, gt_peak=15, gt_end=20) == 3
    assert temporal_absolute_error(12, gt_start=10, gt_end=20) == 0
    assert temporal_absolute_error(7, gt_start=10, gt_end=20) == 3
    assert temporal_absolute_error(24, gt_start=10, gt_end=20) == 4
    assert temporal_absolute_error(None, gt_start=10, gt_end=20) is None


def test_temporal_absolute_error_rejects_invalid_ground_truth() -> None:
    import pytest

    with pytest.raises(ValueError, match="gt_end"):
        temporal_absolute_error(5, gt_start=10, gt_end=9)
    with pytest.raises(ValueError, match="gt_peak"):
        temporal_absolute_error(5, gt_start=10, gt_peak=30, gt_end=20)


def test_agreement_rate_is_paired_and_requires_equal_lengths() -> None:
    import pytest

    assert agreement_rate(["a", "b", "c"], ["a", "x", "c"]) == 2 / 3
    assert agreement_rate([], []) == 0.0
    with pytest.raises(ValueError, match="aynı uzunlukta"):
        agreement_rate(["a"], [])


def test_binary_annotator_raw_agreement_and_kappa() -> None:
    left = [True, True, False, False]
    right = [True, False, False, False]
    assert raw_binary_agreement(left, right) == 0.75
    result = binary_cohens_kappa(left, right)
    assert result["status"] == "defined"
    assert result["value"] == 0.5
    assert result["reason"] is None


def test_binary_kappa_is_typed_undefined_for_degenerate_distribution() -> None:
    assert raw_binary_agreement([True, True], [True, True]) == 1.0
    assert binary_cohens_kappa([True, True], [True, True]) == {
        "status": "undefined",
        "value": None,
        "reason": "DEGENERATE_CLASS_DISTRIBUTION",
    }


def test_grounding_metrics_reports_quality_guardrails_and_cost() -> None:
    result = grounding_metrics(
        [
            {
                "expected_event_type": "physical_fight",
                "contract_valid": True,
                "evidence_precision": 1.0,
                "event_has_valid_evidence": True,
                "evidence_count": 1,
                "evidence_set_recall": 0.5,
                "temporal_absolute_error": 2.0,
                "event_detected": True,
                "event_type_correct": True,
                "uncertain": False,
                "latency_ms": 100,
                "prompt_tokens": 200,
                "completion_tokens": 50,
            },
            {
                "expected_event_type": "physical_fight",
                "contract_valid": True,
                "evidence_precision": 0.0,
                "event_has_valid_evidence": False,
                "evidence_count": 3,
                "evidence_set_recall": 0.0,
                "temporal_absolute_error": 6.0,
                "event_detected": False,
                "event_type_correct": False,
                "uncertain": True,
                "latency_ms": 300,
                "prompt_tokens": 220,
                "completion_tokens": 70,
            },
            {
                "expected_event_type": "normal",
                "contract_valid": True,
                "normal_false_positive": True,
                "uncertain": False,
            },
        ]
    )
    assert result["evidence_frame_correctness"] == 0.5
    assert result["event_has_valid_evidence_rate"] == 0.5
    assert result["mean_evidence_count"] == 2.0
    assert result["mean_evidence_set_recall"] == 0.25
    assert result["median_temporal_absolute_error"] == 4.0
    assert result["event_recall"] == 0.5
    assert result["event_type_correctness"] == 0.5
    assert result["normal_false_positive_rate"] == 1.0
    assert result["uncertain_rate"] == 1 / 3
    assert result["mean_latency_ms"] == 200


def test_agent_metrics_counts_routes_tools_and_recovery() -> None:
    result = agent_metrics(
        [
            {
                "terminal_status": "confirmed",
                "vlm_attempts": 1,
                "decision_trace": [
                    {"action": "RUN_VLM", "tool_name": "vlm_tool"},
                    {"action": "CONFIRM_EVENT"},
                ],
            },
            {
                "terminal_status": "human_review",
                "vlm_attempts": 2,
                "decision_trace": [
                    {"action": "RETRY_VLM_STRICT", "tool_name": "vlm_tool"},
                    {"action": "REQUEST_HUMAN_REVIEW"},
                ],
            },
        ]
    )
    assert result["terminal_counts"] == {"confirmed": 1, "human_review": 1}
    assert result["tool_counts"] == {"vlm_tool": 2}
    assert result["recovery_actions"] == 1
    assert result["mean_vlm_calls"] == 1.5


def test_evidence_metrics_reports_confirmation_gate() -> None:
    result = evidence_metrics(
        [
            {
                "schema_valid": True,
                "timestamps_valid": True,
                "evidence_valid": True,
                "language_valid": True,
                "permits_confirmation": True,
            },
            {
                "schema_valid": False,
                "timestamps_valid": False,
                "evidence_valid": False,
                "language_valid": True,
                "unsupported_critical_claim": True,
                "permits_confirmation": False,
            },
        ]
    )
    assert result["confirmation_rate"] == 0.5
    assert result["unsupported_critical_claims"] == 1
    assert result["valid_counts"]["schema_valid"] == 1


def test_candidate_metrics_reports_recall_fn_tiou_and_vlm_ratio() -> None:
    result = candidate_metrics(
        [
            {
                "duration_seconds": 100,
                "ground_truth": [
                    {"start_time": 10, "end_time": 20},
                    {"start_time": 50, "end_time": 60},
                ],
                "candidates": [
                    {"start_time": 10, "end_time": 20},
                    {"start_time": 70, "end_time": 80},
                ],
            }
        ]
    )
    assert result["recall"] == 0.5
    assert result["false_negative_intervals"] == 1
    assert result["mean_tiou"] == 1.0
    assert result["vlm_time_ratio"] == 0.2


def test_vlm_metrics_reports_decision_time_evidence_and_latency() -> None:
    result = vlm_metrics(
        [
            {
                "expected_positive": True,
                "predicted_status": "confirmed",
                "expected_peak_time": 10,
                "predicted_peak_time": 12,
                "schema_valid": True,
                "evidence_valid": True,
                "duration_ms": 100,
            },
            {
                "expected_positive": False,
                "predicted_status": "confirmed",
                "schema_valid": True,
                "evidence_valid": False,
                "unsupported_critical_claim": True,
                "duration_ms": 300,
            },
            {
                "expected_positive": True,
                "predicted_status": "rejected",
                "schema_valid": False,
                "evidence_valid": False,
            },
        ]
    )
    assert (result["precision"], result["recall"], result["f1"]) == (0.5, 0.5, 0.5)
    assert result["peak_mae_seconds"] == 2
    assert result["mean_latency_ms"] == 200
    assert result["unsupported_critical_claim_rate"] == 1 / 3


def test_e2e_metrics_reports_critical_recall_false_alarm_and_resources() -> None:
    result = e2e_metrics(
        [
            {
                "expected_critical": True,
                "confirmed_critical": True,
                "latency_ms": 100,
                "ram_mb": 200,
                "vram_mb": 300,
            },
            {
                "expected_critical": True,
                "confirmed_critical": False,
                "latency_ms": 300,
                "ram_mb": 250,
                "vram_mb": 280,
            },
            {"is_normal": True, "duration_seconds": 1800, "false_alarm": True},
        ]
    )
    assert result["critical_recall"] == 0.5
    assert result["false_alarms_per_hour"] == 2
    assert result["mean_latency_ms"] == 200
    assert result["p95_latency_ms"] == 300
    assert result["peak_memory_mb"] == 300
    assert result["normal_seconds"] == 1800
    assert (result["max_ram_mb"], result["max_vram_mb"]) == (250, 300)
