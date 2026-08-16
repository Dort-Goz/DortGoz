"""Görev 06 local REST upload ve ortak hata sözleşmesi testleri."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dortgoz.api import router as api_module
from dortgoz.api.router import ApiRuntime
from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.provenance import AnalysisProvenance
from dortgoz.domain.video import VideoMetadata
from dortgoz.main import app


def metadata(video_id: str = "00000000-0000-0000-0000-000000000021") -> VideoMetadata:
    return VideoMetadata(
        video_id=video_id,
        original_filename="fixture.mp4",
        stored_filename=f"{video_id}.mp4",
        media_path=f"{video_id}.mp4",
        file_size_bytes=1024,
        file_hash_sha256="b" * 64,
        container="mov",
        codec="h264",
        width=640,
        height=480,
        fps=25,
        duration_seconds=90,
        has_audio=False,
        time_base="1/12800",
    )


def test_upload_and_review_routes_use_same_repository(monkeypatch, tmp_path: Path) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    video = metadata("00000000-0000-0000-0000-000000000022")

    class FakeIngest:
        async def ingest_file(self, source: Path, *, original_filename: str | None = None):
            assert source.is_file()
            assert original_filename == "upload.mp4"
            return video

    runtime.ingest = FakeIngest()
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/videos",
            files={"file": ("upload.mp4", b"fixture", "video/mp4")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["video_id"] == video.video_id
        fetched = client.get(f"/api/videos/{video.video_id}")
        assert fetched.status_code == 200


def test_legacy_http_error_also_uses_error_envelope() -> None:
    with TestClient(app) as client:
        missing = client.get("/api/runs/does-not-exist")

    assert missing.status_code == 404
    payload = missing.json()["error"]
    assert payload["code"] == "NOT_FOUND"
    assert payload["message"] == "koşu bulunamadı"
    assert payload["details"] == {"detail": "koşu bulunamadı"}


def test_framework_http_error_also_uses_error_envelope() -> None:
    with TestClient(app) as client:
        missing = client.get("/api/no-such-route")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_review_and_development_approval_are_separate_api_decisions(
    monkeypatch,
) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    video = metadata("00000000-0000-0000-0000-000000000023")
    analysis_id = "analysis-feedback-api"
    candidate = CandidateEvent(
        candidate_id="candidate-feedback-api",
        analysis_id=analysis_id,
        video_id=video.video_id,
        start_time=10,
        peak_time=12,
        end_time=15,
        candidate_type=CandidateType.POSSIBLE_FIGHT,
        peak_score=0.8,
        anomaly_score=0.8,
        trigger_signals=["fixture"],
        screening_model_id="fixture-screening",
        threshold_version="test-v1",
    )
    event = VerifiedEvent(
        event_id="event-feedback-api",
        analysis_id=analysis_id,
        video_id=video.video_id,
        candidate_id=candidate.candidate_id,
        status=EventStatus.HUMAN_REVIEW,
        event_type=VerifiedEventType.PHYSICAL_FIGHT,
        start_time=10,
        peak_time=12,
        end_time=15,
        confidence=0.8,
    )
    runtime.repository.create_video(video)
    runtime.repository.create_analysis(
        video.video_id,
        AnalysisProvenance(
            contract_version="1.0.0",
            config_version="test-v1",
            code_revision="test-revision",
        ),
        analysis_id=analysis_id,
    )
    runtime.repository.save_candidate(candidate)
    runtime.repository.save_event(event)

    with TestClient(app) as client:
        reviewed = client.post(
            "/api/events/event-feedback-api/review",
            json={
                "decision": "reject",
                "reviewer": "operator",
                "note": "Olağan hareket.",
                "false_alarm_reason": "normal_activity",
                "intervention_required": False,
            },
        )
        assert reviewed.status_code == 200
        review_id = reviewed.json()["review_id"]

        approved = client.post(
            "/api/events/event-feedback-api/development-approval",
            json={
                "review_id": review_id,
                "status": "approved",
                "approved_uses": ["threshold_calibration", "evaluation"],
                "reviewer": "operator",
                "note": "Kalibrasyon için kullanılabilir.",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        reviews = client.get("/api/events/event-feedback-api/reviews")
        approvals = client.get("/api/events/event-feedback-api/development-approvals")
        assert len(reviews.json()) == 1
        assert len(approvals.json()) == 1
