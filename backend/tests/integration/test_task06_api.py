"""Görev 06 local REST upload ve ortak hata sözleşmesi testleri."""

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
