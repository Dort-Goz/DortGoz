from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from dortgoz.events import EventEvidenceRef

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "bench"
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

from ab_evidence_grounding import (  # noqa: E402
    Arm,
    EvaluationStage,
    FramePayload,
    HarnessFailure,
    _base_row,
    annotator_agreement_report,
    apply_prediction_metrics,
    assert_fairness,
    build_plans,
    build_user_content,
    consistency_metrics,
    deterministic_order,
    dry_run_summary,
    main,
    normalize_evidence,
    parse_annotator_label,
    parse_prediction,
    parse_sample,
    schema_for_arm,
)


def sample_payload() -> dict:
    return {
        "sample_id": "fight-boundary-001",
        "evaluation_stage": "pilot",
        "video_id": "Fighting001_x264.mp4",
        "video_path": "media/Fighting001_x264.mp4",
        "source_label": "Fighting",
        "window_start": 30.0,
        "window_end": 60.0,
        "gt_event_type": "physical_fight",
        "gt_start": 42.0,
        "gt_peak": 48.0,
        "gt_end": 54.0,
        "selected_frames": [
            {"frame_index": 0, "timestamp": 34.0},
            {"frame_index": 1, "timestamp": 48.0},
            {"frame_index": 2, "timestamp": 56.0},
        ],
        "valid_evidence_frames": [1, {"timestamp": 48.0}],
        "boundary_near": True,
        "short_event": False,
        "visually_ambiguous": False,
        "notes": "Zirve ikinci seçilmiş karede.",
    }


def payloads() -> tuple[FramePayload, ...]:
    return tuple(
        FramePayload(
            frame_index=index,
            frame_id=f"f_{index:03d}",
            timestamp=timestamp,
            jpeg=f"jpeg-{index}".encode(),
            sha256=f"hash-{index}",
        )
        for index, timestamp in enumerate((34.0, 48.0, 56.0))
    )


def plans(*, repeats: int = 1, orders: tuple[str, ...] = ("original",)):
    sample = parse_sample(sample_payload())
    return build_plans(
        [sample],
        arms=[Arm.A, Arm.B, Arm.C],
        repeats=repeats,
        orders=orders,
        model_id="test-model",
        max_tokens=1400,
    )


def test_ordinal_mapping_uses_display_position_after_permutation() -> None:
    plan = next(plan for plan in plans(orders=("permuted",)) if plan.arm is Arm.A)
    data = {
        "events": [
            {"evidence": [{"image_index": 0, "claim": "İlk gösterilen karede kavga görülüyor."}]}
        ]
    }
    normalized = normalize_evidence(plan, data)
    assert normalized[0]["display_position"] == 0
    assert normalized[0]["frame_index"] == plan.order[0]
    assert normalized[0]["frame_id"] == f"f_{plan.order[0]:03d}"


def test_frame_id_mapping_is_stable_across_order() -> None:
    plan = next(plan for plan in plans(orders=("permuted",)) if plan.arm is Arm.B)
    data = {
        "events": [
            {"evidence": [{"frame_id": "f_001", "claim": "Bu karede fiziksel temas görülüyor."}]}
        ]
    }
    normalized = normalize_evidence(plan, data)
    assert normalized[0]["frame_index"] == 1
    assert normalized[0]["timestamp"] == 48.0
    assert normalized[0]["display_position"] == plan.order.index(1)


def test_c_output_uses_frame_id_without_requiring_model_timestamp() -> None:
    plan = next(plan for plan in plans() if plan.arm is Arm.C)
    data = {
        "events": [
            {
                "evidence": [
                    {
                        "frame_id": "f_001",
                        "claim": "Karede iki kişi arasında fiziksel temas var.",
                    }
                ]
            }
        ]
    }
    assert normalize_evidence(plan, data)[0]["timestamp"] == 48.0
    schema = schema_for_arm(Arm.C)
    branches = schema["oneOf"] if "oneOf" in schema else [schema]
    attention = next(branch for branch in branches if "events" in branch.get("properties", {}))
    evidence_schema = attention["properties"]["events"]["items"]["properties"]["evidence"]["items"]
    assert evidence_schema["required"] == ["frame_id", "claim"]
    assert "timestamp" not in evidence_schema["properties"]


def test_permutation_is_reproducible_non_identity_and_sample_stable() -> None:
    sample = parse_sample(sample_payload())
    first = deterministic_order(sample, "permuted", seed=42)
    second = deterministic_order(sample, "permuted", seed=42)
    assert first == second
    assert first != deterministic_order(sample, "original", seed=42)
    assert sorted(first) == [0, 1, 2]


def test_fairness_invariant_accepts_arms_and_rejects_token_budget_drift() -> None:
    valid = plans()
    assert_fairness(valid)
    changed = [*valid]
    changed[-1] = replace(changed[-1], max_tokens=999)
    with pytest.raises(HarnessFailure) as exc:
        assert_fairness(changed)
    assert exc.value.code == "FAIRNESS_INVARIANT_VIOLATION"


