from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite

from ..domain.candidate import CandidateEvent, CandidateType, ScreeningSample


@dataclass(frozen=True)
class IntervalConfig:

    start_threshold: float = 0.65
    continue_threshold: float = 0.40
    end_patience: int = 3
    merge_gap_seconds: float = 2.0
    min_duration_seconds: float = 0.5
    threshold_version: str = "candidate-thresholds-v1"

    def __post_init__(self) -> None:
        if not 0 <= self.continue_threshold <= self.start_threshold <= 1:
            raise ValueError("candidate eşikleri 0 <= continue <= start <= 1 olmalı")
        if self.end_patience < 1:
            raise ValueError("end_patience en az 1 olmalı")
        if self.merge_gap_seconds < 0 or self.min_duration_seconds <= 0:
            raise ValueError("merge gap >= 0 ve min duration > 0 olmalı")
        if not self.threshold_version:
            raise ValueError("threshold_version boş olamaz")


_SCORE_FIELDS = (
    "anomaly_score",
    "interaction_score",
    "fall_score",
    "fire_smoke_score",
    "vehicle_conflict_score",
    "tampering_score",
)


def sample_score(sample: ScreeningSample) -> float:

    return max(getattr(sample, field) for field in _SCORE_FIELDS)


def adaptive_saturation_shift(
    samples: list[ScreeningSample],
    *,
    start_threshold: float,
    saturation: float = 0.95,
    raised_threshold: float = 0.85,
    warmup_samples: int = 30,
) -> list[ScreeningSample]:

    out: list[ScreeningSample] = []
    seen_saturation = False
    for index, sample in enumerate(samples):
        raised = seen_saturation and index >= warmup_samples
        shift = start_threshold - (raised_threshold if raised else start_threshold)
        score = sample_score(sample)
        if shift:
            adjusted = min(max(score + shift, 0.0), 1.0)
            out.append(sample.model_copy(update={
                "anomaly_score": adjusted,
                "interaction_score": 0.0, "fall_score": 0.0,
                "fire_smoke_score": 0.0, "vehicle_conflict_score": 0.0,
                "tampering_score": 0.0,
            }))
        else:
            out.append(sample)
        seen_saturation = seen_saturation or score >= saturation
    return out


def build_candidate_intervals(
    samples: Iterable[ScreeningSample],
    *,
    analysis_id: str,
    video_id: str,
    duration_seconds: float,
    model_id: str,
    config: IntervalConfig | None = None,
) -> list[CandidateEvent]:

    cfg = config or IntervalConfig()
    if not analysis_id or not video_id or not model_id:
        raise ValueError("analysis_id, video_id ve model_id zorunludur")
    if not isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds pozitif ve sonlu olmalı")

    ordered = list(samples)
    _validate_samples(ordered, duration_seconds)
    if not ordered:
        return []
    step = _sample_step(ordered, duration_seconds)
    intervals: list[list[ScreeningSample]] = []
    current: list[ScreeningSample] = []
    low_count = 0

    for sample in ordered:
        score = sample_score(sample)
        if not current:
            if score >= cfg.start_threshold:
                current = [sample]
                low_count = 0
            continue

        current.append(sample)
        if score >= cfg.continue_threshold:
            low_count = 0
        else:
            low_count += 1
            if low_count >= cfg.end_patience:
                intervals.append(current)
                current = []
                low_count = 0

    if current:
        intervals.append(current)

    candidates = [
        _candidate_from_samples(
            group,
            index=index,
            analysis_id=analysis_id,
            video_id=video_id,
            duration_seconds=duration_seconds,
            model_id=model_id,
            threshold_version=cfg.threshold_version,
            step=step,
            min_duration=cfg.min_duration_seconds,
        )
        for index, group in enumerate(intervals, start=1)
    ]
    return merge_candidate_events(candidates, gap_seconds=cfg.merge_gap_seconds)


def merge_candidate_events(
    candidates: Iterable[CandidateEvent], *, gap_seconds: float = 2.0
) -> list[CandidateEvent]:

    if gap_seconds < 0:
        raise ValueError("gap_seconds negatif olamaz")
    ordered = sorted(candidates, key=lambda item: (item.start_time, item.end_time))
    if not ordered:
        return []
    merged: list[CandidateEvent] = [ordered[0]]
    for candidate in ordered[1:]:
        previous = merged[-1]
        if (
            candidate.candidate_type == previous.candidate_type
            and candidate.start_time - previous.end_time <= gap_seconds
        ):
            merged[-1] = _merge_pair(previous, candidate)
        else:
            merged.append(candidate)
    return merged


