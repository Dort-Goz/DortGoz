"""Phase B evidence-grounding A/B/C benchmark harness'ı.

Bu dosya production pipeline'ını değiştirmez. Aynı seçilmiş JPEG payload'larını
üç grounding gösterimiyle yerel Qwen'e gönderir:

* A: ordinal ``image_index``
* B: explicit ``frame_id``
* C: explicit ``frame_id`` + input-only video timestamp

Önce yalnız planı doğrulamak için ``--dry-run`` kullanın. Model çağrısı güvenlik
amacıyla ancak açık ``--execute`` bayrağıyla yapılır.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dortgoz.agent.llm import call_stats, create_chat, main_client  # noqa: E402
from dortgoz.benchmark_metrics import (  # noqa: E402
    agreement_rate,
    binary_cohens_kappa,
    event_has_valid_evidence,
    evidence_count,
    evidence_precision,
    evidence_set_recall,
    grounding_metrics,
    raw_binary_agreement,
    temporal_absolute_error,
)
from dortgoz.config import settings  # noqa: E402
from dortgoz.domain.taxonomy import CanonicalEventType  # noqa: E402
from dortgoz.pipeline.ingest import grab_frame  # noqa: E402
from dortgoz.pipeline.interpret import (  # noqa: E402
    FRAME_TIMESTAMP_TOLERANCE_SECONDS,
    SYSTEM_TR,
    TASK_TR,
    TIER_TR,
    report_schema,
    tier_schema,
)

DEFAULT_SEED = 20260809
TEMPERATURE = 0
GROUNDING_EVALUATION_CONDITION = "at_least_one_valid_selected_frame"
EXCLUDED_KEYFRAME_FAILURE = "EXCLUDED_KEYFRAME_FAILURE"
PERMUTATION_SCOPE = "single_controlled_order_perturbation_sensitivity"
PRODUCTION_EVIDENCE_CONTRACT = ("frame_id", "claim")  # B-biçimi (2026-08-11):
# timestamp model-facing şemadan çıkarıldı; uygulama frame_id→timestamp doldurur.
BENCHMARK_EVIDENCE_CONTRACTS = {
    "A": ("image_index", "claim"),
    "B": ("frame_id", "claim"),
    "C": ("frame_id", "claim"),
}
GROUNDING_REPRESENTATIONS = {
    "A": "ordinal_image_index",
    "B": "explicit_frame_id",
    "C": "explicit_frame_id_with_input_timestamp",
}
PREREGISTERED_CRITERIA = {
    "evidence_correctness_gain_pp_vs_a": 5.0,
    "temporal_median_error_relative_improvement_vs_a": 0.10,
    "maximum_event_recall_degradation_pp": 2.0,
    "latency_increase_tradeoff_threshold": 0.10,
    "timestamp_requires_incremental_gain_over_b": True,
}
FAIRNESS_INVARIANTS = (
    "same_image_count",
    "same_exact_selected_timestamps",
    "same_image_source_or_bytes",
    "same_order_within_permutation",
    "same_model",
    "same_token_budget",
    "same_temperature_and_sampling",
    "same_system_semantics_event_task_and_taxonomy",
    "same_event_output_semantics",
    "same_number_of_evidence_semantic_fields",
)

_C_SYSTEM_CLAUSE = (
    "Her olayın `evidence` alanında yalnızca sana verilen FRAME_ID değerlerini kullan; "
    "yeni kare kimliği uydurma."
)
_ARM_SYSTEM_CLAUSE = {
    "A": (
        "Her olayın `evidence` alanında yalnızca görüntülerin gösterim sırasını belirten "
        "image_index değerlerini kullan; ilk görüntü 0'dır ve yeni index uydurma."
    ),
    "B": (
        "Her olayın `evidence` alanında yalnızca sana verilen FRAME_ID değerlerini kullan; "
        "yeni kare kimliği uydurma."
    ),
    "C": _C_SYSTEM_CLAUSE,
}
_PRODUCTION_TASK_CLAUSE = (
    "Her evidence kaydında yalnız ilgili karenin FRAME_ID değerini aynen kullan."
)
_NORMALIZED_FRAME_TASK_CLAUSE = (
    "Her evidence kaydında yalnız ilgili karenin FRAME_ID değerini aynen kullan."
)
_ARM_TASK_CLAUSE = {
    "A": (
        "Her evidence kaydında yalnız ilgili görüntünün gösterim sırasındaki image_index "
        "değerini kullan; ilk görüntü 0'dır."
    ),
    "B": _NORMALIZED_FRAME_TASK_CLAUSE,
    "C": _NORMALIZED_FRAME_TASK_CLAUSE,
}


class Arm(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class EvaluationStage(StrEnum):
    PILOT = "pilot"
    FINAL = "final"


class HarnessFailure(ValueError):
    """Benchmark planı/kontratı için typed, fail-closed hata."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SelectedFrame:
    frame_index: int
    timestamp: float
    image_path: str | None = None

    @property
    def frame_id(self) -> str:
        return f"f_{self.frame_index:03d}"


@dataclass(frozen=True)
class EvidenceSample:
    sample_id: str
    video_id: str
    video_path: str | None
    window_start: float
    window_end: float
    gt_event_type: str
    gt_start: float | None
    gt_peak: float | None
    gt_end: float | None
    selected_frames: tuple[SelectedFrame, ...]
    valid_evidence_frames: frozenset[int]
    evaluation_stage: EvaluationStage
    boundary_near: bool
    short_event: bool
    visually_ambiguous: bool
    notes: str
    source_label: str | None = None

    @property
    def grounding_evaluation_eligible(self) -> bool:
        return self.gt_event_type == CanonicalEventType.NORMAL.value or bool(
            self.valid_evidence_frames
        )

    @property
    def grounding_exclusion_reason(self) -> str | None:
        return None if self.grounding_evaluation_eligible else EXCLUDED_KEYFRAME_FAILURE


@dataclass(frozen=True)
class ExperimentPlan:
    sample: EvidenceSample
    arm: Arm
    permutation_id: str
    repeat: int
    order: tuple[int, ...]
    model_id: str
    max_tokens: int
    temperature: int = TEMPERATURE

    @property
    def grounding_representation(self) -> str:
        return GROUNDING_REPRESENTATIONS[self.arm.value]


@dataclass(frozen=True)
class FramePayload:
    frame_index: int
    frame_id: str
    timestamp: float
    jpeg: bytes
    sha256: str


@dataclass(frozen=True)
class AnnotatorLabel:
    sample_id: str
    frame_index: int
    annotator_slot: str
    is_valid_evidence: bool


def _failure(code: str, message: str, *, line_number: int | None = None) -> HarnessFailure:
    prefix = f"annotation line {line_number}: " if line_number is not None else ""
    return HarnessFailure(code, prefix + message)


