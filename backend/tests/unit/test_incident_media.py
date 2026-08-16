"""Olay klibi üretimi, idempotency ve event revision yenilemesi."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.memory import AnalysisStatus
from dortgoz.domain.provenance import AnalysisProvenance
from dortgoz.domain.video import VideoMetadata
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.incident_media import IncidentMediaService

VIDEO_ID = "00000000-0000-0000-0000-000000000201"


def _repository(media_root: Path) -> InMemoryEventRepository:
    repository = InMemoryEventRepository()
    stored_filename = f"{VIDEO_ID}.mp4"
    source = media_root / stored_filename
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-video")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    repository.create_video(
        VideoMetadata(
            video_id=VIDEO_ID,
            original_filename="source.mp4",
            stored_filename=stored_filename,
            media_path=stored_filename,
            file_size_bytes=source.stat().st_size,
            file_hash_sha256=digest,
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
        analysis_id="analysis-test-1",
    )
    repository.save_candidate(
        CandidateEvent(
            candidate_id="candidate-test-1",
            analysis_id="analysis-test-1",
            video_id=VIDEO_ID,
            start_time=10,
            peak_time=12,
            end_time=15,
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
            event_id="event-test-1",
            analysis_id="analysis-test-1",
            video_id=VIDEO_ID,
            candidate_id="candidate-test-1",
            status=EventStatus.HUMAN_REVIEW,
            event_type=VerifiedEventType.UNKNOWN_ANOMALY,
            start_time=10,
            peak_time=12,
            end_time=15,
        )
    )
    return repository


@pytest.mark.asyncio
async def test_prepare_writes_hashed_clip_and_thumbnail_once(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    repository = _repository(media_root)
    writes: list[tuple[float, float]] = []

    async def write_clip(
        _source: Path, target: Path, start: float, end: float, _timeout: float
    ) -> None:
        writes.append((start, end))
        target.write_bytes(f"clip:{start}:{end}".encode())

    async def read_frame(_source: Path, timestamp: float, width: int) -> bytes:
        return f"jpeg:{timestamp}:{width}".encode()

    service = IncidentMediaService(
        repository,
        media_root=media_root,
        before_seconds=8,
        after_seconds=8,
        clip_writer=write_clip,
        frame_reader=read_frame,
    )

    first = await service.prepare("event-test-1")
    second = await service.prepare("event-test-1")

    assert writes == [(2, 23)]
    assert second == first
    assert first.clip_start == 2
    assert first.clip_end == 23
    assert first.peak_time == 12
    assert first.source_refs == [f"{VIDEO_ID}.mp4"]
    assert (media_root / first.clip_ref).is_file()
    assert (media_root / first.thumbnail_ref).is_file()
    assert hashlib.sha256((media_root / first.clip_ref).read_bytes()).hexdigest() == first.clip_sha256


@pytest.mark.asyncio
async def test_event_revision_regenerates_same_media_identity(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    repository = _repository(media_root)
    writes = 0

    async def write_clip(
        _source: Path, target: Path, _start: float, end: float, _timeout: float
    ) -> None:
        nonlocal writes
        writes += 1
        target.write_bytes(f"clip-revision:{writes}:{end}".encode())

    async def read_frame(_source: Path, _timestamp: float, _width: int) -> bytes:
        return f"jpeg:{writes}".encode()

    service = IncidentMediaService(
        repository,
        media_root=media_root,
        clip_writer=write_clip,
        frame_reader=read_frame,
    )
    first = await service.prepare("event-test-1")
    event = repository.get_event("event-test-1")
    assert event is not None
    repository.save_event(
        event.model_copy(
            update={
                "end_time": 18,
                "updated_at": datetime.now(UTC),
                "revision": event.revision + 1,
            }
        )
    )

    second = await service.prepare("event-test-1")

    assert writes == 2
    assert second.media_id == first.media_id
    assert second.revision == 2
    assert second.event_revision == 2
    assert second.clip_end == 26
    assert second.clip_sha256 != first.clip_sha256


@pytest.mark.asyncio
async def test_finalize_only_runs_for_terminal_analysis(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    repository = _repository(media_root)

    async def write_clip(
        _source: Path, target: Path, _start: float, _end: float, _timeout: float
    ) -> None:
        target.write_bytes(b"clip")

    async def read_frame(_source: Path, _timestamp: float, _width: int) -> bytes:
        return b"jpeg"

    service = IncidentMediaService(
        repository,
        media_root=media_root,
        clip_writer=write_clip,
        frame_reader=read_frame,
    )

    assert await service.finalize_analysis("analysis-test-1") == []
    repository.update_analysis_status(
        "analysis-test-1", AnalysisStatus.REVIEW_REQUIRED.value, 1.0
    )
    saved = await service.finalize_analysis("analysis-test-1")

    assert [item.event_id for item in saved] == ["event-test-1"]
