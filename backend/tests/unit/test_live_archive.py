from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.provenance import AnalysisProvenance
from dortgoz.domain.video import VideoMetadata
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.incident_media import IncidentMediaService
from dortgoz.services.live_archive import clip_feed, list_live_clips, prune_live_clips

FEED = "kamera1"
SEGMENT_EPOCH = 1787822000
VIDEO_ID = "00000000-0000-0000-0000-000000000301"


def _live_repository(
    media_root: Path, *, start: float, peak: float, end: float, segments: int = 2
) -> InMemoryEventRepository:
    repository = InMemoryEventRepository()
    feed_dir = media_root / "canli" / FEED
    feed_dir.mkdir(parents=True)
    for index in range(segments):
        segment = feed_dir / f"seg_{SEGMENT_EPOCH + index * 30}.mp4"
        segment.write_bytes(f"segment-{index}".encode())
    first = feed_dir / f"seg_{SEGMENT_EPOCH}.mp4"
    media_path = f"canli/{FEED}/{first.name}"
    repository.create_video(
        VideoMetadata(
            video_id=VIDEO_ID,
            original_filename=first.name,
            stored_filename=f"{VIDEO_ID}.mp4",
            media_path=media_path,
            file_size_bytes=first.stat().st_size,
            file_hash_sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
            container="mp4",
            codec="h264",
            width=640,
            height=360,
            fps=25,
            duration_seconds=30,
            has_audio=False,
            time_base="1/25",
        )
    )
    repository.create_analysis(
        VIDEO_ID,
        AnalysisProvenance(
            contract_version="1.0.0",
            config_version="test-v1",
            code_revision="test-revision",
        ),
        analysis_id="analysis-live-1",
    )
    repository.save_candidate(
        CandidateEvent(
            candidate_id="candidate-live-1",
            analysis_id="analysis-live-1",
            video_id=VIDEO_ID,
            start_time=start,
            peak_time=peak,
            end_time=end,
            candidate_type=CandidateType.UNKNOWN_ANOMALY,
            peak_score=0.8,
            anomaly_score=0.8,
            trigger_signals=["test"],
            screening_model_id="test",
            threshold_version="test-v1",
        )
    )
    repository.save_event(
        VerifiedEvent(
            event_id="event-live-1",
            analysis_id="analysis-live-1",
            video_id=VIDEO_ID,
            candidate_id="candidate-live-1",
            status=EventStatus.HUMAN_REVIEW,
            event_type=VerifiedEventType.UNKNOWN_ANOMALY,
            start_time=start,
            peak_time=peak,
            end_time=end,
        )
    )
    return repository


def _service(repository, media_root: Path, concat_calls: list):
    async def write_clip(
        source: Path, target: Path, start: float, end: float, _timeout: float
    ) -> None:
        target.write_bytes(f"clip:{source.name}:{start}:{end}".encode())

    async def read_frame(_source: Path, timestamp: float, _width: int) -> bytes:
        return f"jpeg:{timestamp}".encode()

    async def fake_concat(segments: list[Path], target: Path, _timeout: float) -> None:
        concat_calls.append([item.name for item in segments])
        target.write_bytes(b"stitched")

    service = IncidentMediaService(
        repository,
        media_root=media_root,
        before_seconds=8,
        after_seconds=8,
        live_segment_seconds=30,
        live_tail_seconds=30,
        clip_writer=write_clip,
        frame_reader=read_frame,
    )
    return service, fake_concat


@pytest.mark.asyncio
async def test_event_touching_the_segment_end_is_stitched(tmp_path, monkeypatch) -> None:
    media_root = tmp_path / "media"
    repository = _live_repository(media_root, start=24, peak=27, end=30)
    calls: list = []
    service, fake_concat = _service(repository, media_root, calls)
    monkeypatch.setattr("dortgoz.services.incident_media.concat_segments", fake_concat)

    media = await service.prepare("event-live-1")

    assert calls == [[f"seg_{SEGMENT_EPOCH}.mp4", f"seg_{SEGMENT_EPOCH + 30}.mp4"]]
    assert media.clip_start == pytest.approx(16.0)
    assert media.clip_end == pytest.approx(60.0)
    assert media.source_refs == [
        f"canli/{FEED}/seg_{SEGMENT_EPOCH}.mp4",
        f"canli/{FEED}/seg_{SEGMENT_EPOCH + 30}.mp4",
    ]
    assert not (media_root / "_incident_media" / media.media_id / ".stitched.mp4").exists()


@pytest.mark.asyncio
async def test_event_inside_one_segment_is_not_stitched(tmp_path, monkeypatch) -> None:
    media_root = tmp_path / "media"
    repository = _live_repository(media_root, start=10, peak=12, end=15)
    calls: list = []
    service, fake_concat = _service(repository, media_root, calls)
    monkeypatch.setattr("dortgoz.services.incident_media.concat_segments", fake_concat)

    media = await service.prepare("event-live-1")

    assert calls == []
    assert media.clip_start == pytest.approx(2.0)
    assert media.clip_end == pytest.approx(23.0)
    assert media.source_refs == [f"canli/{FEED}/seg_{SEGMENT_EPOCH}.mp4"]


