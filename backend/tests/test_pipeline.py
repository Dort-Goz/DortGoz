import json
import math
from pathlib import Path

import pytest

from dortgoz.config import settings
from dortgoz.events import Event, WindowReport
from dortgoz.pipeline import ingest, windowing
from dortgoz.pipeline.interpret import _yes_probability, report_schema
from dortgoz.pipeline.runner import resolve_media

MEDIA = settings.media_dir
CLIPS = sorted(MEDIA.glob("*.mp4")) if MEDIA.exists() else []
needs_clip = pytest.mark.skipif(not CLIPS, reason="media/ altında örnek klip yok")


def test_report_schema_has_no_refs():
    raw = json.dumps(report_schema())
    assert "$ref" not in raw and "$defs" not in raw


def test_report_schema_fields():
    schema = report_schema()
    assert set(schema["required"]) == {"anomaly_type", "summary", "events", "uncertainties"}
    assert "physical_fight" in schema["properties"]["anomaly_type"]["enum"]
    assert "kavga" not in schema["properties"]["anomaly_type"]["enum"]
    assert "window_start" not in schema["properties"]
    assert "type" not in schema["properties"]
    assert schema["additionalProperties"] is False
    event = schema["properties"]["events"]["items"]
    assert set(event["required"]) == {
        "t", "desc", "evidence", "severity_hint", "event_type",
    }


def test_schema_output_validates_as_window_report():
    payload = {"summary": "Sakin", "events": [
        {"t": 4.0, "desc": "Kapıdan bir kişi girdi", "severity_hint": "dusuk"}],
        "uncertainties": []}
    report = WindowReport(window_start=0.0, window_end=30.0, **payload)
    assert report.events[0].severity_hint == "dusuk"
    Event.wrap(report)


class _Alt:
    def __init__(self, token, logprob):
        self.token, self.logprob = token, logprob


class _Resp:
    def __init__(self, alts, content="NO"):
        top = type("T", (), {"top_logprobs": [_Alt(t, lp) for t, lp in alts]})()
        lg = type("L", (), {"content": [top]})() if alts else None
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"logprobs": lg, "message": msg})()]


def test_yes_probability_normalizes_over_yes_and_no():
    p = _yes_probability(_Resp([("YES", math.log(0.25)), ("NO", math.log(0.25)),
                                (".", math.log(0.5))]))
    assert p == pytest.approx(0.5)


def test_yes_probability_recovers_minority_mass():
    p = _yes_probability(_Resp([("NO", -0.14), ("YES", -2.08)]))
    assert 0.10 < p < 0.20

def test_yes_probability_matches_token_variants():
    p = _yes_probability(_Resp([(" YES", math.log(0.6)), ("no", math.log(0.4))]))
    assert p == pytest.approx(0.6)


def test_yes_probability_falls_back_without_logprobs():
    assert _yes_probability(_Resp([], content="YES")) == 1.0
    assert _yes_probability(_Resp([], content="NO")) == 0.0


def test_windows_cover_duration_without_gaps():
    wins = windowing.windows(70.0, 30.0)
    assert wins == [(0.0, 30.0), (30.0, 60.0), (60.0, 70.0)]
    assert windowing.windows(0.0) == []


def test_short_tail_merges_into_previous_window():
    assert windowing.windows(34.0, 30.0) == [(0.0, 34.0)]
    assert windowing.windows(64.0, 30.0) == [(0.0, 30.0), (30.0, 64.0)]
    assert windowing.windows(4.0, 30.0) == [(0.0, 4.0)]


def _sample(t: float, activity: float, fg: float = 0.0) -> ingest.MotionSample:
    return ingest.MotionSample(t=t, changed=activity, fg=fg, mad=activity)


def test_select_keyframes_prefers_motion_and_spreads():
    profile = [_sample(float(t), 0.9 if t == 10 else 0.0) for t in range(30)]
    picked = windowing.select_keyframes(profile, 0.0, 30.0, k=4)
    assert 10.0 in picked
    assert len(picked) == 4
    assert picked == sorted(picked)
    assert all(b - a > 0 for a, b in zip(picked, picked[1:]))


