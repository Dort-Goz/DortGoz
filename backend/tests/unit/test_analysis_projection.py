from __future__ import annotations

import json
from pathlib import Path

from dortgoz.domain.event import EventStatus
from dortgoz.domain.memory import AnalysisStatus
from dortgoz.domain.video import VideoMetadata, VideoProbe
from dortgoz.events import Event, IncidentUpdate, RunStatus
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.analysis_projection import RuntimeAnalysisProjection


def _video() -> VideoMetadata:
    video_id = "00000000-0000-0000-0000-000000000901"
    return VideoMetadata(
        video_id=video_id,
        original_filename="offline.mp4",
        stored_filename=f"{video_id}.mp4",
        media_path=f"{video_id}.mp4",
        file_size_bytes=100,
        file_hash_sha256="a" * 64,
        container="mov",
        codec="h264",
        width=640,
        height=360,
        fps=25,
        duration_seconds=90,
        has_audio=False,
        time_base="1/12800",
    )


def _emit(projection: RuntimeAnalysisProjection, payload, feed: str = "") -> None:
    projection.observe(Event.wrap(payload, feed=feed))


def test_completed_runtime_run_projects_one_reviewable_event(tmp_path: Path) -> None:
    repository = InMemoryEventRepository()
    video = repository.create_video(_video())
    run_id = "offline-run-1"
    (tmp_path / f"{run_id}.meta.json").write_text(
        json.dumps({"model": "local-vlm", "mode": "temkinli"}), encoding="utf-8"
    )
    projection = RuntimeAnalysisProjection(repository, tmp_path)

    _emit(
        projection,
        RunStatus(
            run_id=run_id,
            state="processing",
            video=video.stored_filename,
        ),
    )
    _emit(
        projection,
        IncidentUpdate(
            incident_id="incident-1",
            t=14,
            phase="basladi",
            title="Fiziksel kavga",
            anomaly_type="kavga",
            risk="yuksek",
            detail="İki kişi kavga ediyor.",
            olay_baslangic=10,
            olay_bitis=20,
        ),
    )
    # Event koşu bitmeden oluşur. Nöbet kararı aynı yayın turunda bu kimliğe
    # bağlanabilir; JSONL yan kanalı gerekmez.
    assert projection.event_id_for("", "incident-1") == f"{run_id}:incident-1"
    assert repository.get_event(f"{run_id}:incident-1") is not None
    _emit(
        projection,
        IncidentUpdate(
            incident_id="incident-1",
            t=18,
            phase="sonuclandi",
            title="Fiziksel kavga",
            anomaly_type="kavga",
            risk="yuksek",
            detail="Olay sona erdi.",
            olay_baslangic=10,
            olay_bitis=20,
        ),
    )
    _emit(
        projection,
        RunStatus(run_id=run_id, state="done", video=video.stored_filename),
    )

    analysis = repository.get_analysis(run_id)
    assert analysis is not None
    assert analysis.status == AnalysisStatus.REVIEW_REQUIRED
    assert analysis.provenance.model_runs[0].model_id == "local-vlm"
    events = repository.list_events(run_id)
    assert len(events) == 1
    event = events[0]
    assert event.event_id == f"{run_id}:incident-1"
    assert event.status == EventStatus.HUMAN_REVIEW
    assert (event.start_time, event.peak_time, event.end_time) == (10, 18, 20)
    assert repository.find_video_by_stored_filename(video.stored_filename) == video


def test_unregistered_media_and_failed_run_do_not_create_training_events(
    tmp_path: Path,
) -> None:
    repository = InMemoryEventRepository()
    projection = RuntimeAnalysisProjection(repository, tmp_path)

    _emit(
        projection,
        RunStatus(run_id="missing", state="processing", video="missing.mp4"),
    )
    _emit(
        projection,
        IncidentUpdate(
            incident_id="ignored",
            t=1,
            phase="sonuclandi",
            title="Kayıtsız olay",
            anomaly_type="bilinmeyen",
            risk="orta",
        ),
    )
    _emit(projection, RunStatus(run_id="missing", state="done", video="missing.mp4"))

    assert repository.get_analysis("missing") is None

    video = repository.create_video(_video())
    _emit(
        projection,
        RunStatus(run_id="failed", state="processing", video=video.stored_filename),
    )
    _emit(
        projection,
        RunStatus(
            run_id="failed",
            state="error",
            video=video.stored_filename,
            detail="model yanıt vermedi",
        ),
    )
    analysis = repository.get_analysis("failed")
    assert analysis is not None
    assert analysis.status == AnalysisStatus.FAILED
    assert analysis.error == "model yanıt vermedi"
    assert repository.list_events("failed") == []


def test_mock_virtual_source_keeps_operator_feedback_canonical(tmp_path: Path) -> None:
    repository = InMemoryEventRepository()
    projection = RuntimeAnalysisProjection(
        repository, tmp_path, allow_virtual_sources=True
    )

    _emit(projection, RunStatus(run_id="mock-1", state="processing"))
    _emit(
        projection,
        IncidentUpdate(
            incident_id="mock-incident",
            t=3,
            phase="basladi",
            title="Mock olay",
            anomaly_type="bilinmeyen",
            risk="orta",
        ),
    )

    event_id = projection.event_id_for("", "mock-incident")
    assert event_id == "mock-1:mock-incident"
    event = repository.get_event(event_id)
    assert event is not None
    video = repository.get_video(event.video_id)
    assert video is not None
    assert "NOT_TRAINING_ELIGIBLE" in video.warnings


async def test_live_runtime_source_is_registered_without_copy(tmp_path: Path) -> None:
    repository = InMemoryEventRepository()

    async def fake_probe(_path: Path) -> VideoProbe:
        return VideoProbe(
            container="mp4",
            codec="h264",
            width=640,
            height=360,
            fps=25,
            duration_seconds=30,
            has_audio=False,
            time_base="1/25",
        )

    source = tmp_path / "seg_1.mp4"
    source.write_bytes(b"live-segment")
    projection = RuntimeAnalysisProjection(
        repository, tmp_path, source_probe=fake_probe
    )
    metadata = await projection.register_runtime_source(
        "live-run", "canli/KAM-1/seg_1.mp4", source
    )

    assert source.read_bytes() == b"live-segment"
    assert metadata.media_path == "canli/KAM-1/seg_1.mp4"
    assert "RUNTIME_SOURCE_REFERENCE" in metadata.warnings
    _emit(
        projection,
        RunStatus(run_id="live-run", state="processing", video="canli/KAM-1/seg_1.mp4"),
        feed="KAM-1",
    )
    _emit(
        projection,
        IncidentUpdate(
            incident_id="live-event",
            t=5,
            phase="basladi",
            title="Canlı olay",
            anomaly_type="vandalizm",
            risk="orta",
        ),
        feed="KAM-1",
    )
    assert projection.event_id_for("KAM-1", "live-event") == "live-run:live-event"
