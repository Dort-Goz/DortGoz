"""Select a bounded, diverse D-FINE training snapshot without GPU work."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain.dataset import (
    DatasetLicenseStatus,
    DatasetSplit,
    DatasetUse,
    OfflineDatasetManifest,
)
from ..domain.training import TrainingSample, TrainingSampleStatus

_NEGATIVE_LABEL = "__verified_no_target_objects__"
_MAX_POLICY_BYTES = 1024 * 1024


class TrainingSelectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SelectionExclusion(StrEnum):
    NOT_VERIFIED = "not_verified"
    STALE_DATASET = "stale_dataset"
    EXACT_DUPLICATE = "exact_duplicate"
    TRAIN_BUDGET = "train_budget"
    VALIDATION_BUDGET = "validation_budget"
    SOURCE_VIDEO_CAP = "source_video_cap"
    EVENT_CAP = "event_cap"
    NEGATIVE_QUOTA = "negative_quota"


class TrainingSelectionPolicy(BaseModel):
    """Resource and diversity limits for one immutable export snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["dfine-selection-v1"] = "dfine-selection-v1"
    minimum_train_samples: int = Field(default=80, ge=1)
    maximum_train_samples: int = Field(default=500, ge=1)
    minimum_validation_samples: int = Field(default=20, ge=1)
    maximum_validation_samples: int = Field(default=100, ge=1)
    minimum_train_source_videos: int = Field(default=10, ge=1)
    maximum_samples_per_source_video: int = Field(default=9, ge=1)
    maximum_samples_per_event: int = Field(default=3, ge=1)
    maximum_negative_fraction: float = Field(default=0.25, ge=0, le=0.5)

    @model_validator(mode="after")
    def limits_are_consistent(self) -> TrainingSelectionPolicy:
        if self.minimum_train_samples > self.maximum_train_samples:
            raise ValueError("minimum_train_samples maximum değerini aşamaz")
        if self.minimum_validation_samples > self.maximum_validation_samples:
            raise ValueError("minimum_validation_samples maximum değerini aşamaz")
        if self.minimum_train_source_videos > self.maximum_train_samples:
            raise ValueError("minimum_train_source_videos train bütçesini aşamaz")
        if self.maximum_samples_per_event > self.maximum_samples_per_source_video:
            raise ValueError("olay kotası kaynak video kotasını aşamaz")
        return self


class SelectionScoreReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    points: int = Field(ge=0, le=40)
    detail: str = Field(min_length=1)


class TrainingSelectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str = Field(min_length=1)
    split: DatasetSplit
    selected: bool
    training_value_score: int = Field(ge=0, le=100)
    reasons: list[SelectionScoreReason] = Field(default_factory=list)
    exclusion: SelectionExclusion | None = None

    @model_validator(mode="after")
    def decision_is_consistent(self) -> TrainingSelectionDecision:
        if self.selected == (self.exclusion is not None):
            raise ValueError("seçim kararı selected veya exclusion alanlarından yalnız birini taşır")
        if sum(reason.points for reason in self.reasons) != self.training_value_score:
            raise ValueError("training_value_score gerekçe puanlarıyla eşleşmiyor")
        return self


class TrainingSelectionCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    considered: int = Field(ge=0)
    eligible: int = Field(ge=0)
    selected: int = Field(ge=0)
    excluded: int = Field(ge=0)
    selected_train: int = Field(ge=0)
    selected_validation: int = Field(ge=0)
    selected_negative: int = Field(ge=0)
    selected_train_source_videos: int = Field(ge=0)


