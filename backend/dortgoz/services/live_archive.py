from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..domain.media import IncidentMedia
from ..repositories.protocols import EventRepository
from .live_clip import LIVE_DIR_NAME, feed_from_media_path, feed_from_run_id

LOGGER = logging.getLogger(__name__)


@dataclass
class LiveClipEntry:
    media_id: str
    event_id: str
    feed: str
    category: str
    risk: str
    verdict: str
    recorded_at: float
    duration_seconds: float
    size_bytes: int
    clip_url: str | None
    thumbnail_url: str | None
    available: bool


def clip_feed(media: IncidentMedia) -> str:
    for ref in media.source_refs:
        feed = feed_from_media_path(ref)
        if feed:
            return feed
    return feed_from_run_id(media.analysis_id)


def _artifact_exists(media_root: Path, ref: str) -> bool:
    candidate = (media_root / ref).resolve()
    return candidate.is_relative_to(media_root.resolve()) and candidate.is_file()


def list_live_clips(
    repository: EventRepository,
    media_root: Path,
    *,
    feed: str = "",
    limit: int = 200,
) -> list[dict]:
    entries: list[LiveClipEntry] = []
    for media in repository.list_incident_media():
        name = clip_feed(media)
        if not name or (feed and name != feed):
            continue
        event = repository.get_event(media.event_id)
        review = event.review if event is not None else None
        available = _artifact_exists(media_root, media.clip_ref)
        entries.append(
            LiveClipEntry(
                media_id=media.media_id,
                event_id=media.event_id,
                feed=name,
                category=(
                    event.legacy_event_type or str(event.event_type)
                    if event is not None
                    else "bilinmeyen"
                ),
                risk=(
                    str(event.risk.level) if event is not None and event.risk else "undetermined"
                ),
                verdict=str(review.decision) if review is not None else "",
                recorded_at=media.created_at.timestamp(),
                duration_seconds=round(media.clip_end - media.clip_start, 2),
                size_bytes=media.clip_size_bytes,
                clip_url=f"/media/{media.clip_ref}" if available else None,
                thumbnail_url=(
                    f"/media/{media.thumbnail_ref}"
                    if _artifact_exists(media_root, media.thumbnail_ref)
                    else None
                ),
                available=available,
            )
        )
    entries.sort(key=lambda item: item.recorded_at, reverse=True)
    return [asdict(item) for item in entries[:limit]]


def prune_live_clips(
    repository: EventRepository,
    media_root: Path,
    *,
    retention_hours: float,
    max_per_feed: int,
) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    by_feed: dict[str, list[IncidentMedia]] = {}
    for media in repository.list_incident_media():
        name = clip_feed(media)
        if name:
            by_feed.setdefault(name, []).append(media)

    removed = 0
    for items in by_feed.values():
        items.sort(key=lambda item: item.created_at, reverse=True)
        for index, media in enumerate(items):
            expired = media.created_at < cutoff or index >= max_per_feed
            if not expired:
                continue
            removed += _drop_artifacts(media_root, media)
    if removed:
        LOGGER.info("canlı arşiv budandı: %d dosya silindi", removed)
    return removed


def _drop_artifacts(media_root: Path, media: IncidentMedia) -> int:
    root = media_root.resolve()
    dropped = 0
    for ref in (media.clip_ref, media.thumbnail_ref):
        candidate = (media_root / ref).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            continue
        try:
            candidate.unlink()
            dropped += 1
        except OSError:
            LOGGER.warning("canlı arşiv dosyası silinemedi: %s", ref)
    return dropped


__all__ = [
    "LIVE_DIR_NAME",
    "LiveClipEntry",
    "clip_feed",
    "list_live_clips",
    "prune_live_clips",
]
