from __future__ import annotations

from pathlib import Path

from dortgoz.domain.candidate import ScreeningSample
from dortgoz.domain.video import VideoMetadata
from dortgoz.pipeline.feature_cache import JsonFeatureCache
from dortgoz.pipeline.ingest import MotionSample
from dortgoz.tools import screening as screening_module
from dortgoz.tools.screening import LocalCandidateScreeningTool


def metadata() -> VideoMetadata:
    return VideoMetadata(
        video_id="00000000-0000-0000-0000-000000000031",
        original_filename="fixture.mp4",
        stored_filename="00000000-0000-0000-0000-000000000031.mp4",
        media_path="00000000-0000-0000-0000-000000000031.mp4",
        file_size_bytes=1024,
        file_hash_sha256="c" * 64,
        container="mov",
        codec="h264",
        width=640,
        height=480,
        fps=25,
        duration_seconds=10,
        has_audio=False,
        time_base="1/12800",
    )


async def test_local_screening_uses_cache_and_contract(monkeypatch, tmp_path: Path) -> None:
    video = metadata()
    path = tmp_path / video.media_path
    path.write_bytes(b"fixture")
    calls = 0

    async def fake_profile(_: Path, *, base_fps: float):
        nonlocal calls
        calls += 1
        assert base_fps == 1.0
        return [
            MotionSample(t=0, changed=0, fg=0, mad=0),
            MotionSample(t=1, changed=0.2, fg=0.3, mad=0.1),
            MotionSample(t=2, changed=0.2, fg=0.3, mad=0.1),
            MotionSample(t=3, changed=0, fg=0, mad=0),
            MotionSample(t=4, changed=0, fg=0, mad=0),
        ]

    monkeypatch.setattr(screening_module, "motion_profile", fake_profile)
    tool = LocalCandidateScreeningTool(
        video_root=tmp_path,
        cache=JsonFeatureCache(tmp_path / "cache"),
    )
    first = await tool.screen(video, "analysis-07")
    second = await tool.screen(video, "analysis-07")

    assert calls == 1
    assert len(first) == len(second) == 1
    assert first[0].candidate_id == second[0].candidate_id
    assert first[0].screening_model_id == "motion-baseline-v1"


async def test_semantic_video_scorer_uses_score_video_off_event_loop(
    monkeypatch, tmp_path: Path
) -> None:
    video = metadata()
    path = tmp_path / video.media_path
    path.write_bytes(b"fixture")

    async def fake_profile(_: Path, *, base_fps: float):
        assert base_fps == 1.0
        return [MotionSample(t=0, changed=0.2, fg=0.3, mad=0.1)]

    class VideoScorer:
        model_id = "semantic-fixture-v1"

        def __init__(self) -> None:
            self.calls: list[Path] = []

        def score(self, _profile):
            raise AssertionError("semantic scorer score() yoluna düşmemeli")

        def score_video(self, _profile, video_path: Path):
            self.calls.append(video_path)
            return [
                ScreeningSample(
                    timestamp=0,
                    anomaly_score=0.95,
                    source_model=self.model_id,
                ),
                ScreeningSample(
                    timestamp=1,
                    anomaly_score=0.90,
                    source_model=self.model_id,
                ),
            ]

    scorer = VideoScorer()
    monkeypatch.setattr(screening_module, "motion_profile", fake_profile)
    tool = LocalCandidateScreeningTool(video_root=tmp_path, model=scorer)

    candidates = await tool.screen(video, "analysis-semantic")

    assert scorer.calls == [path]
    assert len(candidates) == 1
    assert candidates[0].screening_model_id == scorer.model_id