def _validate_samples(samples: list[ScreeningSample], duration: float) -> None:
    previous = -1.0
    for sample in samples:
        if sample.timestamp < previous:
            raise ValueError("screening sample timestamp'leri sıralı olmalı")
        if sample.timestamp > duration:
            raise ValueError("screening sample video süresi dışına çıkamaz")
        previous = sample.timestamp


def _sample_step(samples: list[ScreeningSample], duration: float) -> float:
    deltas = [b.timestamp - a.timestamp for a, b in zip(samples, samples[1:])]
    positive = [delta for delta in deltas if delta > 0]
    if positive:
        return min(positive)
    return min(max(duration, 0.5), 1.0)


def _candidate_from_samples(
    samples: list[ScreeningSample],
    *,
    index: int,
    analysis_id: str,
    video_id: str,
    duration_seconds: float,
    model_id: str,
    threshold_version: str,
    step: float,
    min_duration: float,
) -> CandidateEvent:
    peak = max(samples, key=sample_score)
    start = max(0.0, samples[0].timestamp - step)
    end = min(duration_seconds, max(samples[-1].timestamp + step, start + min_duration))
    if end <= start:
        end = min(duration_seconds, start + min_duration)
        start = max(0.0, end - min_duration)
    scores = {field: max(getattr(sample, field) for sample in samples) for field in _SCORE_FIELDS}
    candidate_type, type_signal = _candidate_type(scores)
    trigger_signals = sorted(
        {
            type_signal,
            "score_above_start_threshold",
            *(
                signal
                for signal, field in (
                    ("anomaly", "anomaly_score"),
                    ("interaction", "interaction_score"),
                    ("fall", "fall_score"),
                    ("fire_smoke", "fire_smoke_score"),
                    ("vehicle_conflict", "vehicle_conflict_score"),
                    ("tampering", "tampering_score"),
                )
                if scores[field] >= 0.4
            ),
        }
    )
    return CandidateEvent(
        candidate_id=f"{analysis_id}-candidate-{index:04d}",
        analysis_id=analysis_id,
        video_id=video_id,
        start_time=start,
        peak_time=peak.timestamp,
        end_time=end,
        candidate_type=candidate_type,
        peak_score=sample_score(peak),
        anomaly_score=scores["anomaly_score"],
        interaction_score=scores["interaction_score"],
        fall_score=scores["fall_score"],
        fire_score=scores["fire_smoke_score"],
        vehicle_score=scores["vehicle_conflict_score"],
        tampering_score=scores["tampering_score"],
        image_quality=min(sample.image_quality for sample in samples),
        trigger_signals=trigger_signals,
        screening_model_id=model_id,
        threshold_version=threshold_version,
    )


def _candidate_type(scores: dict[str, float]) -> tuple[CandidateType, str]:
    field, score = max(scores.items(), key=lambda item: item[1])
    del score
    mapping = {
        "interaction_score": (CandidateType.INTENSE_PERSON_INTERACTION, "interaction_signal"),
        "fall_score": (CandidateType.POSSIBLE_FALL, "fall_signal"),
        "fire_smoke_score": (CandidateType.FIRE_SMOKE_CANDIDATE, "fire_smoke_signal"),
        "vehicle_conflict_score": (
            CandidateType.VEHICLE_COLLISION,
            "vehicle_conflict_signal",
        ),
        "tampering_score": (CandidateType.CAMERA_OCCLUSION, "tampering_signal"),
    }
    return mapping.get(field, (CandidateType.UNKNOWN_ANOMALY, "anomaly_signal"))


def _merge_pair(left: CandidateEvent, right: CandidateEvent) -> CandidateEvent:
    scores = {
        "anomaly_score": max(left.anomaly_score, right.anomaly_score),
        "interaction_score": max(left.interaction_score, right.interaction_score),
        "fall_score": max(left.fall_score, right.fall_score),
        "fire_score": max(left.fire_score, right.fire_score),
        "vehicle_score": max(left.vehicle_score, right.vehicle_score),
        "tampering_score": max(left.tampering_score, right.tampering_score),
    }
    peak_source = left if left.peak_score >= right.peak_score else right
    return left.model_copy(
        update={
            "start_time": min(left.start_time, right.start_time),
            "peak_time": peak_source.peak_time,
            "end_time": max(left.end_time, right.end_time),
            "peak_score": max(left.peak_score, right.peak_score),
            **scores,
            "image_quality": min(left.image_quality, right.image_quality),
            "trigger_signals": sorted(set(left.trigger_signals + right.trigger_signals)),
        }
    )


__all__ = [
    "IntervalConfig",
    "build_candidate_intervals",
    "merge_candidate_events",
    "sample_score",
]
