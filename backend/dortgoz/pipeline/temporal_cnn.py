from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite
from random import Random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain.candidate import ScreeningSample
from .ingest import MotionSample

FEATURE_SCHEMA = ("changed", "fg", "mad", "activity")


class TemporalCnnArtifact(BaseModel):

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_type: str = "temporal_cnn"
    feature_schema: tuple[str, ...] = FEATURE_SCHEMA
    kernel_size: int = Field(ge=1, le=15)
    weights: tuple[tuple[float, ...], ...]
    bias: float
    trained_sample_count: int = Field(ge=0)
    training_seed: int = Field(ge=0)
    license: Literal["Apache-2.0", "MIT"]
    notes: str = ""

    @model_validator(mode="after")
    def validate_shape_and_values(self) -> TemporalCnnArtifact:
        if self.model_type != "temporal_cnn":
            raise ValueError("temporal CNN artifact model_type temporal_cnn olmalı")
        if self.feature_schema != FEATURE_SCHEMA:
            raise ValueError("temporal CNN feature_schema motion-v1 ile eşleşmiyor")
        if self.kernel_size % 2 == 0:
            raise ValueError("temporal CNN kernel_size tek sayı olmalı")
        if len(self.weights) != self.kernel_size:
            raise ValueError("temporal CNN ağırlık satır sayısı kernel_size ile eşleşmiyor")
        if any(len(row) != len(FEATURE_SCHEMA) for row in self.weights):
            raise ValueError("temporal CNN ağırlık sütun sayısı feature schema ile eşleşmiyor")
        if not isfinite(self.bias) or any(
            not isfinite(weight) for row in self.weights for weight in row
        ):
            raise ValueError("temporal CNN ağırlıkları sonlu olmalı")
        return self


@dataclass(frozen=True)
class TemporalCnnTrainingExample:

    video_id: str
    profile: list[MotionSample]
    positive_intervals: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not self.video_id:
            raise ValueError("training example video_id boş olamaz")
        if any(
            start < 0 or end <= start or not isfinite(start) or not isfinite(end)
            for start, end in self.positive_intervals
        ):
            raise ValueError("pozitif interval değerleri sonlu ve start < end olmalı")


class TemporalCnnCandidateModel:

    def __init__(self, artifact: TemporalCnnArtifact) -> None:
        self.artifact = artifact
        self.model_id = artifact.model_id

    def score(self, profile: list[MotionSample]) -> list[ScreeningSample]:
        return [
            ScreeningSample(
                timestamp=sample.t,
                anomaly_score=self.predict_probability(profile, index),
                image_quality=1.0,
                source_model=self.model_id,
                feature_ref=f"temporal-cnn:{index}",
            )
            for index, sample in enumerate(profile)
        ]

    def predict_probability(self, profile: list[MotionSample], index: int) -> float:
        return _sigmoid(self._logit(profile, index))

    def _logit(self, profile: list[MotionSample], index: int) -> float:
        return self.artifact.bias + sum(
            weight * value
            for row, values in zip(self.artifact.weights, _window(profile, index, self.artifact.kernel_size))
            for weight, value in zip(row, values)
        )


@dataclass(frozen=True)
class TemporalCnnTrainingMetrics:
    sample_count: int
    positive_count: int
    mean_loss: float
    recall_at_half: float
    false_positive_rate_at_half: float
    interval_event_recall: float = 0.0


