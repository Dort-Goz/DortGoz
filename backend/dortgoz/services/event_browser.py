from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from ..domain.event import EventStatus, VerifiedEvent
from ..repositories.protocols import EventRepository
from .live_clip import feed_from_media_path, feed_from_run_id

MAX_LIMIT = 500


@dataclass
class EventRow:
    event_id: str
    analysis_id: str
    live: bool
    feed: str
    source_label: str
    category: str
    risk: str
    status: str
    verdict: str
    reviewer: str
    note: str
    start_time: float | None
    peak_time: float | None
    end_time: float | None
    recorded_at: float
    intervention_score: int
    intervention_band: str
    clip_url: str | None
    thumbnail_url: str | None
    evidence_count: int


@dataclass
class EventFilters:
    origin: str = "all"
    status: str = "all"
    urgency: str = "all"
    category: str = "all"
    feed: str = ""
    query: str = ""
    limit: int = 100
    offset: int = 0


def _origin(repository: EventRepository, event: VerifiedEvent) -> tuple[bool, str, str]:
    analysis = repository.get_analysis(event.analysis_id)
    video = repository.get_video(analysis.video_id) if analysis is not None else None
    feed = feed_from_run_id(event.analysis_id)
    if video is not None:
        feed = feed_from_media_path(video.media_path) or feed
    if feed:
        return True, feed, feed
    if video is None:
        return False, "", event.analysis_id
    return False, "", video.original_filename or video.stored_filename


def _category(event: VerifiedEvent) -> str:
    return event.legacy_event_type or str(event.event_type)


def _risk(event: VerifiedEvent) -> str:
    return str(event.risk.level) if event.risk is not None else "undetermined"


def _artifact(media_root: Path, ref: str) -> str | None:
    if not ref:
        return None
    candidate = (media_root / ref).resolve()
    if not candidate.is_relative_to(media_root.resolve()) or not candidate.is_file():
        return None
    return f"/media/{ref}"


def _matches(row: EventRow, filters: EventFilters) -> bool:
    if filters.origin == "live" and not row.live:
        return False
    if filters.origin == "analysis" and row.live:
        return False
    if filters.status != "all" and row.status != filters.status:
        return False
    if filters.urgency != "all" and row.intervention_band != filters.urgency:
        return False
    if filters.category != "all" and row.category != filters.category:
        return False
    if filters.feed and row.feed != filters.feed:
        return False
    if filters.query:
        needle = filters.query.casefold()
        haystack = " ".join(
            [row.source_label, row.category, row.note, row.reviewer, row.event_id]
        ).casefold()
        if needle not in haystack:
            return False
    return True


def browse_events(
    repository: EventRepository,
    media_root: Path,
    filters: EventFilters,
) -> dict:
    priorities = {
        item.event_id: item
        for item in repository.list_intervention_priorities()
    }
    media = {
        item.event_id: item
        for item in repository.list_incident_media()
    }
    rows: list[EventRow] = []
    for event in repository.list_all_events():
        live, feed, label = _origin(repository, event)
        review = event.review
        priority = priorities.get(event.event_id)
        clip = media.get(event.event_id)
        rows.append(
            EventRow(
                event_id=event.event_id,
                analysis_id=event.analysis_id,
                live=live,
                feed=feed,
                source_label=label,
                category=_category(event),
                risk=_risk(event),
                status=str(event.status),
                verdict=str(review.decision) if review is not None else "",
                reviewer=review.reviewer if review is not None else "",
                note=review.note if review is not None else "",
                start_time=event.start_time,
                peak_time=event.peak_time,
                end_time=event.end_time,
                recorded_at=event.created_at.timestamp(),
                intervention_score=priority.score if priority is not None else 0,
                intervention_band=(
                    str(priority.band) if priority is not None else "routine"
                ),
                clip_url=_artifact(media_root, clip.clip_ref) if clip is not None else None,
                thumbnail_url=(
                    _artifact(media_root, clip.thumbnail_ref) if clip is not None else None
                ),
                evidence_count=len(event.evidence),
            )
        )
    rows.sort(key=lambda row: row.recorded_at, reverse=True)
    selected = [row for row in rows if _matches(row, filters)]
    limit = max(1, min(filters.limit, MAX_LIMIT))
    offset = max(0, filters.offset)
    return {
        "events": [asdict(row) for row in selected[offset : offset + limit]],
        "total": len(selected),
        "offset": offset,
        "limit": limit,
        "facets": {
            "origins": {
                "live": sum(1 for row in rows if row.live),
                "analysis": sum(1 for row in rows if not row.live),
            },
            "statuses": _counts(rows, lambda row: row.status),
            "urgencies": _counts(rows, lambda row: row.intervention_band),
            "risks": _counts(rows, lambda row: row.risk),
            "categories": _counts(rows, lambda row: row.category),
            "feeds": _counts(rows, lambda row: row.feed, skip_empty=True),
        },
    }


def _counts(rows: list[EventRow], key, *, skip_empty: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = key(row)
        if skip_empty and not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


__all__ = ["EventFilters", "EventRow", "EventStatus", "browse_events"]