def _as_nonempty_string(payload: dict[str, Any], key: str, *, line_number: int | None) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _failure(
            "INVALID_ANNOTATION", f"{key} boş olmayan string olmalı", line_number=line_number
        )
    return value.strip()


def _as_float(
    payload: dict[str, Any],
    key: str,
    *,
    line_number: int | None,
    optional: bool = False,
) -> float | None:
    value = payload.get(key)
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _failure("INVALID_ANNOTATION", f"{key} sayı olmalı", line_number=line_number)
    result = float(value)
    if result < 0 or result != result or result in {float("inf"), float("-inf")}:
        raise _failure(
            "INVALID_ANNOTATION",
            f"{key} sonlu ve negatif olmayan sayı olmalı",
            line_number=line_number,
        )
    return result


def _as_bool(payload: dict[str, Any], key: str, *, line_number: int | None) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise _failure("INVALID_ANNOTATION", f"{key} boolean olmalı", line_number=line_number)
    return value


def _parse_selected_frames(
    payload: dict[str, Any], *, line_number: int | None
) -> tuple[SelectedFrame, ...]:
    raw_frames = payload.get("selected_frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise _failure(
            "INVALID_ANNOTATION",
            "selected_frames boş olmayan liste olmalı",
            line_number=line_number,
        )
    frames: list[SelectedFrame] = []
    for position, raw in enumerate(raw_frames):
        if not isinstance(raw, dict):
            raise _failure(
                "INVALID_ANNOTATION",
                f"selected_frames[{position}] object olmalı",
                line_number=line_number,
            )
        frame_index = raw.get("frame_index")
        if isinstance(frame_index, bool) or not isinstance(frame_index, int):
            raise _failure(
                "INVALID_ANNOTATION",
                f"selected_frames[{position}].frame_index integer olmalı",
                line_number=line_number,
            )
        timestamp = _as_float(raw, "timestamp", line_number=line_number)
        image_path = raw.get("image_path")
        if image_path is not None and (not isinstance(image_path, str) or not image_path.strip()):
            raise _failure(
                "INVALID_ANNOTATION",
                f"selected_frames[{position}].image_path string olmalı",
                line_number=line_number,
            )
        frames.append(
            SelectedFrame(
                frame_index=frame_index,
                timestamp=float(timestamp),
                image_path=image_path.strip() if isinstance(image_path, str) else None,
            )
        )
    expected = list(range(len(frames)))
    actual = [frame.frame_index for frame in frames]
    if actual != expected:
        raise _failure(
            "MISSING_FRAME_MAPPING",
            f"selected frame_index değerleri sıralı {expected} olmalı; alınan {actual}",
            line_number=line_number,
        )
    timestamps = [frame.timestamp for frame in frames]
    if len(set(timestamps)) != len(timestamps):
        raise _failure(
            "INVALID_ANNOTATION",
            "selected frame timestamp değerleri unique olmalı",
            line_number=line_number,
        )
    if timestamps != sorted(timestamps):
        raise _failure(
            "INVALID_ANNOTATION",
            "selected_frames canonical sırası kronolojik olmalı",
            line_number=line_number,
        )
    return tuple(frames)


def _valid_frame_index(
    raw: Any,
    frames: tuple[SelectedFrame, ...],
    *,
    line_number: int | None,
) -> int:
    if isinstance(raw, dict):
        has_index = "frame_index" in raw
        has_timestamp = "timestamp" in raw
        if not has_index and not has_timestamp:
            raise _failure(
                "MISSING_FRAME_MAPPING",
                "valid evidence object frame_index veya timestamp içermeli",
                line_number=line_number,
            )
        if has_index:
            frame_index = _valid_frame_index(raw["frame_index"], frames, line_number=line_number)
            if has_timestamp:
                try:
                    timestamp = float(raw["timestamp"])
                except (TypeError, ValueError) as exc:
                    raise _failure(
                        "MISSING_FRAME_MAPPING",
                        "valid evidence timestamp sayı olmalı",
                        line_number=line_number,
                    ) from exc
                timestamp_index = _valid_frame_index(timestamp, frames, line_number=line_number)
                if timestamp_index != frame_index:
                    raise _failure(
                        "MISSING_FRAME_MAPPING",
                        "valid evidence frame_index ve timestamp aynı kareyi göstermiyor",
                        line_number=line_number,
                    )
            return frame_index
        try:
            raw = float(raw["timestamp"])
        except (TypeError, ValueError) as exc:
            raise _failure(
                "MISSING_FRAME_MAPPING",
                "valid evidence timestamp sayı olmalı",
                line_number=line_number,
            ) from exc
    if isinstance(raw, bool):
        raise _failure(
            "MISSING_FRAME_MAPPING", "boolean valid frame olamaz", line_number=line_number
        )
    if isinstance(raw, int):
        if not 0 <= raw < len(frames):
            raise _failure(
                "MISSING_FRAME_MAPPING",
                f"valid frame_index selected_frames dışında: {raw}",
                line_number=line_number,
            )
        return raw
    if isinstance(raw, float):
        matches = [
            frame.frame_index
            for frame in frames
            if abs(frame.timestamp - raw) <= FRAME_TIMESTAMP_TOLERANCE_SECONDS
        ]
        if len(matches) != 1:
            raise _failure(
                "MISSING_FRAME_MAPPING",
                f"valid evidence timestamp selected frame'e map edilemedi: {raw}",
                line_number=line_number,
            )
        return matches[0]
    raise _failure(
        "MISSING_FRAME_MAPPING",
        "valid_evidence_frames elemanı index, timestamp veya mapping object olmalı",
        line_number=line_number,
    )


