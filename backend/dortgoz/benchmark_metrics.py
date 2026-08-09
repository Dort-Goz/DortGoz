"""JSONL benchmark artifact'larından saf agent ve evidence metrikleri."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from statistics import median
from typing import Any


def evidence_precision[T](chosen: Collection[T], valid: Collection[T]) -> float:
    """Seçilen kanıtların insan-doğrulamalı geçerli kümeye düşen oranı."""

    if not chosen:
        return 0.0
    return len(set(chosen) & set(valid)) / len(chosen)


def event_has_valid_evidence[T](chosen: Collection[T], valid: Collection[T]) -> bool:
    """Bir event'in en az bir insan-doğrulamalı kanıt karesi var mı?"""

    return bool(set(chosen) & set(valid))


def evidence_count[T](chosen: Collection[T]) -> int:
    """Modelin ürettiği evidence kayıtlarının sayısı."""

    return len(chosen)


def evidence_set_recall[T](chosen: Collection[T], valid: Collection[T]) -> float | None:
    """Geçerli evidence kümesinin ne kadarının seçildiğini diagnostic olarak ölçer.

    Geçerli küme boşsa oran matematiksel olarak tanımsızdır. Normal kontrollerde
    sahte bir sıfır veya bir üretmek yerine ``None`` döndürülür.
    """

    if not valid:
        return None
    return len(set(chosen) & set(valid)) / len(set(valid))


def temporal_absolute_error(
    predicted_timestamp: float | None,
    *,
    gt_start: float,
    gt_end: float,
    gt_peak: float | None = None,
) -> float | None:
    """Bir kanıt zamanının GT peak/interval'a mutlak uzaklığı.

    GT peak varsa mutlak peak hatası kullanılır. Yalnız interval varsa interval
    içindeki tahmin sıfır hata alır; dışarıdaki tahmin en yakın sınıra olan
    uzaklığı alır. Tahmin yoksa ölçüm tanımsızdır ve ``None`` döner.
    """

    if gt_end < gt_start:
        raise ValueError("gt_end, gt_start değerinden küçük olamaz")
    if gt_peak is not None and not gt_start <= gt_peak <= gt_end:
        raise ValueError("gt_peak, GT interval içinde olmalı")
    if predicted_timestamp is None:
        return None
    predicted = float(predicted_timestamp)
    if gt_peak is not None:
        return abs(predicted - gt_peak)
    if predicted < gt_start:
        return gt_start - predicted
    if predicted > gt_end:
        return predicted - gt_end
    return 0.0


def agreement_rate[T](left: Sequence[T], right: Sequence[T]) -> float:
    """Eşlenmiş iki sonuç dizisindeki exact-agreement oranı."""

    if len(left) != len(right):
        raise ValueError("agreement dizileri aynı uzunlukta olmalı")
    if not left:
        return 0.0
    return sum(a == b for a, b in zip(left, right, strict=True)) / len(left)


def raw_binary_agreement(left: Sequence[bool], right: Sequence[bool]) -> float | None:
    """İki annotator'ın eşlenmiş binary frame kararlarında ham anlaşması."""

    if len(left) != len(right):
        raise ValueError("annotator dizileri aynı uzunlukta olmalı")
    if not left:
        return None
    return sum(a is b for a, b in zip(left, right, strict=True)) / len(left)


def binary_cohens_kappa(
    left: Sequence[bool], right: Sequence[bool]
) -> dict[str, float | str | None]:
    """İki annotator için dependency'siz Cohen's kappa hesabı.

    Beklenen anlaşma 1 olduğunda payda sıfırdır. Bu degenerate class dağılımı
    typed ``undefined`` sonucu ile gösterilir; uydurma 0/1 üretilmez.
    """

    if len(left) != len(right):
        raise ValueError("annotator dizileri aynı uzunlukta olmalı")
    if not left:
        return {"status": "undefined", "value": None, "reason": "NO_PAIRED_ANNOTATIONS"}

    observed = raw_binary_agreement(left, right)
    assert observed is not None
    count = len(left)
    left_positive = sum(left) / count
    right_positive = sum(right) / count
    expected = left_positive * right_positive + (1 - left_positive) * (1 - right_positive)
    if expected == 1.0:
        return {
            "status": "undefined",
            "value": None,
            "reason": "DEGENERATE_CLASS_DISTRIBUTION",
        }
    return {
        "status": "defined",
        "value": (observed - expected) / (1 - expected),
        "reason": None,
    }