def test_fairness_rejects_extra_evidence_semantic_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ab_evidence_grounding as harness

    original = harness._evidence_schema

    def drifted_schema(arm: Arm) -> dict:
        schema = original(arm)
        if arm is Arm.C:
            schema["properties"]["accidental_field"] = {"type": "string"}
            schema["required"].append("accidental_field")
        return schema

    monkeypatch.setattr(harness, "_evidence_schema", drifted_schema)
    with pytest.raises(HarnessFailure) as exc:
        assert_fairness(plans())
    assert exc.value.code == "FAIRNESS_INVARIANT_VIOLATION"


def test_arm_content_reuses_exact_jpeg_bytes_and_only_labels_b_and_c() -> None:
    arm_plans = plans()
    contents = {plan.arm: build_user_content(plan, payloads()) for plan in arm_plans}
    image_urls = {
        arm: [part["image_url"]["url"] for part in content if part["type"] == "image_url"]
        for arm, content in contents.items()
    }
    assert image_urls[Arm.A] == image_urls[Arm.B] == image_urls[Arm.C]
    a_text = [part["text"] for part in contents[Arm.A] if part["type"] == "text"]
    b_text = [part["text"] for part in contents[Arm.B] if part["type"] == "text"]
    c_text = [part["text"] for part in contents[Arm.C] if part["type"] == "text"]
    assert not any("FRAME_ID:" in text for text in a_text)
    assert any(text == "FRAME_ID: f_000" for text in b_text)
    assert any("VIDEO_TIMESTAMP_SECONDS: 34.000" in text for text in c_text)


def test_malformed_annotation_reports_typed_failure(tmp_path: Path) -> None:
    from ab_evidence_grounding import load_samples

    path = tmp_path / "bad.jsonl"
    path.write_text('{"sample_id":', encoding="utf-8")
    with pytest.raises(HarnessFailure) as exc:
        load_samples(path)
    assert exc.value.code == "INVALID_ANNOTATION"


@pytest.mark.parametrize(
    ("stage", "expected"), [("pilot", EvaluationStage.PILOT), ("final", EvaluationStage.FINAL)]
)
def test_evaluation_stage_parses_pilot_and_final(stage: str, expected: EvaluationStage) -> None:
    raw = sample_payload()
    raw["evaluation_stage"] = stage
    assert parse_sample(raw).evaluation_stage is expected


def test_missing_frame_mapping_reports_typed_failure() -> None:
    raw = sample_payload()
    raw["selected_frames"][1]["frame_index"] = 7
    with pytest.raises(HarnessFailure) as exc:
        parse_sample(raw)
    assert exc.value.code == "MISSING_FRAME_MAPPING"


def test_dry_run_call_count_and_source_label_preservation(tmp_path: Path) -> None:
    all_plans = plans(repeats=3, orders=("original", "permuted"))
    sample = all_plans[0].sample
    summary = dry_run_summary(
        [sample], all_plans, output_path=tmp_path / "result.jsonl", seed=20260809
    )
    assert summary["expected_call_count"] == 18
    assert summary["model_calls_made"] == 0
    assert summary["GROUNDING_EVALUATION_CONDITION"] == "at_least_one_valid_selected_frame"
    assert summary["permutation_scope"] == "single_controlled_order_perturbation_sensitivity"
    assert summary["contracts"] == {
        "production": ["frame_id", "timestamp", "claim"],
        "benchmark_normalized": {
            "A": ["image_index", "claim"],
            "B": ["frame_id", "claim"],
            "C": ["frame_id", "claim"],
        },
        "production_contract_changed": False,
    }
    assert summary["frame_mappings"][sample.sample_id]["source_label"] == "Fighting"
    assert not (tmp_path / "result.jsonl").exists()


def test_keyframe_failure_is_retained_but_excluded_with_typed_reason(tmp_path: Path) -> None:
    raw = sample_payload()
    raw["sample_id"] = "keyframe-failure-001"
    raw["valid_evidence_frames"] = []
    sample = parse_sample(raw)
    excluded_plans = build_plans(
        [sample],
        arms=[Arm.A, Arm.B, Arm.C],
        repeats=1,
        orders=["original", "permuted"],
        model_id="test-model",
        max_tokens=1400,
    )
    summary = dry_run_summary(
        [sample], excluded_plans, output_path=tmp_path / "result.jsonl", seed=20260809
    )
    assert excluded_plans == []
    assert summary["sample_count"] == 1
    assert summary["eligible_grounding_sample_count"] == 0
    assert summary["excluded_keyframe_failure_count"] == 1
    assert summary["excluded_samples"] == [
        {
            "sample_id": "keyframe-failure-001",
            "evaluation_stage": "pilot",
            "reason": "EXCLUDED_KEYFRAME_FAILURE",
        }
    ]