def test_select_keyframes_falls_back_when_calm():
    profile = [_sample(float(t), 0.0) for t in range(30)]
    picked = windowing.select_keyframes(profile, 0.0, 30.0, k=3)
    assert len(picked) == 3


def test_window_motion_peak():
    profile = [_sample(0.0, 0.1), _sample(1.0, 0.5), _sample(40.0, 0.9)]
    assert windowing.window_motion(profile, 0.0, 30.0) == 0.5
    assert windowing.window_motion(profile, 100.0, 130.0) == 0.0


def test_activity_uses_presence_when_nothing_moves():
    still_but_present = _sample(5.0, activity=0.0, fg=0.30)
    assert still_but_present.activity == 0.30
    assert windowing.window_motion([still_but_present], 0.0, 30.0) == 0.30


def test_noise_floor_and_adaptive_gate_scale_with_camera():
    quiet = [_sample(float(t), 0.001) for t in range(40)]
    noisy = [_sample(float(t), 0.020) for t in range(40)]
    assert ingest.noise_floor(quiet) < ingest.noise_floor(noisy)
    assert ingest.adaptive_gate(noisy) > ingest.adaptive_gate(quiet)


def test_adaptive_gate_never_collapses_to_zero():
    dead = [_sample(float(t), 0.0) for t in range(40)]
    assert ingest.adaptive_gate(dead) > 0.0


def test_resolve_media_rejects_traversal():
    with pytest.raises(ValueError):
        resolve_media("../../etc/passwd")


def test_resolve_media_rejects_missing():
    with pytest.raises(FileNotFoundError):
        resolve_media("yok-boyle-bir-video.mp4")


def test_dataset_video_is_listed_and_resolved_by_name(monkeypatch, tmp_path):
    from dortgoz.config import settings
    from dortgoz.services import video_library

    media = tmp_path / "media"
    media.mkdir()
    (media / "yerel.mp4").write_bytes(b"0")
    dataset = tmp_path / "UCF_Crimes" / "Videos" / "Burglary"
    dataset.mkdir(parents=True)
    clip = dataset / "Burglary054_x264.mp4"
    clip.write_bytes(b"0")
    monkeypatch.setattr(settings, "media_dir", media)
    monkeypatch.setattr(settings, "ucf_dir", tmp_path / "UCF_Crimes")
    video_library.reset_cache()

    try:
        assert video_library.catalog() == ["Burglary054_x264.mp4", "yerel.mp4"]
        assert resolve_media("Burglary054_x264.mp4") == clip.resolve()
        with pytest.raises(ValueError):
            resolve_media("../Burglary054_x264.mp4")
    finally:
        video_library.reset_cache()


@needs_clip
@pytest.mark.asyncio
async def test_probe_and_motion_profile_on_real_clip():
    clip = CLIPS[0]
    duration = await ingest.probe_duration(clip)
    assert duration > 0
    profile = await ingest.motion_profile(clip, base_fps=1.0)
    assert len(profile) >= 1
    assert profile[0].changed == 0.0
    assert all(0.0 <= s.activity <= 1.0 for s in profile)
    assert max(s.activity for s in profile) > 0


@needs_clip
@pytest.mark.asyncio
async def test_grab_frame_returns_jpeg():
    jpeg = await ingest.grab_frame(CLIPS[0], 1.0)
    assert jpeg[:2] == b"\xff\xd8"
    assert len(jpeg) > 1000


@needs_clip
@pytest.mark.asyncio
async def test_gate_kills_dead_footage_but_not_real_footage(tmp_path):
    still = tmp_path / "still.png"
    dead = tmp_path / "dead_noisy.mp4"
    await ingest._run("ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(CLIPS[0]),
                      "-frames:v", "1", str(still))
    await ingest._run("ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(still),
                      "-t", "20", "-r", "5", "-vf", "noise=alls=10:allf=t",
                      "-pix_fmt", "yuv420p", str(dead))

    dead_profile = await ingest.motion_profile(dead, base_fps=1.0)
    dead_peak = max(s.activity for s in dead_profile)
    assert dead_peak < ingest.adaptive_gate(dead_profile), "ölü görüntü elenmedi"

    real_profile = await ingest.motion_profile(CLIPS[0], base_fps=1.0)
    real_peak = max(s.activity for s in real_profile)
    assert real_peak > ingest.adaptive_gate(real_profile), "gerçek görüntü elendi"
    assert real_peak > dead_peak * 2, "ölü/gerçek payı çok dar — kapı ayarlanamaz"