def train_temporal_cnn(
    examples: list[TemporalCnnTrainingExample],
    *,
    model_id: str,
    version: str = "1.0.0",
    kernel_size: int = 3,
    epochs: int = 80,
    learning_rate: float = 0.35,
    l2: float = 0.0001,
    seed: int = 20260806,
    artifact_license: Literal["Apache-2.0", "MIT"] = "Apache-2.0",
) -> tuple[TemporalCnnCandidateModel, TemporalCnnTrainingMetrics]:

    if not model_id:
        raise ValueError("model_id boş olamaz")
    if kernel_size < 1 or kernel_size > 15 or kernel_size % 2 == 0:
        raise ValueError("kernel_size 1–15 arasında tek sayı olmalı")
    if epochs < 1 or learning_rate <= 0 or l2 < 0:
        raise ValueError("epochs, learning_rate ve l2 geçersiz")
    if artifact_license not in {"Apache-2.0", "MIT"}:
        raise ValueError("candidate artifact lisansı Apache-2.0 veya MIT olmalı")
    samples = [
        (example.profile, index, _is_positive(item.t, example.positive_intervals))
        for example in examples
        for index, item in enumerate(example.profile)
    ]
    if not samples:
        raise ValueError("temporal CNN eğitimi için en az bir motion sample gerekir")
    if not any(label for _, _, label in samples):
        raise ValueError("temporal CNN eğitimi için en az bir pozitif interval gerekir")
    if all(label for _, _, label in samples):
        raise ValueError("temporal CNN eğitimi için en az bir negatif sample gerekir")

    rng = Random(seed)
    weights = [
        [rng.uniform(-0.01, 0.01) for _ in FEATURE_SCHEMA]
        for _ in range(kernel_size)
    ]
    bias = 0.0
    n_pos = sum(1 for _, _, label in samples if label)
    w_pos = (len(samples) - n_pos) / max(n_pos, 1)
    order = list(range(len(samples)))
    for _ in range(epochs):
        rng.shuffle(order)
        for sample_index in order:
            profile, index, label = samples[sample_index]
            values = _window(profile, index, kernel_size)
            logit = bias + sum(
                weight * value
                for row, row_values in zip(weights, values)
                for weight, value in zip(row, row_values)
            )
            error = (_sigmoid(logit) - float(label)) * (w_pos if label else 1.0)
            for row, row_values in zip(weights, values):
                for feature_index, value in enumerate(row_values):
                    row[feature_index] -= learning_rate * (
                        error * value + l2 * row[feature_index]
                    )
            bias -= learning_rate * error

    artifact = TemporalCnnArtifact(
        model_id=model_id,
        version=version,
        kernel_size=kernel_size,
        weights=tuple(tuple(row) for row in weights),
        bias=bias,
        trained_sample_count=len(samples),
        training_seed=seed,
        license=artifact_license,
        notes="Yerel project annotations ile eğitilmiş motion-feature temporal CNN.",
    )
    model = TemporalCnnCandidateModel(artifact)
    return model, evaluate_temporal_cnn(model, examples)


def evaluate_temporal_cnn(
    model: TemporalCnnCandidateModel,
    examples: list[TemporalCnnTrainingExample],
    *,
    threshold: float = 0.5,
) -> TemporalCnnTrainingMetrics:

    if not 0 < threshold < 1:
        raise ValueError("evaluation threshold 0 ile 1 arasında olmalı")
    labels_and_scores = [
        (_is_positive(sample.t, example.positive_intervals), model.predict_probability(example.profile, index))
        for example in examples
        for index, sample in enumerate(example.profile)
    ]
    if not labels_and_scores:
        raise ValueError("evaluation için en az bir motion sample gerekir")
    positives = sum(label for label, _ in labels_and_scores)
    negatives = len(labels_and_scores) - positives
    true_positive = sum(label and score >= threshold for label, score in labels_and_scores)
    false_positive = sum(not label and score >= threshold for label, score in labels_and_scores)
    loss = sum(
        -((1.0 if label else 0.0) * _log_safe(score)
          + (0.0 if label else 1.0) * _log_safe(1.0 - score))
        for label, score in labels_and_scores
    ) / len(labels_and_scores)
    from .candidate_intervals import IntervalConfig, build_candidate_intervals
    ev_total = ev_hit = 0
    for example in examples:
        if not example.positive_intervals:
            continue
        duration = example.profile[-1].t + 1.0 if example.profile else 0.0
        ivs = build_candidate_intervals(
            model.score(example.profile), analysis_id="eval",
            video_id=example.video_id, duration_seconds=max(duration, 1.0),
            model_id=model.artifact.model_id, config=IntervalConfig())
        for g0, g1 in example.positive_intervals:
            ev_total += 1
            ev_hit += any(iv.start_time <= g1 and iv.end_time >= g0 for iv in ivs)

    return TemporalCnnTrainingMetrics(
        sample_count=len(labels_and_scores),
        positive_count=positives,
        mean_loss=loss,
        recall_at_half=true_positive / positives if positives else 0.0,
        false_positive_rate_at_half=false_positive / negatives if negatives else 0.0,
        interval_event_recall=ev_hit / ev_total if ev_total else 0.0,
    )


def _window(profile: list[MotionSample], index: int, kernel_size: int) -> tuple[tuple[float, ...], ...]:
    radius = kernel_size // 2
    values: list[tuple[float, ...]] = []
    for offset in range(-radius, radius + 1):
        source_index = index + offset
        if 0 <= source_index < len(profile):
            sample = profile[source_index]
            values.append((sample.changed, sample.fg, sample.mad, sample.activity))
        else:
            values.append((0.0,) * len(FEATURE_SCHEMA))
    return tuple(values)


def _is_positive(timestamp: float, intervals: tuple[tuple[float, float], ...]) -> bool:
    return any(start <= timestamp <= end for start, end in intervals)


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + exp(-value))
    exp_value = exp(value)
    return exp_value / (1.0 + exp_value)


def _log_safe(value: float) -> float:
    from math import log

    return log(max(value, 1e-12))


__all__ = [
    "FEATURE_SCHEMA",
    "TemporalCnnArtifact",
    "TemporalCnnCandidateModel",
    "TemporalCnnTrainingExample",
    "TemporalCnnTrainingMetrics",
    "evaluate_temporal_cnn",
    "train_temporal_cnn",
]