class TrainingSelectionReport(BaseModel):
    """Audit record bound to exact sample revisions, annotations, and policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_version: Literal["1.0.0"] = "1.0.0"
    dataset_id: str = Field(min_length=1)
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_sample_ids: list[str] = Field(min_length=1)
    counts: TrainingSelectionCounts
    decisions: list[TrainingSelectionDecision] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def report_is_consistent(self) -> TrainingSelectionReport:
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at saat dilimi içermelidir")
        if len(self.selected_sample_ids) != len(set(self.selected_sample_ids)):
            raise ValueError("selected_sample_ids tekrar içeremez")
        selected = [item.sample_id for item in self.decisions if item.selected]
        if selected != self.selected_sample_ids:
            raise ValueError("selected_sample_ids seçim kararlarıyla eşleşmiyor")
        if self.counts.selected != len(selected):
            raise ValueError("selected sayısı seçim kararlarıyla eşleşmiyor")
        return self


@dataclass(frozen=True)
class TrainingSelectionResult:
    selected_samples: list[TrainingSample]
    report: TrainingSelectionReport


@dataclass(frozen=True)
class _ScoredSample:
    sample: TrainingSample
    score: int
    reasons: list[SelectionScoreReason]


def load_training_selection_policy(path: Path) -> TrainingSelectionPolicy:
    if path.is_symlink():
        raise TrainingSelectionError("SELECTION_POLICY_UNSAFE", f"symlink reddedildi: {path}")
    resolved = path.resolve()
    if not resolved.is_file():
        raise TrainingSelectionError(
            "SELECTION_POLICY_MISSING", f"seçim politikası bulunamadı: {path}"
        )
    if resolved.stat().st_size > _MAX_POLICY_BYTES:
        raise TrainingSelectionError(
            "SELECTION_POLICY_TOO_LARGE", f"seçim politikası çok büyük: {path}"
        )
    try:
        return TrainingSelectionPolicy.model_validate_json(
            resolved.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise TrainingSelectionError(
            "SELECTION_POLICY_INVALID", f"seçim politikası geçersiz: {path}: {exc}"
        ) from exc


def select_training_samples(
    *,
    samples: list[TrainingSample],
    dataset_manifest: OfflineDatasetManifest,
    policy: TrainingSelectionPolicy,
    created_at: datetime | None = None,
) -> TrainingSelectionResult:
    """Return a deterministic metadata-only selection and its audit report."""

    _ensure_training_manifest_allowed(dataset_manifest)
    entries = {entry.dataset_video_id: entry for entry in dataset_manifest.entries}
    matching = sorted(
        (sample for sample in samples if sample.dataset_id == dataset_manifest.dataset_id),
        key=lambda sample: sample.sample_id,
    )
    preexcluded: list[TrainingSelectionDecision] = []
    eligible: list[TrainingSample] = []
    for sample in matching:
        if sample.dataset_fingerprint != dataset_manifest.dataset_fingerprint:
            preexcluded.append(_excluded(sample, SelectionExclusion.STALE_DATASET))
            continue
        if sample.status != TrainingSampleStatus.VERIFIED or sample.frame_review is None:
            preexcluded.append(_excluded(sample, SelectionExclusion.NOT_VERIFIED))
            continue
        entry = entries.get(sample.dataset_video_id)
        if (
            entry is None
            or entry.source_ref != sample.source_video_ref
            or entry.file_sha256 != sample.source_video_sha256
            or entry.split != sample.split
            or DatasetUse.TRAINING not in entry.allowed_uses
        ):
            raise TrainingSelectionError(
                "SELECTION_SAMPLE_PROVENANCE_MISMATCH",
                f"doğrulanmış örnek dataset kaydıyla eşleşmiyor: {sample.sample_id}",
            )
        eligible.append(sample)

    eligible, duplicate_decisions = _remove_exact_duplicates(eligible)
    decisions: list[TrainingSelectionDecision] = [*preexcluded, *duplicate_decisions]
    selected: list[TrainingSample] = []
    for split, maximum in (
        (DatasetSplit.TRAIN, policy.maximum_train_samples),
        (DatasetSplit.VALIDATION, policy.maximum_validation_samples),
    ):
        pool = [sample for sample in eligible if sample.split == split]
        split_selected, split_decisions = _select_split(pool, split, maximum, policy)
        selected.extend(split_selected)
        decisions.extend(split_decisions)

    selected.sort(key=lambda sample: (sample.split.value, sample.sample_id))
    selected_ids = {sample.sample_id for sample in selected}
    decisions.sort(
        key=lambda item: (
            0 if item.sample_id in selected_ids else 1,
            item.split.value,
            item.sample_id,
        )
    )
    _enforce_minimums(selected, policy)
    policy_fingerprint = _payload_sha256(policy.model_dump(mode="json"))
    selection_fingerprint = _payload_sha256(
        {
            "dataset_fingerprint": dataset_manifest.dataset_fingerprint,
            "policy_fingerprint": policy_fingerprint,
            "samples": [
                {
                    "sample_id": sample.sample_id,
                    "revision": sample.revision,
                    "frame_sha256": sample.frame_sha256,
                    "review": sample.frame_review.model_dump(mode="json"),
                }
                for sample in selected
            ],
        }
    )
    negative_count = sum(_is_negative(sample) for sample in selected)
    train_sources = {
        sample.dataset_video_id for sample in selected if sample.split == DatasetSplit.TRAIN
    }
    report = TrainingSelectionReport(
        dataset_id=dataset_manifest.dataset_id,
        dataset_fingerprint=dataset_manifest.dataset_fingerprint,
        policy_version=policy.policy_version,
        policy_fingerprint=policy_fingerprint,
        selection_fingerprint=selection_fingerprint,
        selected_sample_ids=[item.sample_id for item in decisions if item.selected],
        counts=TrainingSelectionCounts(
            considered=len(matching),
            eligible=len(eligible),
            selected=len(selected),
            excluded=len(matching) - len(selected),
            selected_train=sum(sample.split == DatasetSplit.TRAIN for sample in selected),
            selected_validation=sum(
                sample.split == DatasetSplit.VALIDATION for sample in selected
            ),
            selected_negative=negative_count,
            selected_train_source_videos=len(train_sources),
        ),
        decisions=decisions,
        created_at=created_at or datetime.now(UTC),
    )
    selected_by_id = {sample.sample_id: sample for sample in selected}
    return TrainingSelectionResult(
        selected_samples=[selected_by_id[sample_id] for sample_id in report.selected_sample_ids],
        report=report,
    )


def write_training_selection_report(path: Path, report: TrainingSelectionReport) -> None:
    if path.is_symlink():
        raise TrainingSelectionError("SELECTION_REPORT_UNSAFE", f"symlink reddedildi: {path}")
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _select_split(
    pool: list[TrainingSample],
    split: DatasetSplit,
    maximum: int,
    policy: TrainingSelectionPolicy,
) -> tuple[list[TrainingSample], list[TrainingSelectionDecision]]:
    if not pool:
        return [], []
    budget = min(maximum, len(pool))
    selected: list[TrainingSample] = []
    remaining = {sample.sample_id: sample for sample in pool}
    source_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    global_source_counts = Counter(sample.dataset_video_id for sample in pool)
    global_event_counts = Counter(sample.event_id for sample in pool)
    global_label_counts = Counter(label for sample in pool for label in _labels(sample))
    selected_negative = 0
    decisions: list[TrainingSelectionDecision] = []

    while remaining and len(selected) < budget:
        feasible: list[_ScoredSample] = []
        slots_left = budget - len(selected)
        missing_train_sources = (
            max(0, policy.minimum_train_source_videos - len(source_counts))
            if split == DatasetSplit.TRAIN
            else 0
        )
        for sample in remaining.values():
            if source_counts[sample.dataset_video_id] >= policy.maximum_samples_per_source_video:
                continue
            if event_counts[sample.event_id] >= policy.maximum_samples_per_event:
                continue
            if (
                missing_train_sources > 0
                and slots_left <= missing_train_sources
                and sample.dataset_video_id in source_counts
            ):
                continue
            if (
                split == DatasetSplit.TRAIN
                and _is_negative(sample)
                and selected_negative + 1
                > math.floor((len(selected) + 1) * policy.maximum_negative_fraction)
            ):
                continue
            feasible.append(
                _score_sample(
                    sample,
                    source_counts,
                    event_counts,
                    label_counts,
                    global_source_counts,
                    global_event_counts,
                    global_label_counts,
                )
            )
        if not feasible:
            break
        winner = min(feasible, key=lambda item: (-item.score, item.sample.sample_id))
        sample = winner.sample
        selected.append(sample)
        decisions.append(
            TrainingSelectionDecision(
                sample_id=sample.sample_id,
                split=sample.split,
                selected=True,
                training_value_score=winner.score,
                reasons=winner.reasons,
            )
        )
        source_counts[sample.dataset_video_id] += 1
        event_counts[sample.event_id] += 1
        label_counts.update(_labels(sample))
        selected_negative += int(_is_negative(sample))
        del remaining[sample.sample_id]

    for sample in sorted(remaining.values(), key=lambda item: item.sample_id):
        scored = _score_sample(
            sample,
            source_counts,
            event_counts,
            label_counts,
            global_source_counts,
            global_event_counts,
            global_label_counts,
        )
        if source_counts[sample.dataset_video_id] >= policy.maximum_samples_per_source_video:
            exclusion = SelectionExclusion.SOURCE_VIDEO_CAP
        elif event_counts[sample.event_id] >= policy.maximum_samples_per_event:
            exclusion = SelectionExclusion.EVENT_CAP
        elif (
            split == DatasetSplit.TRAIN
            and _is_negative(sample)
            and selected_negative + 1
            > math.floor((len(selected) + 1) * policy.maximum_negative_fraction)
        ):
            exclusion = SelectionExclusion.NEGATIVE_QUOTA
        elif split == DatasetSplit.TRAIN:
            exclusion = SelectionExclusion.TRAIN_BUDGET
        else:
            exclusion = SelectionExclusion.VALIDATION_BUDGET
        decisions.append(
            TrainingSelectionDecision(
                sample_id=sample.sample_id,
                split=sample.split,
                selected=False,
                training_value_score=scored.score,
                reasons=scored.reasons,
                exclusion=exclusion,
            )
        )
    return selected, decisions


def _score_sample(
    sample: TrainingSample,
    selected_source_counts: Counter[str],
    selected_event_counts: Counter[str],
    selected_label_counts: Counter[str],
    global_source_counts: Counter[str],
    global_event_counts: Counter[str],
    global_label_counts: Counter[str],
) -> _ScoredSample:
    labels = _labels(sample)
    minimum_source_frequency = min(global_source_counts.values())
    minimum_event_frequency = min(global_event_counts.values())
    minimum_label_frequency = min(global_label_counts.values())
    category_points = max(
        round(
            40
            * minimum_label_frequency
            / global_label_counts[label]
            / (1 + selected_label_counts[label])
        )
        for label in labels
    )
    source_points = round(
        25
        * minimum_source_frequency
        / global_source_counts[sample.dataset_video_id]
        / (1 + selected_source_counts[sample.dataset_video_id])
    )
    event_points = round(
        20
        * minimum_event_frequency
        / global_event_counts[sample.event_id]
        / (1 + selected_event_counts[sample.event_id])
    )
    peak_points = 10 if sample.selection_reason == "event_peak" else 0
    reasons = [
        SelectionScoreReason(
            code="category_balance",
            points=category_points,
            detail=f"etiket dengesi: {', '.join(labels)}",
        ),
        SelectionScoreReason(
            code="source_video_diversity",
            points=source_points,
            detail=f"kaynak video çeşitliliği: {sample.dataset_video_id}",
        ),
        SelectionScoreReason(
            code="event_diversity",
            points=event_points,
            detail=f"olay çeşitliliği: {sample.event_id}",
        ),
        SelectionScoreReason(
            code="event_peak",
            points=peak_points,
            detail=("olayın zirve karesi" if peak_points else "zirve karesi değil"),
        ),
        SelectionScoreReason(
            code="human_verified",
            points=5,
            detail="insan doğrulaması mevcut",
        ),
    ]
    return _ScoredSample(sample=sample, score=sum(item.points for item in reasons), reasons=reasons)


def _remove_exact_duplicates(
    samples: list[TrainingSample],
) -> tuple[list[TrainingSample], list[TrainingSelectionDecision]]:
    by_hash: dict[str, list[TrainingSample]] = defaultdict(list)
    for sample in samples:
        by_hash[sample.frame_sha256].append(sample)
    retained: list[TrainingSample] = []
    decisions: list[TrainingSelectionDecision] = []
    for frame_hash in sorted(by_hash):
        group = sorted(by_hash[frame_hash], key=lambda sample: sample.sample_id)
        if len({sample.split for sample in group}) > 1:
            raise TrainingSelectionError(
                "SELECTION_SPLIT_LEAKAGE",
                f"aynı kare train ve validation içinde bulunuyor: {frame_hash}",
            )
        signatures = {_annotation_signature(sample) for sample in group}
        if len(signatures) > 1:
            raise TrainingSelectionError(
                "SELECTION_ANNOTATION_CONFLICT",
                f"aynı kare için çelişen insan anotasyonu bulundu: {frame_hash}",
            )
        retained.append(group[0])
        decisions.extend(
            _excluded(sample, SelectionExclusion.EXACT_DUPLICATE) for sample in group[1:]
        )
    return sorted(retained, key=lambda sample: sample.sample_id), decisions


def _annotation_signature(sample: TrainingSample) -> str:
    assert sample.frame_review is not None
    return _payload_sha256(
        {
            "review_result": sample.frame_review.review_result.value,
            "boxes": [box.model_dump(mode="json") for box in sample.frame_review.boxes],
            "width": sample.image_width,
            "height": sample.image_height,
        }
    )


def _excluded(
    sample: TrainingSample, exclusion: SelectionExclusion
) -> TrainingSelectionDecision:
    return TrainingSelectionDecision(
        sample_id=sample.sample_id,
        split=sample.split,
        selected=False,
        training_value_score=0,
        exclusion=exclusion,
    )


def _labels(sample: TrainingSample) -> tuple[str, ...]:
    assert sample.frame_review is not None
    labels = sorted({box.category_name for box in sample.frame_review.boxes})
    return tuple(labels or [_NEGATIVE_LABEL])


def _is_negative(sample: TrainingSample) -> bool:
    return _labels(sample) == (_NEGATIVE_LABEL,)


def _enforce_minimums(
    selected: list[TrainingSample], policy: TrainingSelectionPolicy
) -> None:
    train = [sample for sample in selected if sample.split == DatasetSplit.TRAIN]
    validation = [
        sample for sample in selected if sample.split == DatasetSplit.VALIDATION
    ]
    if len(train) < policy.minimum_train_samples:
        raise TrainingSelectionError(
            "SELECTION_TRAIN_MINIMUM_NOT_MET",
            f"seçim en az {policy.minimum_train_samples} train karesi gerektirir; {len(train)} seçildi",
        )
    if len(validation) < policy.minimum_validation_samples:
        raise TrainingSelectionError(
            "SELECTION_VALIDATION_MINIMUM_NOT_MET",
            "seçim en az "
            f"{policy.minimum_validation_samples} validation karesi gerektirir; "
            f"{len(validation)} seçildi",
        )
    train_sources = {sample.dataset_video_id for sample in train}
    if len(train_sources) < policy.minimum_train_source_videos:
        raise TrainingSelectionError(
            "SELECTION_SOURCE_MINIMUM_NOT_MET",
            "seçim en az "
            f"{policy.minimum_train_source_videos} train kaynak videosu gerektirir; "
            f"{len(train_sources)} seçildi",
        )


def _ensure_training_manifest_allowed(manifest: OfflineDatasetManifest) -> None:
    if (
        not manifest.training_allowed
        or DatasetUse.TRAINING not in manifest.allowed_uses
        or manifest.license_status != DatasetLicenseStatus.VERIFIED
        or manifest.license_id not in {"Apache-2.0", "MIT"}
    ):
        raise TrainingSelectionError(
            "SELECTION_DATASET_REJECTED",
            f"dataset D-FINE seçimi için onaylı değil: {manifest.dataset_id}",
        )


def _payload_sha256(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "SelectionExclusion",
    "TrainingSelectionDecision",
    "TrainingSelectionError",
    "TrainingSelectionPolicy",
    "TrainingSelectionReport",
    "TrainingSelectionResult",
    "load_training_selection_policy",
    "select_training_samples",
    "write_training_selection_report",
]