@needs_clip
@pytest.mark.asyncio
async def test_ffmpeg_error_is_typed():
    with pytest.raises(ingest.FFmpegError):
        await ingest.probe_duration(Path("/nonexistent/clip.mp4"))


def _grid_sample(t, grid, activity=0.0):
    from dortgoz.pipeline.ingest import MotionSample
    return MotionSample(t=t, changed=activity, fg=0.0, mad=0.0, grid=grid)


def test_dedup_collapses_static_window():
    from dortgoz.pipeline.windowing import _dedup
    flat = bytes([100]) * (64 * 48)
    samples = [_grid_sample(float(t), flat) for t in range(30)]
    times = [2.0, 7.0, 12.0, 17.0, 22.0, 27.0]
    out = _dedup(times, samples, threshold=0.006)
    assert out == [2.0, 27.0]


def test_dedup_keeps_distinct_frames():
    from dortgoz.pipeline.windowing import _dedup
    samples = [_grid_sample(float(t), bytes([(t * 40) % 256]) * (64 * 48))
               for t in range(30)]
    times = [2.0, 7.0, 12.0, 17.0]
    assert _dedup(times, samples, threshold=0.006) == times


def test_dedup_survives_missing_grids():
    from dortgoz.pipeline.windowing import _dedup
    samples = [_grid_sample(float(t), b"") for t in range(30)]
    times = [2.0, 7.0, 12.0]
    assert _dedup(times, samples, threshold=0.006) == times


def _screen(ts, anomaly, model="siglip2-semantic-v1"):
    from dortgoz.domain.candidate import ScreeningSample
    return ScreeningSample(
        timestamp=ts, anomaly_score=anomaly, interaction_score=0.2,
        fall_score=0.1, fire_smoke_score=0.0, vehicle_conflict_score=0.0,
        tampering_score=0.0, image_quality=0.8, source_model=model,
    )


def _motion(t, changed, fg, mad):
    return ingest.MotionSample(t=t, changed=changed, fg=fg, mad=mad)


def test_window_signals_picks_the_peak_sample_inside_the_window():
    from dortgoz.pipeline.runner import window_signals
    samples = [_screen(1.0, 0.2), _screen(5.0, 0.9), _screen(9.0, 0.4),
               _screen(40.0, 0.99)]
    motion = [_motion(1.0, 0.1, 0.2, 0.01), _motion(5.0, 0.5, 0.3, 0.09),
              _motion(40.0, 0.9, 0.9, 0.9)]
    sig = window_signals(0.0, 30.0, samples, motion, {"durum_p": 0.61})

    assert sig.anomaly_score == pytest.approx(0.9)
    assert sig.image_quality == pytest.approx(0.8)
    assert sig.durum_p == pytest.approx(0.61)
    assert sig.changed == pytest.approx(0.5)
    assert sig.mad == pytest.approx(0.09)
    assert sig.screening_model == "siglip2-semantic-v1"


def test_window_signals_is_empty_when_nothing_falls_in_the_window():
    from dortgoz.pipeline.runner import window_signals
    sig = window_signals(0.0, 30.0, [_screen(90.0, 0.9)], [_motion(90.0, 1.0, 1.0, 1.0)], {})
    assert sig.anomaly_score is None
    assert sig.changed is None
    assert sig.durum_p is None
    assert sig.screening_model == ""


def test_window_signals_survives_screening_being_disabled():
    from dortgoz.pipeline.runner import window_signals
    sig = window_signals(0.0, 30.0, [], [_motion(2.0, 0.4, 0.1, 0.02)], {"durum_p": 0.05})
    assert sig.anomaly_score is None
    assert sig.changed == pytest.approx(0.4)
    assert sig.durum_p == pytest.approx(0.05)