def parse_sample(payload: dict[str, Any], *, line_number: int | None = None) -> EvidenceSample:
    """Tek JSON annotation satırını doğrular ve canonical frame mapping kurar."""

    if not isinstance(payload, dict):
        raise _failure(
            "INVALID_ANNOTATION", "annotation satırı object olmalı", line_number=line_number
        )
    sample_id = _as_nonempty_string(payload, "sample_id", line_number=line_number)
    video_id = _as_nonempty_string(payload, "video_id", line_number=line_number)
    raw_evaluation_stage = _as_nonempty_string(payload, "evaluation_stage", line_number=line_number)
    try:
        evaluation_stage = EvaluationStage(raw_evaluation_stage)
    except ValueError as exc:
        raise _failure(
            "INVALID_ANNOTATION",
            "evaluation_stage yalnız pilot veya final olabilir",
            line_number=line_number,
        ) from exc
    window_start = float(_as_float(payload, "window_start", line_number=line_number))
    window_end = float(_as_float(payload, "window_end", line_number=line_number))
    if window_end <= window_start:
        raise _failure(
            "INVALID_ANNOTATION",
            "window_end, window_start'tan büyük olmalı",
            line_number=line_number,
        )
    gt_event_type = _as_nonempty_string(payload, "gt_event_type", line_number=line_number)
    try:
        canonical_type = CanonicalEventType(gt_event_type)
    except ValueError as exc:
        raise _failure(
            "INVALID_ANNOTATION",
            f"gt_event_type canonical değil: {gt_event_type}",
            line_number=line_number,
        ) from exc

    frames = _parse_selected_frames(payload, line_number=line_number)
    if any(not window_start <= frame.timestamp <= window_end for frame in frames):
        raise _failure(
            "INVALID_ANNOTATION",
            "selected frame timestamp window sınırları içinde olmalı",
            line_number=line_number,
        )
    raw_valid = payload.get("valid_evidence_frames")
    if not isinstance(raw_valid, list):
        raise _failure(
            "INVALID_ANNOTATION",
            "valid_evidence_frames liste olmalı",
            line_number=line_number,
        )
    valid_frames = frozenset(
        _valid_frame_index(item, frames, line_number=line_number) for item in raw_valid
    )

    normal = canonical_type is CanonicalEventType.NORMAL
    gt_start = _as_float(payload, "gt_start", line_number=line_number, optional=normal)
    gt_peak = _as_float(payload, "gt_peak", line_number=line_number, optional=True)
    gt_end = _as_float(payload, "gt_end", line_number=line_number, optional=normal)
    if normal:
        if valid_frames:
            raise _failure(
                "INVALID_ANNOTATION",
                "normal sample valid_evidence_frames içermemeli",
                line_number=line_number,
            )
    else:
        if gt_start is None or gt_end is None:
            raise _failure(
                "INVALID_ANNOTATION",
                "non-normal sample gt_start ve gt_end içermeli",
                line_number=line_number,
            )
        if gt_end < gt_start:
            raise _failure(
                "INVALID_ANNOTATION", "gt_end, gt_start'tan küçük", line_number=line_number
            )
        if gt_peak is not None and not gt_start <= gt_peak <= gt_end:
            raise _failure(
                "INVALID_ANNOTATION",
                "gt_peak GT interval içinde olmalı",
                line_number=line_number,
            )
        if gt_end < window_start or gt_start > window_end:
            raise _failure(
                "INVALID_ANNOTATION",
                "GT interval sample window ile kesişmeli",
                line_number=line_number,
            )

    video_path = payload.get("video_path")
    if video_path is not None and (not isinstance(video_path, str) or not video_path.strip()):
        raise _failure("INVALID_ANNOTATION", "video_path string olmalı", line_number=line_number)
    source_label = payload.get("source_label")
    if source_label is not None and (not isinstance(source_label, str) or not source_label.strip()):
        raise _failure("INVALID_ANNOTATION", "source_label string olmalı", line_number=line_number)
    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        raise _failure("INVALID_ANNOTATION", "notes string olmalı", line_number=line_number)

    return EvidenceSample(
        sample_id=sample_id,
        video_id=video_id,
        video_path=video_path.strip() if isinstance(video_path, str) else None,
        window_start=window_start,
        window_end=window_end,
        gt_event_type=canonical_type.value,
        gt_start=float(gt_start) if gt_start is not None else None,
        gt_peak=float(gt_peak) if gt_peak is not None else None,
        gt_end=float(gt_end) if gt_end is not None else None,
        selected_frames=frames,
        valid_evidence_frames=valid_frames,
        evaluation_stage=evaluation_stage,
        boundary_near=_as_bool(payload, "boundary_near", line_number=line_number),
        short_event=_as_bool(payload, "short_event", line_number=line_number),
        visually_ambiguous=_as_bool(payload, "visually_ambiguous", line_number=line_number),
        notes=notes,
        source_label=source_label.strip() if isinstance(source_label, str) else None,
    )


def load_samples(path: Path) -> list[EvidenceSample]:
    """JSONL annotation dosyasını typed ve duplicate-safe biçimde okur."""

    if not path.is_file():
        raise HarnessFailure("ANNOTATION_FILE_NOT_FOUND", f"annotation bulunamadı: {path}")
    samples: list[EvidenceSample] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _failure(
                "INVALID_ANNOTATION", f"geçersiz JSON: {exc.msg}", line_number=line_number
            ) from exc
        sample = parse_sample(payload, line_number=line_number)
        if sample.sample_id in seen:
            raise _failure(
                "INVALID_ANNOTATION",
                f"duplicate sample_id: {sample.sample_id}",
                line_number=line_number,
            )
        seen.add(sample.sample_id)
        samples.append(sample)
    if not samples:
        raise HarnessFailure("INVALID_ANNOTATION", "annotation dosyası boş")
    return samples


def parse_annotator_label(
    payload: dict[str, Any], *, line_number: int | None = None
) -> AnnotatorLabel:
    """Kişisel veri taşımayan ayrı binary per-frame annotation satırını doğrular."""

    allowed_fields = {"sample_id", "frame_index", "annotator_slot", "is_valid_evidence"}
    if not isinstance(payload, dict) or set(payload) != allowed_fields:
        raise _failure(
            "INVALID_ANNOTATOR_ANNOTATION",
            "annotator satırı yalnız sample_id, frame_index, annotator_slot ve "
            "is_valid_evidence alanlarını içermeli",
            line_number=line_number,
        )
    sample_id = _as_nonempty_string(payload, "sample_id", line_number=line_number)
    frame_index = payload["frame_index"]
    if isinstance(frame_index, bool) or not isinstance(frame_index, int) or frame_index < 0:
        raise _failure(
            "INVALID_ANNOTATOR_ANNOTATION",
            "frame_index negatif olmayan integer olmalı",
            line_number=line_number,
        )
    annotator_slot = _as_nonempty_string(payload, "annotator_slot", line_number=line_number)
    if annotator_slot not in {"ann_1", "ann_2"}:
        raise _failure(
            "INVALID_ANNOTATOR_ANNOTATION",
            "annotator_slot yalnız ann_1 veya ann_2 olabilir",
            line_number=line_number,
        )
    is_valid_evidence = payload["is_valid_evidence"]
    if not isinstance(is_valid_evidence, bool):
        raise _failure(
            "INVALID_ANNOTATOR_ANNOTATION",
            "is_valid_evidence boolean olmalı",
            line_number=line_number,
        )
    return AnnotatorLabel(
        sample_id=sample_id,
        frame_index=frame_index,
        annotator_slot=annotator_slot,
        is_valid_evidence=is_valid_evidence,
    )


def load_annotator_labels(path: Path) -> list[AnnotatorLabel]:
    """Anonim annotator JSONL dosyasını duplicate-safe biçimde okur."""

    if not path.is_file():
        raise HarnessFailure("ANNOTATOR_FILE_NOT_FOUND", f"annotator kaydı bulunamadı: {path}")
    labels: list[AnnotatorLabel] = []
    seen: set[tuple[str, int, str]] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _failure(
                "INVALID_ANNOTATOR_ANNOTATION",
                f"geçersiz JSON: {exc.msg}",
                line_number=line_number,
            ) from exc
        label = parse_annotator_label(payload, line_number=line_number)
        key = (label.sample_id, label.frame_index, label.annotator_slot)
        if key in seen:
            raise _failure(
                "INVALID_ANNOTATOR_ANNOTATION",
                f"duplicate annotator kararı: {key}",
                line_number=line_number,
            )
        seen.add(key)
        labels.append(label)
    if not labels:
        raise HarnessFailure("INVALID_ANNOTATOR_ANNOTATION", "annotator dosyası boş")
    return labels


