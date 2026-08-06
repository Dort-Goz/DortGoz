"""Görev 09 duplicate event merge sözleşmesi."""

from __future__ import annotations

from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import EvidenceItem, EvidenceValidationResult, VerifiedEventType
from dortgoz.services.event_merger import merge_confirmed_events


def _event(identifier: str, start: float, end: float, *, status: EventStatus = EventStatus.CONFIRMED) -> VerifiedEvent:
    evidence = EvidenceItem(
        evidence_id=f"evidence-{identifier}",
        timestamp=(start + end) / 2,
        frame_id=f"frame-{identifier}",
        frame_path=f"runs/a/{identifier}.jpg",
        clip_path=f"runs/a/{identifier}.mp4",
        claim="Karede olağan dışı hareket gözleniyor.",
        source_model="fixture",
        validated=True,
    )
    validation = EvidenceValidationResult(
        candidate_id=f"candidate-{identifier}",
        schema_valid=True,
        timestamps_valid=True,
        evidence_valid=True,
        validated_evidence=[evidence],
        validator_version="fixture",
    )
    return VerifiedEvent(
        event_id=f"event-{identifier}",
        analysis_id="analysis-merge",
        video_id="video-merge",
        candidate_id=f"candidate-{identifier}",
        status=status,
        event_type=VerifiedEventType.UNKNOWN_ANOMALY,
        start_time=start,
        peak_time=(start + end) / 2,
        end_time=end,
        confidence=0.8,
        validation=validation if status == EventStatus.CONFIRMED else None,
        evidence=[evidence] if status == EventStatus.CONFIRMED else [],
    )


def test_merger_combines_confirmed_duplicates_but_keeps_rejected_audit() -> None:
    first = _event("first", 2, 6)
    duplicate = _event("duplicate", 5, 9)
    rejected = _event("rejected", 5, 9, status=EventStatus.REJECTED)

    result = merge_confirmed_events([first, duplicate, rejected])

    assert [event.event_id for event in result.events] == ["event-first", "event-rejected"]
    assert result.events[0].start_time == 2
    assert result.events[0].end_time == 9
    assert len(result.events[0].evidence) == 2
    assert result.duplicate_of == {"event-duplicate": "event-first"}
