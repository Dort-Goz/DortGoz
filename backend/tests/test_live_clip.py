from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from dortgoz.services.live_clip import (
    LiveClipError,
    concat_segments,
    is_live_segment,
    segment_start_epoch,
    segments_covering,
)

SEGMENT_SECONDS = 30.0


def _touch(directory: Path, epoch: int, size: int = 16) -> Path:
    path = directory / f"seg_{epoch}.mp4"
    path.write_bytes(b"x" * size)
    return path


def test_live_segment_paths_are_recognised() -> None:
    assert is_live_segment("canli/kamera1/seg_1787822000.mp4")
    assert not is_live_segment("kamera1.mp4")
    assert not is_live_segment("canli/kamera1/latest.jpg")
    assert not is_live_segment("_incident_media/abc/incident.mp4")


def test_segment_epoch_is_read_from_the_name(tmp_path: Path) -> None:
    assert segment_start_epoch(_touch(tmp_path, 1787822000)) == 1787822000.0
    assert segment_start_epoch(tmp_path / "latest.jpg") is None


def test_only_segments_overlapping_the_window_are_taken(tmp_path: Path) -> None:
    for epoch in (1000, 1030, 1060, 1090):
        _touch(tmp_path, epoch)

    covering = segments_covering(tmp_path, 1055.0, 1065.0, SEGMENT_SECONDS)

    assert [p.name for p in covering] == ["seg_1030.mp4", "seg_1060.mp4"]


def test_an_event_crossing_a_boundary_pulls_both_segments(tmp_path: Path) -> None:
    _touch(tmp_path, 1000)
    _touch(tmp_path, 1030)

    covering = segments_covering(tmp_path, 1022.0, 1038.0, SEGMENT_SECONDS)

    assert [p.name for p in covering] == ["seg_1000.mp4", "seg_1030.mp4"]


def test_a_dropped_segment_stops_the_run_so_time_stays_honest(tmp_path: Path) -> None:
    _touch(tmp_path, 1000)
    _touch(tmp_path, 1060)

    covering = segments_covering(tmp_path, 1020.0, 1080.0, SEGMENT_SECONDS)

    assert [p.name for p in covering] == ["seg_1000.mp4"]


def test_empty_segments_are_ignored(tmp_path: Path) -> None:
    _touch(tmp_path, 1000)
    (tmp_path / "seg_1030.mp4").write_bytes(b"")

    covering = segments_covering(tmp_path, 1020.0, 1040.0, SEGMENT_SECONDS)

    assert [p.name for p in covering] == ["seg_1000.mp4"]


@pytest.mark.asyncio
async def test_concat_rejects_a_single_segment(tmp_path: Path) -> None:
    with pytest.raises(LiveClipError) as failure:
        await concat_segments([_touch(tmp_path, 1000)], tmp_path / "out.mp4", 10.0)

    assert failure.value.code == "LIVE_CONCAT_INPUT"


def _make_segment(path: Path, seconds: int, colour: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={colour}:s=160x120:d={seconds}:r=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)],
        check=True,
    )


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe yok",
)
def test_two_segments_become_one_continuous_clip(tmp_path: Path) -> None:
    first, second = tmp_path / "seg_1000.mp4", tmp_path / "seg_1004.mp4"
    _make_segment(first, 4, "red")
    _make_segment(second, 4, "blue")
    target = tmp_path / "birlesik.mp4"

    asyncio.run(concat_segments([first, second], target, 60.0))

    assert target.is_file()
    assert _duration(target) == pytest.approx(8.0, abs=0.5)
    assert not list(tmp_path.glob("*.concat.txt"))


def test_activity_levels_mark_only_frames_over_the_motion_gate() -> None:
    from dortgoz.pipeline.ingest import MotionSample
    from dortgoz.pipeline.windowing import activity_levels

    gate = 0.010
    profile = [
        MotionSample(t=0.0, changed=0.002, fg=0.001, mad=0.0),
        MotionSample(t=1.0, changed=0.011, fg=0.0, mad=0.0),
        MotionSample(t=2.0, changed=0.025, fg=0.0, mad=0.0),
        MotionSample(t=3.0, changed=0.090, fg=0.0, mad=0.0),
        MotionSample(t=40.0, changed=0.500, fg=0.0, mad=0.0),
    ]

    levels = activity_levels(profile, 0.0, 30.0, gate)

    assert levels == [0, 1, 2, 3]


def test_activity_levels_are_empty_without_samples_in_the_window() -> None:
    from dortgoz.pipeline.windowing import activity_levels

    assert activity_levels([], 0.0, 30.0, 0.01) == []


def test_a_zero_gate_never_reports_a_passing_frame() -> None:
    from dortgoz.pipeline.ingest import MotionSample
    from dortgoz.pipeline.windowing import activity_levels

    profile = [MotionSample(t=0.0, changed=0.5, fg=0.5, mad=0.0)]

    assert activity_levels(profile, 0.0, 30.0, 0.0) == [0]