def annotator_agreement_report(
    labels: Sequence[AnnotatorLabel], samples: Sequence[EvidenceSample]
) -> dict[str, Any]:
    """Tam ann_1/ann_2 frame çiftlerinde raw agreement ve kappa üretir."""

    valid_units = {
        (sample.sample_id, frame.frame_index)
        for sample in samples
        for frame in sample.selected_frames
    }
    decisions: dict[tuple[str, int], dict[str, bool]] = defaultdict(dict)
    for label in labels:
        unit = (label.sample_id, label.frame_index)
        if unit not in valid_units:
            raise HarnessFailure(
                "INVALID_ANNOTATOR_ANNOTATION",
                f"annotator kararı canonical selected frame'e bağlı değil: {unit}",
            )
        if label.annotator_slot in decisions[unit]:
            raise HarnessFailure(
                "INVALID_ANNOTATOR_ANNOTATION",
                f"duplicate annotator kararı: {unit}/{label.annotator_slot}",
            )
        decisions[unit][label.annotator_slot] = label.is_valid_evidence

    paired = [
        (unit, slots)
        for unit, slots in sorted(decisions.items())
        if set(slots) == {"ann_1", "ann_2"}
    ]
    left = [slots["ann_1"] for _, slots in paired]
    right = [slots["ann_2"] for _, slots in paired]
    return {
        "annotation_unit": "binary_per_selected_frame",
        "annotator_slots": ["ann_1", "ann_2"],
        "paired_frame_count": len(paired),
        "incomplete_frame_count": len(decisions) - len(paired),
        "raw_agreement": raw_binary_agreement(left, right),
        "cohens_kappa": binary_cohens_kappa(left, right),
        "adjudicated_canonical_annotation": "separate_sample_jsonl",
        "contains_personal_identifiers": False,
    }


def deterministic_order(
    sample: EvidenceSample, permutation_id: str, *, seed: int = DEFAULT_SEED
) -> tuple[int, ...]:
    """Original veya sample-stable reproducible permutation üretir."""

    order = list(range(len(sample.selected_frames)))
    if permutation_id == "original":
        return tuple(order)
    if permutation_id != "permuted":
        raise HarnessFailure("INVALID_ORDER", f"bilinmeyen order: {permutation_id}")
    digest = hashlib.sha256(f"{seed}:{sample.sample_id}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    rng.shuffle(order)
    if len(order) > 1 and order == list(range(len(order))):
        order = order[1:] + order[:1]
    return tuple(order)


def build_plans(
    samples: Sequence[EvidenceSample],
    *,
    arms: Sequence[Arm],
    repeats: int,
    orders: Sequence[str],
    model_id: str,
    max_tokens: int,
    seed: int = DEFAULT_SEED,
) -> list[ExperimentPlan]:
    if repeats < 1:
        raise HarnessFailure("INVALID_REPEATS", "repeats en az 1 olmalı")
    if max_tokens < 1:
        raise HarnessFailure("INVALID_TOKEN_BUDGET", "max_tokens en az 1 olmalı")
    if not arms:
        raise HarnessFailure("INVALID_ARMS", "en az bir arm seçilmeli")
    plans: list[ExperimentPlan] = []
    for sample in samples:
        if not sample.grounding_evaluation_eligible:
            continue
        for permutation_id in orders:
            order = deterministic_order(sample, permutation_id, seed=seed)
            for repeat_number in range(1, repeats + 1):
                for arm in arms:
                    plans.append(
                        ExperimentPlan(
                            sample=sample,
                            arm=arm,
                            permutation_id=permutation_id,
                            repeat=repeat_number,
                            order=order,
                            model_id=model_id,
                            max_tokens=max_tokens,
                        )
                    )
    return plans


def _normalized_benchmark_contract(plan: ExperimentPlan) -> str:
    """Grounding encoding dışındaki prompt/schema yapısını canonicalize eder."""

    evidence_schema = _evidence_schema(plan.arm)
    expected_fields = BENCHMARK_EVIDENCE_CONTRACTS[plan.arm.value]
    properties = evidence_schema.get("properties", {})
    required = evidence_schema.get("required", [])
    if (
        tuple(properties) != expected_fields
        or set(required) != set(expected_fields)
        or len(properties) != 2
        or evidence_schema.get("additionalProperties") is not False
    ):
        raise HarnessFailure(
            "FAIRNESS_INVARIANT_VIOLATION",
            f"arm {plan.arm.value} normalized evidence contract yapısı farklı",
        )

    system_clause = _ARM_SYSTEM_CLAUSE[plan.arm.value]
    task_clause = _ARM_TASK_CLAUSE[plan.arm.value]
    system = arm_system_prompt(plan.arm)
    task = arm_task_prompt(plan)
    if system.count(system_clause) != 1 or task.count(task_clause) != 1:
        raise HarnessFailure(
            "FAIRNESS_INVARIANT_VIOLATION",
            f"arm {plan.arm.value} grounding clause canonicalize edilemedi",
        )
    system = system.replace(system_clause, "<GROUNDING_REFERENCE_ENCODING>", 1)
    task = task.replace(task_clause, "<GROUNDING_REFERENCE_ENCODING>", 1)

    schema = schema_for_arm(plan.arm)
    branches = schema["oneOf"] if "oneOf" in schema else [schema]
    attention = next(branch for branch in branches if "events" in branch.get("properties", {}))
    item_schema = attention["properties"]["events"]["items"]["properties"]["evidence"]["items"]
    item_schema["properties"] = {
        "grounding_reference": {"type": "benchmark_grounding_reference"},
        "claim": copy.deepcopy(properties["claim"]),
    }
    item_schema["required"] = ["grounding_reference", "claim"]
    contract = {"system": system, "task": task, "schema": schema}
    return json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fairness_signature(
    plan: ExperimentPlan, payload_hashes: dict[str, tuple[str, ...]] | None
) -> tuple[Any, ...]:
    sample = plan.sample
    sources = tuple(
        frame.image_path or f"{sample.video_path or sample.video_id}@{frame.timestamp:.6f}"
        for frame in sample.selected_frames
    )
    if payload_hashes is not None:
        sources = payload_hashes[sample.sample_id]
    return (
        len(sample.selected_frames),
        tuple(frame.timestamp for frame in sample.selected_frames),
        tuple(sources[index] for index in plan.order),
        plan.order,
        plan.model_id,
        plan.max_tokens,
        plan.temperature,
        _normalized_benchmark_contract(plan),
        settings.two_tier,
    )


def assert_fairness(
    plans: Sequence[ExperimentPlan],
    *,
    payload_hashes: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Her sample×order×repeat grubunda grounding dışındaki girdileri eşitler."""

    groups: dict[tuple[str, str, int], list[ExperimentPlan]] = defaultdict(list)
    for plan in plans:
        groups[(plan.sample.sample_id, plan.permutation_id, plan.repeat)].append(plan)
    for key, group in groups.items():
        signatures = {_fairness_signature(plan, payload_hashes) for plan in group}
        if len(signatures) != 1:
            raise HarnessFailure(
                "FAIRNESS_INVARIANT_VIOLATION",
                f"grounding representation dışında input farkı var: {key}",
            )


def arm_system_prompt(arm: Arm) -> str:
    if SYSTEM_TR.count(_C_SYSTEM_CLAUSE) != 1:
        raise HarnessFailure(
            "PROMPT_TEMPLATE_MISMATCH",
            "production SYSTEM_TR grounding cümlesi tekil bulunamadı",
        )
    system = SYSTEM_TR.replace(_C_SYSTEM_CLAUSE, _ARM_SYSTEM_CLAUSE[arm.value], 1)
    return f"{system}\n\n{TIER_TR}" if settings.two_tier else system


def arm_task_prompt(plan: ExperimentPlan) -> str:
    if TASK_TR.count(_PRODUCTION_TASK_CLAUSE) != 1:
        raise HarnessFailure(
            "PROMPT_TEMPLATE_MISMATCH",
            "production TASK_TR grounding cümlesi tekil bulunamadı",
        )
    task = TASK_TR.replace(_PRODUCTION_TASK_CLAUSE, _ARM_TASK_CLAUSE[plan.arm.value], 1)
    return task.replace("{start}", f"{plan.sample.window_start:.0f}").replace(
        "{end}", f"{plan.sample.window_end:.0f}"
    )


def _evidence_schema(arm: Arm) -> dict[str, Any]:
    if arm is Arm.A:
        properties: dict[str, Any] = {
            "image_index": {"type": "integer", "minimum": 0},
            "claim": {"type": "string", "minLength": 5, "maxLength": 500},
        }
    else:
        properties = {
            "frame_id": {"type": "string", "pattern": "^f_[0-9]{3,}$"},
            "claim": {"type": "string", "minLength": 5, "maxLength": 500},
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def schema_for_arm(arm: Arm) -> dict[str, Any]:
    """Production semantic schema'sını yalnız evidence representation'da ayırır."""

    schema = copy.deepcopy(tier_schema() if settings.two_tier else report_schema())
    branches = schema["oneOf"] if "oneOf" in schema else [schema]
    attention = next(branch for branch in branches if "events" in branch.get("properties", {}))
    event_schema = attention["properties"]["events"]["items"]
    event_schema["properties"]["evidence"]["items"] = _evidence_schema(arm)
    return schema


def _image_part(jpeg: bytes) -> dict[str, Any]:
    encoded = base64.b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


def build_user_content(
    plan: ExperimentPlan, payloads: Sequence[FramePayload]
) -> list[dict[str, Any]]:
    """Aynı payloadları seçilen order'da, yalnız arm etiketi değişerek dizer."""

    if len(payloads) != len(plan.sample.selected_frames):
        raise HarnessFailure(
            "MISSING_FRAME_MAPPING", "payload sayısı selected_frames ile eşleşmiyor"
        )
    by_index = {payload.frame_index: payload for payload in payloads}
    content: list[dict[str, Any]] = []
    for canonical_index in plan.order:
        payload = by_index.get(canonical_index)
        if payload is None:
            raise HarnessFailure(
                "MISSING_FRAME_MAPPING", f"canonical frame payload eksik: {canonical_index}"
            )
        if plan.arm is Arm.B:
            content.append({"type": "text", "text": f"FRAME_ID: {payload.frame_id}"})
        elif plan.arm is Arm.C:
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"FRAME_ID: {payload.frame_id}\n"
                        f"VIDEO_TIMESTAMP_SECONDS: {payload.timestamp:.3f}"
                    ),
                }
            )
        content.append(_image_part(payload.jpeg))
    content.append({"type": "text", "text": arm_task_prompt(plan)})
    return content