@pytest.mark.asyncio
async def test_archive_lists_the_clip_under_its_camera(tmp_path, monkeypatch) -> None:
    media_root = tmp_path / "media"
    repository = _live_repository(media_root, start=10, peak=12, end=15)
    calls: list = []
    service, fake_concat = _service(repository, media_root, calls)
    monkeypatch.setattr("dortgoz.services.incident_media.concat_segments", fake_concat)
    media = await service.prepare("event-live-1")

    listed = list_live_clips(repository, media_root)

    assert len(listed) == 1
    assert listed[0]["feed"] == FEED
    assert listed[0]["available"] is True
    assert listed[0]["clip_url"] == f"/media/{media.clip_ref}"
    assert list_live_clips(repository, media_root, feed="baska-kamera") == []


@pytest.mark.asyncio
async def test_retention_drops_the_file_and_keeps_the_record(tmp_path, monkeypatch) -> None:
    media_root = tmp_path / "media"
    repository = _live_repository(media_root, start=10, peak=12, end=15)
    calls: list = []
    service, fake_concat = _service(repository, media_root, calls)
    monkeypatch.setattr("dortgoz.services.incident_media.concat_segments", fake_concat)
    media = await service.prepare("event-live-1")
    aged = media.model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(hours=200)}
    )
    repository._incident_media[media.media_id] = aged

    removed = prune_live_clips(
        repository, media_root, retention_hours=72, max_per_feed=200
    )

    assert removed == 2
    assert not (media_root / media.clip_ref).is_file()
    assert repository.get_incident_media(media.media_id) is not None
    assert list_live_clips(repository, media_root)[0]["available"] is False


@pytest.mark.asyncio
async def test_a_file_analysis_clip_never_enters_the_live_archive(tmp_path) -> None:
    media_root = tmp_path / "media"
    repository = _live_repository(media_root, start=10, peak=12, end=15)
    video = repository.get_video(VIDEO_ID)
    assert video is not None

    assert clip_feed_for_plain_upload(video.media_path) == ""
    assert list_live_clips(repository, media_root) == []


def clip_feed_for_plain_upload(media_path: str) -> str:
    from dortgoz.domain.media import IncidentMedia

    stub = IncidentMedia(
        media_id="00000000-0000-0000-0000-0000000000ff",
        event_id="event-live-1",
        analysis_id="analysis-live-1",
        video_id=VIDEO_ID,
        event_revision=1,
        source_refs=["yuklenen.mp4"],
        source_file_sha256="0" * 64,
        clip_ref="_incident_media/x/incident.mp4",
        thumbnail_ref="_incident_media/x/thumbnail.jpg",
        clip_start=0,
        clip_end=1,
        peak_time=0.5,
        pre_capture_seconds=8,
        post_capture_seconds=8,
        clip_sha256="1" * 64,
        thumbnail_sha256="2" * 64,
        clip_size_bytes=1,
        thumbnail_size_bytes=1,
    )
    assert media_path == "canli/kamera1/seg_1787822000.mp4"
    return clip_feed(stub)


@pytest.mark.asyncio
async def test_browse_separates_live_and_file_events(tmp_path, monkeypatch) -> None:
    from dortgoz.services.event_browser import EventFilters, browse_events

    media_root = tmp_path / "media"
    repository = _live_repository(media_root, start=10, peak=12, end=15)
    calls: list = []
    service, fake_concat = _service(repository, media_root, calls)
    monkeypatch.setattr("dortgoz.services.incident_media.concat_segments", fake_concat)
    await service.prepare("event-live-1")

    everything = browse_events(repository, media_root, EventFilters())
    live_only = browse_events(repository, media_root, EventFilters(origin="live"))
    file_only = browse_events(repository, media_root, EventFilters(origin="analysis"))

    assert everything["total"] == 1
    assert live_only["total"] == 1
    assert file_only["total"] == 0
    assert everything["facets"]["origins"] == {"live": 1, "analysis": 0}
    row = live_only["events"][0]
    assert row["live"] is True
    assert row["feed"] == FEED
    assert row["status"] == "human_review"
    assert row["clip_url"] is not None


@pytest.mark.asyncio
async def test_browse_filters_by_status_and_free_text(tmp_path) -> None:
    from dortgoz.services.event_browser import EventFilters, browse_events

    media_root = tmp_path / "media"
    repository = _live_repository(media_root, start=10, peak=12, end=15)

    by_status = browse_events(
        repository, media_root, EventFilters(status="human_review")
    )
    wrong_status = browse_events(repository, media_root, EventFilters(status="confirmed"))
    by_text = browse_events(repository, media_root, EventFilters(query=FEED))
    no_match = browse_events(repository, media_root, EventFilters(query="bulunmayan"))

    assert by_status["total"] == 1
    assert wrong_status["total"] == 0
    assert by_text["total"] == 1
    assert no_match["total"] == 0
