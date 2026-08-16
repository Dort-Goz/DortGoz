"""Görev 06 local REST upload ve ortak hata sözleşmesi testleri."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from dortgoz.api import router as api_module
from dortgoz.api.router import ApiRuntime
from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.dataset import (
    DatasetLicenseStatus,
    DatasetSplit,
    DatasetUse,
    DatasetVideoRecord,
    OfflineDatasetManifest,
    calculate_dataset_fingerprint,
)
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.media import IncidentMedia
from dortgoz.domain.provenance import AnalysisProvenance
from dortgoz.domain.video import VideoMetadata
from dortgoz.main import app
from dortgoz.services.dataset_manifest import write_dataset_manifest
from dortgoz.services.intervention_priority import InterventionPriorityService
from dortgoz.services.training_sample import TrainingSampleService


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

        revoked = client.post(
            "/api/events/event-feedback-api/development-approval",
            json={
                "review_id": review_id,
                "status": "revoked",
                "approved_uses": [],
                "reviewer": "operator",
                "note": "Geliştirme izni geri alındı.",
                "supersedes_approval_id": approved.json()["approval_id"],
            },
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"

        reviews = client.get("/api/events/event-feedback-api/reviews")
        approvals = client.get("/api/events/event-feedback-api/development-approvals")
        assert len(reviews.json()) == 1
        assert [item["status"] for item in approvals.json()] == ["approved", "revoked"]


def test_incident_media_route_returns_playable_local_urls(monkeypatch) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    video = metadata("00000000-0000-0000-0000-000000000025")
    analysis_id = "analysis-incident-media-api"
    candidate = CandidateEvent(
        candidate_id="candidate-incident-media-api",
        analysis_id=analysis_id,
        video_id=video.video_id,
        start_time=10,
        peak_time=12,
        end_time=15,
        candidate_type=CandidateType.UNKNOWN_ANOMALY,
        peak_score=0.8,
        anomaly_score=0.8,
        trigger_signals=["fixture"],
        screening_model_id="fixture-screening",
        threshold_version="test-v1",
    )
    event = VerifiedEvent(
        event_id="event-incident-media-api",
        analysis_id=analysis_id,
        video_id=video.video_id,
        candidate_id=candidate.candidate_id,
        status=EventStatus.HUMAN_REVIEW,
        event_type=VerifiedEventType.UNKNOWN_ANOMALY,
        start_time=10,
        peak_time=12,
        end_time=15,
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
    runtime.repository.save_incident_media(
        IncidentMedia(
            media_id="incident-media-api",
            event_id=event.event_id,
            analysis_id=analysis_id,
            video_id=video.video_id,
            event_revision=event.revision,
            source_refs=[video.media_path],
            source_file_sha256=video.file_hash_sha256,
            clip_ref="_incident_media/incident-media-api/incident.mp4",
            thumbnail_ref="_incident_media/incident-media-api/thumbnail.jpg",
            clip_start=2,
            clip_end=23,
            peak_time=12,
            pre_capture_seconds=8,
            post_capture_seconds=8,
            clip_sha256="c" * 64,
            thumbnail_sha256="d" * 64,
            clip_size_bytes=200,
            thumbnail_size_bytes=50,
        )
    )
    InterventionPriorityService(runtime.repository).assess_and_save(
        event.event_id,
        risk="kritik",
        event_type="bilinmeyen",
        phase="basladi",
        needs_review=True,
    )

    with TestClient(app) as client:
        response = client.get("/api/events/event-incident-media-api/media")
        priority_response = client.get("/api/events/event-incident-media-api/priority")

    assert response.status_code == 200
    assert response.json()["clip_url"] == (
        "/media/_incident_media/incident-media-api/incident.mp4"
    )
    assert response.json()["thumbnail_url"].endswith("/thumbnail.jpg")
    assert priority_response.status_code == 200
    assert priority_response.json()["score"] == 95
    assert priority_response.json()["band"] == "urgent"
    assert priority_response.json()["ruleset_version"] == "intervention-priority-v1"


def test_training_sample_api_prepares_and_verifies_approved_event(
    monkeypatch, tmp_path: Path
) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    video_payload = b"team-owned-api-video"
    video = metadata("00000000-0000-0000-0000-000000000024").model_copy(
        update={
            "file_size_bytes": len(video_payload),
            "file_hash_sha256": hashlib.sha256(video_payload).hexdigest(),
        }
    )
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / video.media_path).write_bytes(video_payload)
    entry = DatasetVideoRecord(
        dataset_video_id="owned/api-video",
        source_ref="videos/api-video.mp4",
        source_label="owned",
        split=DatasetSplit.TRAIN,
        file_size_bytes=len(video_payload),
        file_sha256=video.file_hash_sha256,
        allowed_uses=[DatasetUse.TRAINING],
    )
    manifest = OfflineDatasetManifest(
        dataset_id="owned-api",
        source_name="Team-owned API fixture",
        source_url="https://example.invalid/owned-api",
        citation="Team-owned API fixture.",
        license_status=DatasetLicenseStatus.VERIFIED,
        license_id="MIT",
        redistribution_allowed=True,
        training_allowed=True,
        allowed_uses=[DatasetUse.TRAINING],
        entries=[entry],
        dataset_fingerprint=calculate_dataset_fingerprint([entry]),
    )
    manifest_root = tmp_path / "manifests"
    write_dataset_manifest(manifest_root / "owned.json", manifest)

    async def fetcher(_video: Path, timestamp: float, _width: int) -> bytes:
        components = b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        sof = b"\x08\x01\x68\x02\x80" + components
        marker = f"frame-{timestamp}".encode()
        comment = b"\xff\xfe" + (len(marker) + 2).to_bytes(2, "big") + marker
        return (
            b"\xff\xd8\xff\xc0"
            + (len(sof) + 2).to_bytes(2, "big")
            + sof
            + comment
            + b"\xff\xd9"
        )

    runtime.training_samples = TrainingSampleService(
        runtime.repository,
        media_root=media_root,
        dataset_manifest_root=manifest_root,
        frame_root=media_root / "_training_samples",
        frame_fetcher=fetcher,
    )
    analysis_id = "analysis-training-api"
    candidate = CandidateEvent(
        candidate_id="candidate-training-api",
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
        event_id="event-training-api",
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
            "/api/events/event-training-api/review",
            json={
                "decision": "edit",
                "reviewer": "operator",
                "note": "Olay zamanları doğrulandı.",
                "start_time": 10,
                "peak_time": 12,
                "end_time": 15,
            },
        )
        approved = client.post(
            "/api/events/event-training-api/development-approval",
            json={
                "review_id": reviewed.json()["review_id"],
                "status": "approved",
                "approved_uses": ["d_fine_training"],
                "reviewer": "operator",
                "note": "D-FINE kutu incelemesi için onaylandı.",
            },
        )
        prepared = client.post(
            "/api/events/event-training-api/training-samples",
            json={
                "approval_id": approved.json()["approval_id"],
                "dataset_manifest_name": "owned.json",
                "prepared_by": "operator",
            },
        )
        assert prepared.status_code == 200
        assert len(prepared.json()) == 3
        assert all(item["status"] == "pending_review" for item in prepared.json())
        assert all(item["frame_url"].startswith("/media/_training_samples/") for item in prepared.json())

        sample_id = prepared.json()[1]["sample_id"]
        verified = client.post(
            f"/api/training-samples/{sample_id}/review",
            json={
                "review_result": "verified_boxes",
                "boxes": [
                    {
                        "category_name": "person",
                        "x": 10,
                        "y": 20,
                        "width": 30,
                        "height": 40,
                    }
                ],
                "reviewer": "operator",
                "annotation_tool": "Dortgoz UI",
            },
        )
        assert verified.status_code == 200
        assert verified.json()["status"] == "verified"
        assert verified.json()["frame_review"]["human_verified"] is True

        listed = client.get("/api/events/event-training-api/training-samples")
        assert len(listed.json()) == 3