def _resolve_path(reference: str, *, annotation_path: Path, media_root: Path | None) -> Path:
    path = Path(reference)
    if path.is_absolute():
        return path.resolve()
    if media_root is not None:
        candidate = (media_root / path).resolve()
        if candidate.exists():
            return candidate
    return (annotation_path.parent / path).resolve()


async def load_frame_payloads(
    sample: EvidenceSample,
    *,
    annotation_path: Path,
    media_root: Path | None,
) -> tuple[FramePayload, ...]:
    """JPEG'leri sample başına bir kez yükler/çeker; bütün arm/repeat'ler paylaşır."""

    video_path: Path | None = None
    if sample.video_path:
        video_path = _resolve_path(
            sample.video_path, annotation_path=annotation_path, media_root=media_root
        )
    elif media_root is not None:
        video_path = (media_root / sample.video_id).resolve()

    async def load(frame: SelectedFrame) -> FramePayload:
        if frame.image_path:
            path = _resolve_path(
                frame.image_path, annotation_path=annotation_path, media_root=media_root
            )
            if not path.is_file():
                raise HarnessFailure("FRAME_SOURCE_NOT_FOUND", f"JPEG bulunamadı: {path}")
            jpeg = await asyncio.to_thread(path.read_bytes)
        else:
            if video_path is None or not video_path.is_file():
                raise HarnessFailure(
                    "FRAME_SOURCE_NOT_FOUND",
                    f"video/JPEG kaynağı bulunamadı: {sample.sample_id}",
                )
            jpeg = await grab_frame(video_path, frame.timestamp)
        if not jpeg:
            raise HarnessFailure(
                "FRAME_SOURCE_EMPTY", f"boş JPEG: {sample.sample_id}/{frame.frame_index}"
            )
        return FramePayload(
            frame_index=frame.frame_index,
            frame_id=frame.frame_id,
            timestamp=frame.timestamp,
            jpeg=jpeg,
            sha256=hashlib.sha256(jpeg).hexdigest(),
        )

    return tuple(await asyncio.gather(*(load(frame) for frame in sample.selected_frames)))


