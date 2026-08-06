"""Görev 06 local REST sözleşme ve mock dikey akış testleri."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dortgoz.api import router as api_module
from dortgoz.api.router import ApiRuntime
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


def test_local_rest_vertical_and_error_envelope(monkeypatch, tmp_path: Path) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    video = metadata()
    runtime.repository.create_video(video)

    with TestClient(app) as client:
        accepted = client.post(
            f"/api/videos/{video.video_id}/analyze",
            json={"profile": "mock"},
        )
        assert accepted.status_code == 202
        analysis_id = accepted.json()["analysis_id"]

        for _ in range(30):
            status = client.get(f"/api/analyses/{analysis_id}/status")
            if status.json()["status"] in {"completed", "review_required", "failed"}:
                break
        assert status.status_code == 200
        assert status.json()["status"] == "review_required"

        events = client.get(f"/api/analyses/{analysis_id}/events")
        assert events.status_code == 200
        assert len(events.json()) == 3
        assert all(event["risk"]["reasons"] and event["risk"]["rule_ids"] for event in events.json())
        event_id = events.json()[0]["event_id"]

        event = client.get(f"/api/events/{event_id}")
        assert event.status_code == 200
        evidence = client.get(f"/api/events/{event_id}/evidence")
        assert evidence.status_code == 200
        assert evidence.json()

        query = client.post(
            f"/api/analyses/{analysis_id}/query",
            json={"question": "confirmed"},
        )
        assert query.status_code == 200
        assert query.json()["event_refs"]

        report = client.get(f"/api/reports/{analysis_id}")
        assert report.status_code == 200
        assert report.json()["candidate_count"] == 3

        missing = client.get("/api/events/does-not-exist")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "EVENT_NOT_FOUND"
        missing_video = client.get("/api/videos/does-not-exist")
        assert missing_video.status_code == 404
        assert missing_video.json()["error"]["code"] == "VIDEO_NOT_FOUND"

        invalid = client.post(
            f"/api/analyses/{analysis_id}/query",
            json={"question": ""},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_REQUEST"


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


def test_candidate_profile_uses_runtime_feature_cache(monkeypatch, tmp_path: Path) -> None:
    cache_root = tmp_path / "candidate-cache"
    monkeypatch.setattr(api_module.settings, "candidate_cache_dir", cache_root)
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    video = metadata("00000000-0000-0000-0000-000000000023")
    runtime.repository.create_video(video)
    captured: dict[str, object] = {}

    class FakeCandidateScreeningTool:
        def __init__(self, *, video_root: Path, model: object, cache: object) -> None:
            captured["video_root"] = video_root
            captured["model"] = model
            captured["cache"] = cache

        async def screen(self, metadata: VideoMetadata, analysis_id: str) -> list:
            return []

    monkeypatch.setattr(api_module, "LocalCandidateScreeningTool", FakeCandidateScreeningTool)
    with TestClient(app) as client:
        accepted = client.post(
            f"/api/videos/{video.video_id}/analyze",
            json={"profile": "candidate"},
        )
        assert accepted.status_code == 202
        analysis_id = accepted.json()["analysis_id"]
        for _ in range(30):
            status = client.get(f"/api/analyses/{analysis_id}/status")
            if status.json()["status"] in {"completed", "review_required", "failed"}:
                break

        assert status.json()["status"] == "completed"
        assert captured["video_root"] == api_module.settings.media_dir
        assert captured["model"] is runtime.candidate_scorer
        assert captured["cache"] is runtime.candidate_cache
        assert runtime.candidate_cache.root == cache_root.resolve()


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


def test_local_vlm_profile_requires_explicit_local_manifest(monkeypatch) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    monkeypatch.setattr(api_module.settings, "vlm_manifest_path", None)
    video = metadata("00000000-0000-0000-0000-000000000024")
    runtime.repository.create_video(video)

    with TestClient(app) as client:
        response = client.post(
            f"/api/videos/{video.video_id}/analyze",
            json={"profile": "local_vlm"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
