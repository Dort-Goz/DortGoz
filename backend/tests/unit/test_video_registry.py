from __future__ import annotations

import json
from pathlib import Path

import pytest

from dortgoz.domain.video import VideoMetadata
from dortgoz.errors import RepositoryDuplicateError, RepositoryError
from dortgoz.services.video_registry import VideoRegistry


def meta(video_id: str = "00000000-0000-0000-0000-000000000001",
         digest: str = "a" * 64) -> VideoMetadata:
    return VideoMetadata(
        video_id=video_id,
        original_filename="kayit.mp4",
        stored_filename=f"{video_id}.mp4",
        media_path=f"media/{video_id}.mp4",
        file_size_bytes=1024,
        file_hash_sha256=digest,
        container="mp4",
        codec="h264",
        width=1280,
        height=720,
        fps=25.0,
        duration_seconds=12.5,
        has_audio=False,
        time_base="1/25",
    )


def test_memory_mode_needs_no_file(tmp_path):
    reg = VideoRegistry(None)
    assert reg.persistence_mode == "memory"
    reg.create_video(meta())
    assert reg.get_video(meta().video_id) is not None
    assert list(tmp_path.iterdir()) == []


def test_videos_survive_a_restart(tmp_path):
    store = tmp_path / "video_registry.json"
    VideoRegistry(store).create_video(meta())

    reborn = VideoRegistry(store)
    assert reborn.persistence_mode == "json"
    got = reborn.get_video(meta().video_id)
    assert got is not None and got.original_filename == "kayit.mp4"


def test_stored_file_is_readable_json(tmp_path):
    store = tmp_path / "video_registry.json"
    VideoRegistry(store).create_video(meta())
    payload = json.loads(store.read_text(encoding="utf-8"))
    assert [v["video_id"] for v in payload["videos"]] == [meta().video_id]


def test_same_video_twice_is_idempotent(tmp_path):
    reg = VideoRegistry(tmp_path / "s.json")
    first = reg.create_video(meta())
    again = reg.create_video(meta())
    assert first.video_id == again.video_id
    assert len(json.loads((tmp_path / "s.json").read_text())["videos"]) == 1


def test_same_id_different_hash_is_rejected(tmp_path):
    reg = VideoRegistry(tmp_path / "s.json")
    reg.create_video(meta())
    with pytest.raises(RepositoryDuplicateError):
        reg.create_video(meta(digest="b" * 64))


def test_lookup_by_hash_finds_the_video(tmp_path):
    reg = VideoRegistry(tmp_path / "s.json")
    reg.create_video(meta())
    assert reg.find_video_by_hash("a" * 64) is not None
    assert reg.find_video_by_hash("c" * 64) is None


def test_returned_records_are_copies(tmp_path):
    reg = VideoRegistry(None)
    reg.create_video(meta())
    got = reg.get_video(meta().video_id)
    assert got is not None
    got.original_filename = "degistirildi.mp4"
    assert reg.get_video(meta().video_id).original_filename == "kayit.mp4"


def test_corrupt_store_fails_loudly(tmp_path):
    store = tmp_path / "video_registry.json"
    store.write_bytes(b"SQLite format 3\x00 not json at all")
    with pytest.raises(RepositoryError):
        VideoRegistry(store)


def test_write_leaves_no_temp_file_behind(tmp_path):
    store = tmp_path / "video_registry.json"
    reg = VideoRegistry(store)
    reg.create_video(meta())
    reg.create_video(meta("00000000-0000-0000-0000-000000000002", "b" * 64))
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert len(json.loads(store.read_text())["videos"]) == 2


def test_registry_exposes_no_event_store_surface():
    reg = VideoRegistry(None)
    for gone in ("save_event", "get_event", "list_events", "save_review",
                 "get_analysis", "snapshot_metrics", "save_agent_bundle"):
        assert not hasattr(reg, gone)


def test_store_directory_is_created_on_demand(tmp_path):
    nested = tmp_path / "yeni" / "klasor" / "video_registry.json"
    VideoRegistry(nested).create_video(meta())
    assert nested.is_file()
    assert isinstance(Path(nested).parent, Path)