def _prompt_hash(plan: ExperimentPlan) -> str:
    prompt_contract = {
        "system": arm_system_prompt(plan.arm),
        "task": arm_task_prompt(plan),
        "schema": schema_for_arm(plan.arm),
        "model": plan.model_id,
        "max_tokens": plan.max_tokens,
        "temperature": plan.temperature,
    }
    canonical = json.dumps(
        prompt_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _code_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _base_row(
    plan: ExperimentPlan,
    payloads: Sequence[FramePayload],
    *,
    experiment_id: str,
    code_commit: str,
) -> dict[str, Any]:
    by_index = {payload.frame_index: payload for payload in payloads}
    selected = [
        {
            "display_position": position,
            "frame_index": canonical_index,
            "frame_id": by_index[canonical_index].frame_id,
            "timestamp": by_index[canonical_index].timestamp,
            "sha256": by_index[canonical_index].sha256,
        }
        for position, canonical_index in enumerate(plan.order)
    ]
    row: dict[str, Any] = {
        "experiment_id": experiment_id,
        "sample_id": plan.sample.sample_id,
        "evaluation_stage": plan.sample.evaluation_stage.value,
        "pairing_key": (f"{plan.sample.sample_id}:{plan.permutation_id}:repeat-{plan.repeat}"),
        "arm": plan.arm.value,
        "permutation_id": plan.permutation_id,
        "repeat": plan.repeat,
        "video_id": plan.sample.video_id,
        "window_start": plan.sample.window_start,
        "window_end": plan.sample.window_end,
        "selected_frames": selected,
        "grounding_representation": plan.grounding_representation,
        "GROUNDING_EVALUATION_CONDITION": GROUNDING_EVALUATION_CONDITION,
        "permutation_scope": PERMUTATION_SCOPE,
        "contracts": {
            "production": list(PRODUCTION_EVIDENCE_CONTRACT),
            "benchmark_normalized": {
                arm: list(fields) for arm, fields in BENCHMARK_EVIDENCE_CONTRACTS.items()
            },
            "production_contract_changed": False,
        },
        "predicted_event_type": None,
        "predicted_start": None,
        "predicted_peak": None,
        "predicted_end": None,
        "predicted_evidence": [],
        "contract_valid": False,
        "error_code": None,
        "latency_ms": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "prompt_eval_time_ms": None,
        "generation_time_ms": None,
        "model_id": plan.model_id,
        "prompt_hash": _prompt_hash(plan),
        "code_commit": code_commit,
        "preregistered_criteria": PREREGISTERED_CRITERIA,
        "expected_event_type": plan.sample.gt_event_type,
        "valid_evidence_frames": sorted(plan.sample.valid_evidence_frames),
        "boundary_near": plan.sample.boundary_near,
        "short_event": plan.sample.short_event,
        "visually_ambiguous": plan.sample.visually_ambiguous,
    }
    if plan.sample.source_label is not None:
        row["source_label"] = plan.sample.source_label
    return row


def _primary_event(data: dict[str, Any]) -> dict[str, Any] | None:
    events = data.get("events")
    if not isinstance(events, list) or not events:
        return None
    anomaly_type = data.get("anomaly_type")
    for event in events:
        if isinstance(event, dict) and event.get("event_type") == anomaly_type:
            return event
    return events[0] if isinstance(events[0], dict) else None


def _frame_index_from_id(frame_id: Any, sample: EvidenceSample) -> int:
    if not isinstance(frame_id, str):
        raise HarnessFailure("INVALID_EVIDENCE_REFERENCE", "frame_id string değil")
    mapping = {frame.frame_id: frame.frame_index for frame in sample.selected_frames}
    if frame_id not in mapping:
        raise HarnessFailure("INVALID_EVIDENCE_REFERENCE", f"bilinmeyen frame_id: {frame_id}")
    return mapping[frame_id]


def normalize_evidence(plan: ExperimentPlan, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Arm-specific referansı canonical selected-frame index/timestamp'a map eder."""

    normalized: list[dict[str, Any]] = []
    events = data.get("events", [])
    if not isinstance(events, list):
        raise HarnessFailure("INVALID_MODEL_OUTPUT", "events liste değil")
    by_index = {frame.frame_index: frame for frame in plan.sample.selected_frames}
    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            raise HarnessFailure("INVALID_MODEL_OUTPUT", f"events[{event_index}] object değil")
        evidence_items = event.get("evidence", [])
        if not isinstance(evidence_items, list) or not evidence_items:
            raise HarnessFailure(
                "MISSING_EVIDENCE_REFERENCE", f"events[{event_index}] evidence içermiyor"
            )
        for evidence_index, evidence in enumerate(evidence_items):
            if not isinstance(evidence, dict):
                raise HarnessFailure("INVALID_MODEL_OUTPUT", "evidence object değil")
            if plan.arm is Arm.A:
                position = evidence.get("image_index")
                if isinstance(position, bool) or not isinstance(position, int):
                    raise HarnessFailure("INVALID_EVIDENCE_REFERENCE", "image_index integer değil")
                if not 0 <= position < len(plan.order):
                    raise HarnessFailure(
                        "INVALID_EVIDENCE_REFERENCE", f"image_index order dışında: {position}"
                    )
                frame_index = plan.order[position]
            else:
                frame_index = _frame_index_from_id(evidence.get("frame_id"), plan.sample)
                position = plan.order.index(frame_index)
            frame = by_index[frame_index]
            claim = evidence.get("claim")
            if not isinstance(claim, str) or not 5 <= len(claim) <= 500:
                raise HarnessFailure("INVALID_EVIDENCE_CLAIM", "claim uzunluğu geçersiz")
            normalized.append(
                {
                    "event_index": event_index,
                    "evidence_index": evidence_index,
                    "display_position": position,
                    "frame_index": frame_index,
                    "frame_id": frame.frame_id,
                    "timestamp": frame.timestamp,
                    "claim": claim,
                }
            )
    return normalized


def apply_prediction_metrics(row: dict[str, Any], sample: EvidenceSample) -> None:
    chosen = [item["frame_index"] for item in row["predicted_evidence"]]
    row["evidence_precision"] = evidence_precision(chosen, sample.valid_evidence_frames)
    row["event_has_valid_evidence"] = event_has_valid_evidence(chosen, sample.valid_evidence_frames)
    row["evidence_count"] = evidence_count(chosen)
    row["evidence_set_recall"] = evidence_set_recall(chosen, sample.valid_evidence_frames)
    errors: list[float] = []
    if sample.gt_start is not None and sample.gt_end is not None:
        for item in row["predicted_evidence"]:
            error = temporal_absolute_error(
                item["timestamp"],
                gt_start=sample.gt_start,
                gt_peak=sample.gt_peak,
                gt_end=sample.gt_end,
            )
            if error is not None:
                errors.append(error)
    row["evidence_temporal_errors"] = errors
    row["temporal_absolute_error"] = median(errors) if errors else None
    predicted = row.get("predicted_event_type")
    row["event_type_correct"] = predicted == sample.gt_event_type
    row["event_detected"] = predicted not in {None, CanonicalEventType.NORMAL.value}
    row["normal_false_positive"] = (
        sample.gt_event_type == CanonicalEventType.NORMAL.value and row["event_detected"]
    )
    row["uncertain"] = predicted in {
        CanonicalEventType.UNCERTAIN.value,
        CanonicalEventType.UNKNOWN_ANOMALY.value,
    }


def parse_prediction(plan: ExperimentPlan, raw: str, row: dict[str, Any]) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HarnessFailure("INVALID_MODEL_OUTPUT", f"JSON parse hatası: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise HarnessFailure("INVALID_MODEL_OUTPUT", "top-level output object değil")
    if data.get("durum") == "olagan":
        row["predicted_event_type"] = CanonicalEventType.NORMAL.value
        row["contract_valid"] = True
        apply_prediction_metrics(row, plan.sample)
        return
    predicted_type = data.get("anomaly_type")
    try:
        row["predicted_event_type"] = CanonicalEventType(predicted_type).value
    except (TypeError, ValueError) as exc:
        raise HarnessFailure(
            "INVALID_MODEL_EVENT_TYPE", f"canonical olmayan event type: {predicted_type}"
        ) from exc
    events = data.get("events", [])
    event_times = [
        float(event["t"])
        for event in events
        if isinstance(event, dict) and isinstance(event.get("t"), int | float)
    ]
    primary = _primary_event(data)
    row["predicted_start"] = min(event_times) if event_times else None
    row["predicted_peak"] = (
        float(primary["t"])
        if primary is not None and isinstance(primary.get("t"), int | float)
        else None
    )
    row["predicted_end"] = max(event_times) if event_times else None
    row["predicted_evidence"] = normalize_evidence(plan, data)
    row["contract_valid"] = True
    apply_prediction_metrics(row, plan.sample)


def _timing_extras(response: Any) -> tuple[float | None, float | None]:
    timings = (getattr(response, "model_extra", None) or {}).get("timings") or {}

    def number(*keys: str) -> float | None:
        for key in keys:
            value = timings.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                return float(value)
        return None

    return number("prompt_ms", "prompt_eval_ms"), number("predicted_ms", "generation_ms")


async def run_plan(
    plan: ExperimentPlan,
    payloads: Sequence[FramePayload],
    *,
    experiment_id: str,
    code_commit: str,
) -> dict[str, Any]:
    row = _base_row(plan, payloads, experiment_id=experiment_id, code_commit=code_commit)
    started = time.perf_counter()
    try:
        response = await create_chat(
            main_client(),
            model=plan.model_id,
            messages=[
                {"role": "system", "content": arm_system_prompt(plan.arm)},
                {"role": "user", "content": build_user_content(plan, payloads)},
            ],
            max_tokens=plan.max_tokens,
            temperature=plan.temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": f"evidence_grounding_arm_{plan.arm.value.lower()}",
                    "strict": True,
                    "schema": schema_for_arm(plan.arm),
                },
            },
            extra_body={
                "speculative.n_max": 0,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        row["latency_ms"] = (time.perf_counter() - started) * 1000
        stats = call_stats(response)
        row["prompt_tokens"] = stats.get("prompt_tokens")
        row["completion_tokens"] = stats.get("completion_tokens")
        row["prompt_eval_time_ms"], row["generation_time_ms"] = _timing_extras(response)
        raw = response.choices[0].message.content or "{}"
        parse_prediction(plan, raw, row)
    except HarnessFailure as exc:
        row["latency_ms"] = (time.perf_counter() - started) * 1000
        row["error_code"] = exc.code
        row["error_detail"] = str(exc)[:500]
    except Exception as exc:  # model/network hatası da kaybolmadan typed row olur
        row["latency_ms"] = (time.perf_counter() - started) * 1000
        row["error_code"] = "MODEL_CALL_FAILED"
        row["error_detail"] = f"{type(exc).__name__}: {exc}"[:500]
    return row


def _pairwise(values: Sequence[Any]) -> tuple[list[Any], list[Any]]:
    left: list[Any] = []
    right: list[Any] = []
    for index, value in enumerate(values):
        for other in values[index + 1 :]:
            left.append(value)
            right.append(other)
    return left, right


def consistency_metrics(records: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Order ve repeat consistency'yi canonical frame referanslarıyla ölçer."""

    def event_value(record: dict[str, Any]) -> Any:
        return (
            record.get("predicted_event_type")
            if record.get("contract_valid")
            else (
                "error",
                record.get("error_code"),
            )
        )

    def evidence_value(record: dict[str, Any]) -> Any:
        if not record.get("contract_valid"):
            return ("error", record.get("error_code"))
        return tuple(sorted(item["frame_index"] for item in record.get("predicted_evidence", [])))

    def temporal_value(record: dict[str, Any]) -> Any:
        if not record.get("contract_valid"):
            return ("error", record.get("error_code"))
        return tuple(
            sorted(
                round(float(item["timestamp"]), 6) for item in record.get("predicted_evidence", [])
            )
        )

    order_groups: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    repeat_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        order_groups[(record["sample_id"], record["arm"], record["repeat"])][
            record["permutation_id"]
        ] = record
        repeat_groups[(record["sample_id"], record["arm"], record["permutation_id"])].append(record)

    event_left: list[Any] = []
    event_right: list[Any] = []
    evidence_left: list[Any] = []
    evidence_right: list[Any] = []
    temporal_left: list[Any] = []
    temporal_right: list[Any] = []
    for orders in order_groups.values():
        if "original" not in orders or "permuted" not in orders:
            continue
        original, permuted = orders["original"], orders["permuted"]
        event_left.append(event_value(original))
        event_right.append(event_value(permuted))
        evidence_left.append(evidence_value(original))
        evidence_right.append(evidence_value(permuted))
        temporal_left.append(temporal_value(original))
        temporal_right.append(temporal_value(permuted))

    repeat_event_left: list[Any] = []
    repeat_event_right: list[Any] = []
    repeat_evidence_left: list[Any] = []
    repeat_evidence_right: list[Any] = []
    for group in repeat_groups.values():
        ordered = sorted(group, key=lambda record: int(record["repeat"]))
        left, right = _pairwise([event_value(record) for record in ordered])
        repeat_event_left.extend(left)
        repeat_event_right.extend(right)
        left, right = _pairwise([evidence_value(record) for record in ordered])
        repeat_evidence_left.extend(left)
        repeat_evidence_right.extend(right)

    return {
        "event_type_consistency": agreement_rate(event_left, event_right),
        "evidence_frame_consistency": agreement_rate(evidence_left, evidence_right),
        "temporal_consistency": agreement_rate(temporal_left, temporal_right),
        "same_event_agreement": agreement_rate(repeat_event_left, repeat_event_right),
        "same_evidence_agreement": agreement_rate(repeat_evidence_left, repeat_evidence_right),
    }


def evaluation_metadata(samples: Sequence[EvidenceSample]) -> dict[str, Any]:
    excluded = [
        {
            "sample_id": sample.sample_id,
            "evaluation_stage": sample.evaluation_stage.value,
            "reason": sample.grounding_exclusion_reason,
        }
        for sample in samples
        if not sample.grounding_evaluation_eligible
    ]
    return {
        "evaluation_stage_counts": {
            stage.value: sum(sample.evaluation_stage is stage for sample in samples)
            for stage in EvaluationStage
        },
        "GROUNDING_EVALUATION_CONDITION": GROUNDING_EVALUATION_CONDITION,
        "measures_end_to_end_keyframe_selection": False,
        "eligible_grounding_sample_count": sum(
            sample.grounding_evaluation_eligible for sample in samples
        ),
        "excluded_keyframe_failure_count": len(excluded),
        "excluded_samples": excluded,
        "permutation_scope": PERMUTATION_SCOPE,
        "contracts": {
            "production": list(PRODUCTION_EVIDENCE_CONTRACT),
            "benchmark_normalized": {
                arm: list(fields) for arm, fields in BENCHMARK_EVIDENCE_CONTRACTS.items()
            },
            "production_contract_changed": False,
        },
    }


def dry_run_summary(
    samples: Sequence[EvidenceSample],
    plans: Sequence[ExperimentPlan],
    *,
    output_path: Path,
    seed: int,
    annotator_agreement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mappings: dict[str, Any] = {}
    plan_orders = sorted({plan.permutation_id for plan in plans})
    for sample in samples:
        mappings[sample.sample_id] = {
            "video_id": sample.video_id,
            "source_label": sample.source_label,
            "evaluation_stage": sample.evaluation_stage.value,
            "grounding_evaluation_eligible": sample.grounding_evaluation_eligible,
            "exclusion_reason": sample.grounding_exclusion_reason,
            "selected_frames": [
                {
                    "frame_index": frame.frame_index,
                    "frame_id": frame.frame_id,
                    "timestamp": frame.timestamp,
                }
                for frame in sample.selected_frames
            ],
            "orders": {
                order: list(deterministic_order(sample, order, seed=seed))
                for order in plan_orders
                if sample.grounding_evaluation_eligible
            },
        }
    summary = {
        "mode": "dry-run",
        "sample_count": len(samples),
        "arms": sorted({plan.arm.value for plan in plans}),
        "orders": sorted({plan.permutation_id for plan in plans}),
        "repeats": max((plan.repeat for plan in plans), default=0),
        "expected_call_count": len(plans),
        "output_path": str(output_path.resolve()),
        "model_id": plans[0].model_id if plans else None,
        "max_tokens": plans[0].max_tokens if plans else None,
        "fairness_invariants": list(FAIRNESS_INVARIANTS),
        "preregistered_criteria": PREREGISTERED_CRITERIA,
        "frame_mappings": mappings,
        "model_calls_made": 0,
        **evaluation_metadata(samples),
    }
    if annotator_agreement is not None:
        summary["annotator_agreement"] = annotator_agreement
    return summary


async def execute(
    samples: Sequence[EvidenceSample],
    plans: Sequence[ExperimentPlan],
    *,
    annotation_path: Path,
    media_root: Path | None,
    output_path: Path,
    experiment_id: str,
    overwrite: bool,
) -> list[dict[str, Any]]:
    if output_path.exists() and not overwrite:
        raise HarnessFailure(
            "OUTPUT_EXISTS", f"çıktı zaten var; --overwrite gerekli: {output_path}"
        )
    payloads_by_sample: dict[str, tuple[FramePayload, ...]] = {}
    for sample in samples:
        if not sample.grounding_evaluation_eligible:
            continue
        payloads_by_sample[sample.sample_id] = await load_frame_payloads(
            sample, annotation_path=annotation_path, media_root=media_root
        )
    payload_hashes = {
        sample_id: tuple(payload.sha256 for payload in payloads)
        for sample_id, payloads in payloads_by_sample.items()
    }
    assert_fairness(plans, payload_hashes=payload_hashes)

    commit = _code_commit()
    rows: list[dict[str, Any]] = []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for plan in plans:
            row = await run_plan(
                plan,
                payloads_by_sample[plan.sample.sample_id],
                experiment_id=experiment_id,
                code_commit=commit,
            )
            rows.append(row)
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
    return rows


def _default_output() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return ROOT / "bench" / "results" / f"evidence_grounding_{stamp}.jsonl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True, help="Annotation JSONL")
    parser.add_argument(
        "--annotator-labels",
        type=Path,
        help="Opsiyonel, anonim ann_1/ann_2 binary per-frame annotation JSONL",
    )
    parser.add_argument(
        "--arms", nargs="+", choices=[arm.value for arm in Arm], default=["A", "B", "C"]
    )
    parser.add_argument(
        "--orders", nargs="+", choices=["original", "permuted"], default=["original", "permuted"]
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--model", default=settings.main_model)
    parser.add_argument("--max-tokens", type=int, default=settings.interpret_max_tokens)
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--out", type=Path, default=_default_output())
    parser.add_argument("--experiment-id")
    parser.add_argument("--overwrite", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Planı doğrula; model çağırma")
    mode.add_argument(
        "--execute", action="store_true", help="Yerel VLM benchmark'ını açıkça çalıştır"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        samples = load_samples(args.samples.resolve())
        annotator_agreement = None
        if args.annotator_labels is not None:
            labels = load_annotator_labels(args.annotator_labels.resolve())
            annotator_agreement = annotator_agreement_report(labels, samples)
        arms = [Arm(value) for value in args.arms]
        plans = build_plans(
            samples,
            arms=arms,
            repeats=args.repeats,
            orders=args.orders,
            model_id=args.model,
            max_tokens=args.max_tokens,
            seed=args.seed,
        )
        assert_fairness(plans)
        if args.dry_run:
            summary = dry_run_summary(
                samples,
                plans,
                output_path=args.out,
                seed=args.seed,
                annotator_agreement=annotator_agreement,
            )
        else:
            experiment_id = args.experiment_id or (
                "phase-b-grounding-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            )
            rows = asyncio.run(
                execute(
                    samples,
                    plans,
                    annotation_path=args.samples.resolve(),
                    media_root=args.media_root.resolve() if args.media_root else None,
                    output_path=args.out.resolve(),
                    experiment_id=experiment_id,
                    overwrite=args.overwrite,
                )
            )
            summary = {
                "mode": "executed",
                "experiment_id": experiment_id,
                "row_count": len(rows),
                "contract_valid": sum(bool(row["contract_valid"]) for row in rows),
                "output_path": str(args.out.resolve()),
                "consistency": consistency_metrics(rows),
                "metrics_by_arm": {
                    arm.value: grounding_metrics([row for row in rows if row["arm"] == arm.value])
                    for arm in arms
                },
                "preregistered_criteria": PREREGISTERED_CRITERIA,
                **evaluation_metadata(samples),
            }
            if annotator_agreement is not None:
                summary["annotator_agreement"] = annotator_agreement
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except HarnessFailure as exc:
        print(
            json.dumps(
                {"status": "FAIL", "error_code": exc.code, "error_detail": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
