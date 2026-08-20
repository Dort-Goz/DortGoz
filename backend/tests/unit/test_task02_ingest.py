from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from dortgoz.domain.video import (
    VideoErrorCode,
    VideoIngestError,
    VideoMetadata,
    VideoProbe,
)
from dortgoz.infrastructure.ffmpeg import check_decode, parse_probe_json, probe_video
from dortgoz.infrastructure.storage import LocalVideoStorage
from dortgoz.services.ingest_service import VideoIngestService


def valid_probe_json(**video_updates: object) -> dict[str, object]:
    video: dict[str, object] = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "24000/1001",
        "r_frame_rate": "24/1",
        "duration": "12.5",
        "time_base": "1/24000",
    }
    video.update(video_updates)
    return {
        "streams": [video, {"codec_type": "audio", "codec_name": "aac"}],
        "format": {"format_name": "mov,mp4,m4a", "duration": "12.5"},
    }


def test_probe_parser_returns_full_normalized_metadata() -> None:
    probe = parse_probe_json(valid_probe_json())
    assert probe.codec == "h264"
    assert probe.container in {"mov", "mp4"}
    assert probe.width == 1920 and probe.height == 1080
    assert probe.has_audio is True
    assert probe.variable_fps is True
    assert probe.time_base == "1/24000"


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"codec_name": "prores"}, VideoErrorCode.UNSUPPORTED_CODEC),
        ({"duration": "0"}, VideoErrorCode.INVALID_DURATION),
        ({"avg_frame_rate": "0/0", "r_frame_rate": "0/0"}, VideoErrorCode.INVALID_FPS),
        ({"width": 0}, VideoErrorCode.DECODE_FAILED),
    ],
)
def test_probe_parser_uses_typed_errors(
    updates: dict[str, object], expected: VideoErrorCode
) -> None:
    with pytest.raises(VideoIngestError) as captured:
        parse_probe_json(valid_probe_json(**updates))
    assert captured.value.code == expected


def test_probe_parser_rejects_disguised_container() -> None:
    payload = valid_probe_json()
    payload["format"] = {"format_name": "image2", "duration": "12.5"}
    with pytest.raises(VideoIngestError) as captured:
        parse_probe_json(payload)
    assert captured.value.code == VideoErrorCode.UNSUPPORTED_CONTAINER


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int,
        stdout: bytes = b"",
        stderr: bytes = b"",
        stall_once: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stall_once = stall_once
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.stall_once and not self.killed:
            await asyncio.sleep(60)
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True


async def test_ffprobe_nonzero_exit_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(returncode=1, stderr=b"invalid data")

    async def create_process(*_: object, **__: object) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    with pytest.raises(VideoIngestError) as captured:
        await probe_video(Path("fixture.mp4"), timeout_seconds=0.01)
    assert captured.value.code == VideoErrorCode.DECODE_FAILED
    assert "invalid data" in str(captured.value)


async def test_ffprobe_timeout_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(returncode=0, stall_once=True)

    async def create_process(*_: object, **__: object) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    with pytest.raises(VideoIngestError) as captured:
        await probe_video(Path("fixture.mp4"), timeout_seconds=0.001)
    assert captured.value.code == VideoErrorCode.DECODE_FAILED
    assert process.killed is True


async def test_decode_nonzero_exit_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(returncode=1, stderr=b"corrupt frame")

    async def create_process(*_: object, **__: object) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    with pytest.raises(VideoIngestError) as captured:
        await check_decode(Path("fixture.mp4"), timeout_seconds=0.01)
    assert captured.value.code == VideoErrorCode.DECODE_FAILED
    assert "corrupt frame" in str(captured.value)


async def test_storage_uses_uuid_hash_and_marks_duplicate(tmp_path: Path) -> None:
    source = tmp_path / "görüntü.mp4"
    payload = b"not-a-real-video-but-storage-is-byte-preserving"
    source.write_bytes(payload)
    storage = LocalVideoStorage(tmp_path / "media", max_bytes=1024)

    first = await storage.store(source)
    second = await storage.store(source)

    assert first.absolute_path.read_bytes() == payload
    assert first.absolute_path.parent == (tmp_path / "media").resolve()
    assert first.stored_filename != source.name
    assert first.file_hash_sha256 == hashlib.sha256(payload).hexdigest()
    assert first.duplicate_of_video_id is None
    assert second.duplicate_of_video_id == first.video_id


@pytest.mark.parametrize(
    ("filename", "max_bytes", "expected"),
    [
        ("../kaçış.mp4", 100, VideoErrorCode.PATH_REJECTED),
        ("clip.exe", 100, VideoErrorCode.UNSUPPORTED_CONTAINER),
        ("clip.mp4", 2, VideoErrorCode.FILE_TOO_LARGE),
    ],
)
async def test_storage_rejects_unsafe_inputs(
    tmp_path: Path,
    filename: str,
    max_bytes: int,
    expected: VideoErrorCode,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"1234")
    storage = LocalVideoStorage(tmp_path / "media", max_bytes=max_bytes)
    with pytest.raises(VideoIngestError) as captured:
        await storage.store(source, filename)
    assert captured.value.code == expected


async def test_ingest_removes_copy_when_probe_fails(tmp_path: Path) -> None:
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"broken")
    storage = LocalVideoStorage(tmp_path / "media", max_bytes=100)

    async def broken_probe(_: Path) -> VideoProbe:
        raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "fixture")

    service = VideoIngestService(storage, broken_probe)
    with pytest.raises(VideoIngestError):
        await service.ingest_file(source)
    assert list((tmp_path / "media").iterdir()) == []
    retry = await storage.store(source)
    assert retry.duplicate_of_video_id is None


def test_video_metadata_rejects_absolute_media_path() -> None:
    with pytest.raises(ValidationError):
        VideoMetadata(
            video_id="00000000-0000-0000-0000-000000000001",
            original_filename="clip.mp4",
            stored_filename="00000000-0000-0000-0000-000000000001.mp4",
            media_path="C:/outside/clip.mp4",
            file_size_bytes=10,
            file_hash_sha256="a" * 64,
            container="mov",
            codec="h264",
            width=64,
            height=48,
            fps=10,
            duration_seconds=1,
            has_audio=False,
            time_base="1/10240",
        )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="yerel ffmpeg/ffprobe bulunamadı",
)
async def test_real_local_ffmpeg_ingest_smoke(tmp_path: Path) -> None:
    source = tmp_path / "sentetik.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:d=0.6",
            "-c:v",
            "mpeg4",
            "-r",
            "10",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=20,
    )
    service = VideoIngestService(
        LocalVideoStorage(tmp_path / "media", max_bytes=10 * 1024 * 1024)
    )
    metadata = await service.ingest_file(source)

    assert metadata.processable is True
    assert metadata.codec == "mpeg4"
    assert metadata.width == 64 and metadata.height == 48
    assert metadata.duration_seconds > 0
    assert metadata.file_size_bytes == source.stat().st_size
    assert not Path(metadata.media_path).is_absolute()
