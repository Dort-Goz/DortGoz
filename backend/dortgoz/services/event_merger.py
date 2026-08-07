"""Aynı analizdeki yinelenen doğrulanmış olaylar için saf merge kuralı."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from ..domain.event import EventStatus, VerifiedEvent


class EventMergeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temporal_iou_threshold: float = Field(default=0.5, ge=0, le=1)
    max_gap_seconds: float = Field(default=2.0, ge=0)
    version: str = "task-09-event-merge-v1"


class EventMergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[VerifiedEvent]
    duplicate_of: dict[str, str] = Field(default_factory=dict)
    merger_version: str


def merge_confirmed_events(
    events: list[VerifiedEvent], config: EventMergeConfig | None = None
) -> EventMergeResult:
    """Yalnız confirmed, aynı türdeki temporal duplicate'leri birleştirir.

    Girdi listesi ve rejected/human-review kayıtları değiştirilmez; ``duplicate_of``
    audit katmanının hangi confirmed olayın görünümde birleştiğini izlemesini sağlar.
    """

    active = config or EventMergeConfig()
    merged: list[VerifiedEvent] = []
    duplicates: dict[str, str] = {}
    for event in sorted(events, key=lambda item: (item.start_time or -1, item.event_id)):
        match_index = next(
            (
                index
                for index in range(len(merged) - 1, -1, -1)
                if _can_merge(merged[index], event, active)
            ),
            None,
        )
        if match_index is None:
            merged.append(event)
            continue
        anchor = merged[match_index]
        merged[match_index] = _merge_pair(anchor, event)
        duplicates[event.event_id] = anchor.event_id
    return EventMergeResult(events=merged, duplicate_of=duplicates, merger_version=active.version)


def _can_merge(left: VerifiedEvent, right: VerifiedEvent, config: EventMergeConfig) -> bool:
    if (
        left.status != EventStatus.CONFIRMED
        or right.status != EventStatus.CONFIRMED
        or left.analysis_id != right.analysis_id
        or left.video_id != right.video_id
        or left.event_type != right.event_type
    ):
        return False
    if None in (left.start_time, left.end_time, right.start_time, right.end_time):
        return False
    assert left.start_time is not None and left.end_time is not None
    assert right.start_time is not None and right.end_time is not None
    overlap = max(0.0, min(left.end_time, right.end_time) - max(left.start_time, right.start_time))
    union = max(left.end_time, right.end_time) - min(left.start_time, right.start_time)
    temporal_iou = overlap / union if union else 1.0
    gap = max(0.0, right.start_time - left.end_time, left.start_time - right.end_time)
    return temporal_iou >= config.temporal_iou_threshold or gap <= config.max_gap_seconds


def _merge_pair(left: VerifiedEvent, right: VerifiedEvent) -> VerifiedEvent:
    highest_confidence = max((left, right), key=lambda item: item.confidence or 0)
    evidence = {item.evidence_id: item for item in [*left.evidence, *right.evidence]}
    uncertainties = list(dict.fromkeys([*left.uncertainties, *right.uncertainties]))
    return left.model_copy(
        update={
            "start_time": min(left.start_time or 0, right.start_time or 0),
            "end_time": max(left.end_time or 0, right.end_time or 0),
            "peak_time": highest_confidence.peak_time,
            "confidence": highest_confidence.confidence,
            "before": highest_confidence.before or left.before or right.before,
            "during": highest_confidence.during or left.during or right.during,
            "after": highest_confidence.after or left.after or right.after,
            "evidence": list(evidence.values()),
            "uncertainties": uncertainties,
            "updated_at": datetime.now(UTC),
        }
    )


__all__ = ["EventMergeConfig", "EventMergeResult", "merge_confirmed_events"]