def test_cli_dry_run_parses_sample_without_creating_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    annotation = tmp_path / "samples.jsonl"
    annotation.write_text(json.dumps(sample_payload()) + "\n", encoding="utf-8")
    output = tmp_path / "result.jsonl"
    exit_code = main(
        [
            "--samples",
            str(annotation),
            "--arms",
            "A",
            "B",
            "C",
            "--orders",
            "original",
            "permuted",
            "--repeats",
            "3",
            "--out",
            str(output),
            "--dry-run",
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert summary["expected_call_count"] == 18
    assert summary["model_calls_made"] == 0
    assert not output.exists()


def test_output_row_preserves_source_label_and_required_fields() -> None:
    plan = next(plan for plan in plans() if plan.arm is Arm.C)
    row = _base_row(plan, payloads(), experiment_id="exp-1", code_commit="abc123")
    assert row["source_label"] == "Fighting"
    assert row["grounding_representation"] == "explicit_frame_id_with_input_timestamp"
    assert row["evaluation_stage"] == "pilot"
    assert row["prompt_hash"]
    assert row["selected_frames"][1]["sha256"] == "hash-1"


def test_prediction_metrics_use_canonical_evidence_mapping() -> None:
    plan = next(plan for plan in plans() if plan.arm is Arm.C)
    row = _base_row(plan, payloads(), experiment_id="exp-1", code_commit="abc123")
    raw = json.dumps(
        {
            "summary": "İki kişi kavga ediyor.",
            "durum": "dikkat",
            "events": [
                {
                    "t": 48.0,
                    "desc": "Fiziksel kavga",
                    "evidence": [
                        {
                            "frame_id": "f_001",
                            "claim": "İki kişi arasında fiziksel temas görülüyor.",
                        }
                    ],
                    "severity_hint": "orta",
                    "event_type": "physical_fight",
                }
            ],
            "uncertainties": [],
            "anomaly_type": "physical_fight",
        }
    )
    parse_prediction(plan, raw, row)
    assert row["contract_valid"] is True
    assert row["evidence_precision"] == 1.0
    assert row["event_has_valid_evidence"] is True
    assert row["evidence_count"] == 1
    assert row["evidence_set_recall"] == 1.0
    assert row["temporal_absolute_error"] == 0.0
    assert row["event_type_correct"] is True


def test_all_arms_map_canonical_frame_to_same_temporal_metric_path() -> None:
    arm_plans = {plan.arm: plan for plan in plans()}
    evidence_by_arm = {
        Arm.A: {"image_index": 1, "claim": "Fiziksel temas açıkça görülüyor."},
        Arm.B: {"frame_id": "f_001", "claim": "Fiziksel temas açıkça görülüyor."},
        Arm.C: {"frame_id": "f_001", "claim": "Fiziksel temas açıkça görülüyor."},
    }
    metrics = []
    for arm, plan in arm_plans.items():
        row = {
            "predicted_evidence": normalize_evidence(
                plan, {"events": [{"evidence": [evidence_by_arm[arm]]}]}
            )
        }
        apply_prediction_metrics(row, plan.sample)
        metrics.append(
            (row["temporal_absolute_error"], row["evidence_precision"], row["evidence_count"])
        )
    assert metrics == [(0.0, 1.0, 1), (0.0, 1.0, 1), (0.0, 1.0, 1)]


def test_production_event_evidence_contract_remains_unchanged() -> None:
    schema = EventEvidenceRef.model_json_schema()
    assert list(schema["properties"]) == ["frame_id", "timestamp", "claim"]
    assert schema["required"] == ["frame_id", "timestamp", "claim"]
    assert schema["additionalProperties"] is False


def test_anonymous_annotator_agreement_is_separate_from_adjudication() -> None:
    sample = parse_sample(sample_payload())
    labels = [
        parse_annotator_label(
            {
                "sample_id": sample.sample_id,
                "frame_index": frame_index,
                "annotator_slot": slot,
                "is_valid_evidence": decision,
            }
        )
        for frame_index, slot, decision in [
            (0, "ann_1", False),
            (0, "ann_2", False),
            (1, "ann_1", True),
            (1, "ann_2", True),
            (2, "ann_1", False),
            (2, "ann_2", True),
        ]
    ]
    report = annotator_agreement_report(labels, [sample])
    assert report["paired_frame_count"] == 3
    assert report["raw_agreement"] == 2 / 3
    assert report["cohens_kappa"]["status"] == "defined"
    assert report["adjudicated_canonical_annotation"] == "separate_sample_jsonl"
    assert report["contains_personal_identifiers"] is False


def test_consistency_metrics_compare_orders_and_repeats() -> None:
    records = []
    for order in ("original", "permuted"):
        for repeat_number in (1, 2, 3):
            records.append(
                {
                    "sample_id": "s1",
                    "arm": "B",
                    "repeat": repeat_number,
                    "permutation_id": order,
                    "contract_valid": True,
                    "predicted_event_type": "physical_fight",
                    "predicted_evidence": [{"frame_index": 1, "timestamp": 48.0}],
                }
            )
    result = consistency_metrics(records)
    assert result == {
        "event_type_consistency": 1.0,
        "evidence_frame_consistency": 1.0,
        "temporal_consistency": 1.0,
        "same_event_agreement": 1.0,
        "same_evidence_agreement": 1.0,
    }
