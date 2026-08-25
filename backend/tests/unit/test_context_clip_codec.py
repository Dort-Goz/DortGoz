import pytest

from dortgoz.tools import context_clip

ENCODER_LIST = b"""
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D mpeg4                MPEG-4 part 2
"""


class _Process:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._payload, b""


@pytest.fixture(autouse=True)
def _reset_cache():
    context_clip._video_encoder = None
    yield
    context_clip._video_encoder = None


@pytest.mark.asyncio
async def test_browser_encoder_prefers_h264(monkeypatch) -> None:
    async def fake_exec(*_args, **_kwargs):
        return _Process(ENCODER_LIST)

    monkeypatch.setattr(context_clip.asyncio, "create_subprocess_exec", fake_exec)
    assert await context_clip.browser_video_encoder() == "libx264"


@pytest.mark.asyncio
async def test_browser_encoder_falls_back_when_h264_missing(monkeypatch) -> None:
    async def fake_exec(*_args, **_kwargs):
        return _Process(b" V....D mpeg4                MPEG-4 part 2\n")

    monkeypatch.setattr(context_clip.asyncio, "create_subprocess_exec", fake_exec)
    assert await context_clip.browser_video_encoder() == "mpeg4"


@pytest.mark.asyncio
async def test_browser_encoder_falls_back_without_ffmpeg(monkeypatch) -> None:
    async def fake_exec(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(context_clip.asyncio, "create_subprocess_exec", fake_exec)
    assert await context_clip.browser_video_encoder() == "mpeg4"