def grounding_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Phase B grounding satırlarından arm-seviyesi kalite ve maliyet özeti."""

    positives = [record for record in records if record.get("expected_event_type") != "normal"]
    normals = [record for record in records if record.get("expected_event_type") == "normal"]
    temporal_errors = [
        float(record["temporal_absolute_error"])
        for record in positives
        if record.get("temporal_absolute_error") is not None
    ]
    latencies = [
        float(record["latency_ms"]) for record in records if record.get("latency_ms") is not None
    ]
    prompt_tokens = [
        int(record["prompt_tokens"])
        for record in records
        if record.get("prompt_tokens") is not None
    ]
    completion_tokens = [
        int(record["completion_tokens"])
        for record in records
        if record.get("completion_tokens") is not None
    ]
    evidence_counts = [int(record.get("evidence_count", 0)) for record in positives]
    set_recalls = [
        float(record["evidence_set_recall"])
        for record in positives
        if record.get("evidence_set_recall") is not None
    ]

    def mean(values: Sequence[float | int]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "record_count": len(records),
        "contract_valid_rate": _rate(records, "contract_valid"),
        "evidence_frame_correctness": mean(
            [float(record.get("evidence_precision", 0.0)) for record in positives]
        ),
        "event_has_valid_evidence_rate": _rate(positives, "event_has_valid_evidence"),
        "mean_evidence_count": mean(evidence_counts),
        "mean_evidence_set_recall": mean(set_recalls),
        "median_temporal_absolute_error": median(temporal_errors) if temporal_errors else None,
        "event_recall": _rate(positives, "event_detected"),
        "event_type_correctness": _rate(positives, "event_type_correct"),
        "normal_false_positive_rate": _rate(normals, "normal_false_positive"),
        "uncertain_rate": _rate(records, "uncertain"),
        "mean_latency_ms": mean(latencies),
        "mean_prompt_tokens": mean(prompt_tokens),
        "mean_completion_tokens": mean(completion_tokens),
    }


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


def candidate_metrics(
    records: list[dict[str, Any]], *, tiou_threshold: float = 0.5
) -> dict[str, Any]:
    if not 0 < tiou_threshold <= 1:
        raise ValueError("tiou_threshold 0 ile 1 arasında olmalı")
    ground_truth = candidates = hits = 0
    matched_ious: list[float] = []
    video_seconds = candidate_seconds = 0.0
    for record in records:
        truths = [
            (float(item["start_time"]), float(item["end_time"]))
            for item in record.get("ground_truth", [])
        ]
        predicted = [
            (float(item["start_time"]), float(item["end_time"]))
            for item in record.get("candidates", [])
        ]
        used: set[int] = set()
        ground_truth += len(truths)
        candidates += len(predicted)
        video_seconds += float(record.get("duration_seconds", 0))
        candidate_seconds += sum(end - start for start, end in predicted)
        for truth in truths:
            choices = [
                (index, _tiou(truth, prediction))
                for index, prediction in enumerate(predicted)
                if index not in used
            ]
            if choices and (best := max(choices, key=lambda item: item[1]))[1] >= tiou_threshold:
                used.add(best[0])
                hits += 1
                matched_ious.append(best[1])
    return {
        "video_count": len(records),
        "ground_truth_count": ground_truth,
        "candidate_count": candidates,
        "true_positive_intervals": hits,
        "false_negative_intervals": ground_truth - hits,
        "recall": hits / ground_truth if ground_truth else 0.0,
        "mean_tiou": sum(matched_ious) / len(matched_ious) if matched_ious else 0.0,
        "candidates_per_hour": candidates / (video_seconds / 3600) if video_seconds else 0.0,
        "vlm_time_ratio": candidate_seconds / video_seconds if video_seconds else 0.0,
    }


def vlm_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Etiketli VLM doğrulama artifact'ından karar/kanıt/zaman KPI'ları."""

    tp = fp = fn = 0
    peak_errors: list[float] = []
    latencies: list[float] = []
    for record in records:
        expected = bool(record.get("expected_positive"))
        predicted = record.get("predicted_status") == "confirmed"
        if predicted and expected:
            tp += 1
        elif predicted:
            fp += 1
        elif expected:
            fn += 1
        if (
            record.get("expected_peak_time") is not None
            and record.get("predicted_peak_time") is not None
        ):
            peak_errors.append(
                abs(float(record["expected_peak_time"]) - float(record["predicted_peak_time"]))
            )
        if record.get("duration_ms") is not None:
            latencies.append(float(record["duration_ms"]))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "record_count": len(records),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "peak_mae_seconds": sum(peak_errors) / len(peak_errors) if peak_errors else None,
        "mean_latency_ms": sum(latencies) / len(latencies) if latencies else None,
        "valid_json_rate": _rate(records, "schema_valid"),
        "evidence_valid_rate": _rate(records, "evidence_valid"),
        "unsupported_critical_claim_rate": _rate(records, "unsupported_critical_claim"),
    }


def e2e_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Video seviyesindeki local run artifact'larından uçtan uca KPI'lar."""

    critical_total = critical_hit = false_alarms = 0
    normal_seconds = 0.0
    latencies: list[float] = []
    ram: list[float] = []
    vram: list[float] = []
    for record in records:
        expected_critical = bool(record.get("expected_critical"))
        confirmed_critical = bool(record.get("confirmed_critical"))
        if expected_critical:
            critical_total += 1
            critical_hit += confirmed_critical
        elif bool(record.get("false_alarm")):
            false_alarms += 1
        if bool(record.get("is_normal")):
            normal_seconds += float(record.get("duration_seconds", 0))
        for key, target in (("latency_ms", latencies), ("ram_mb", ram), ("vram_mb", vram)):
            if record.get(key) is not None:
                target.append(float(record[key]))
    return {
        "record_count": len(records),
        "critical_total": critical_total,
        "critical_hits": critical_hit,
        "critical_recall": critical_hit / critical_total if critical_total else 0.0,
        "false_alarms": false_alarms,
        "false_alarms_per_hour": false_alarms / (normal_seconds / 3600) if normal_seconds else 0.0,
        "mean_latency_ms": sum(latencies) / len(latencies) if latencies else None,
        "max_ram_mb": max(ram, default=None),
        "max_vram_mb": max(vram, default=None),
    }


def _tiou(left: tuple[float, float], right: tuple[float, float]) -> float:
    overlap = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return overlap / union if union else 0.0


def _rate(records: list[dict[str, Any]], key: str) -> float:
    return sum(bool(record.get(key)) for record in records) / len(records) if records else 0.0


__all__ = [
    "agent_metrics",
    "agreement_rate",
    "binary_cohens_kappa",
    "candidate_metrics",
    "e2e_metrics",
    "event_has_valid_evidence",
    "evidence_count",
    "evidence_metrics",
    "evidence_precision",
    "evidence_set_recall",
    "grounding_metrics",
    "raw_binary_agreement",
    "temporal_absolute_error",
    "vlm_metrics",
]
