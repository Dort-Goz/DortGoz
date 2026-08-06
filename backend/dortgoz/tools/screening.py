"""Yerel candidate screening adapter'ı."""

from __future__ import annotations

from pathlib import Path

from ..config import settings
from ..domain.candidate import CandidateEvent
from ..domain.video import VideoMetadata
from ..pipeline.candidate_intervals import IntervalConfig, build_candidate_intervals
from ..pipeline.candidate_model import CandidateScorer, load_candidate_scorer
from ..pipeline.feature_cache import FeatureCacheKey, JsonFeatureCache
from ..pipeline.ingest import motion_profile
from .protocols import ScreeningTool


class ScreeningError(RuntimeError):
    code = "SCREENING_FAILED"


class LocalCandidateScreeningTool:
    """FFmpeg motion profile + local scorer + hysteresis adapter.

    Bu adapter ağ çağrısı yapmaz. Learned CNN/ONNX scorer daha sonra aynı
    ``model.score`` arayüzüne takılabilir; interval üretimi değişmez.
    """

    def __init__(
        self,
        *,
        video_root: Path | None = None,
        base_fps: float | None = None,
        interval_config: IntervalConfig | None = None,
        model: CandidateScorer | None = None,
        cache: JsonFeatureCache | None = None,
    ) -> None:
        self.video_root = (video_root or settings.media_dir).resolve()
        self.base_fps = settings.base_fps if base_fps is None else base_fps
        if self.base_fps <= 0:
            raise ValueError("screening base_fps pozitif olmalı")
        self.interval_config = interval_config or IntervalConfig(
            start_threshold=settings.candidate_start_threshold,
            continue_threshold=settings.candidate_continue_threshold,
            end_patience=settings.candidate_end_patience,
            merge_gap_seconds=settings.candidate_merge_gap_seconds,
            min_duration_seconds=settings.candidate_min_duration_seconds,
            threshold_version=settings.candidate_threshold_version,
        )
        # Manifest hash'i doğrulanmadan hiçbir candidate artifact'i yüklenmez.
        # Varsayılan manifest hâlâ ölçülebilir motion baseline'dır; local temporal
        # CNN eğitildikten sonra yalnız DORTGOZ_CANDIDATE_MANIFEST_PATH değiştirilir.
        self.model = model or load_candidate_scorer(settings.candidate_manifest_path)
        self.cache = cache

    @property
    def model_id(self) -> str:
        return self.model.model_id

    async def screen(
        self, metadata: VideoMetadata, analysis_id: str
    ) -> list[CandidateEvent]:
        path = self._resolve_video(metadata)
        samples = None
        key = None
        if self.cache is not None:
            key = FeatureCacheKey(
                video_hash_sha256=metadata.file_hash_sha256,
                model_id=self.model.model_id,
                feature_version="motion-v1",
            )
            cached = self.cache.load(key)
            samples = cached.samples if cached is not None else None
        if samples is None:
            profile = await motion_profile(path, base_fps=self.base_fps)
            samples = self.model.score(profile)
            if self.cache is not None and key is not None:
                self.cache.save(key, samples)
        return build_candidate_intervals(
            samples,
            analysis_id=analysis_id,
            video_id=metadata.video_id,
            duration_seconds=metadata.duration_seconds,
            model_id=self.model.model_id,
            config=self.interval_config,
        )

    def _resolve_video(self, metadata: VideoMetadata) -> Path:
        target = (self.video_root / metadata.media_path).resolve()
        if not target.is_relative_to(self.video_root):
            raise ScreeningError("video path storage kökü dışına çıkıyor")
        if not target.is_file():
            raise ScreeningError(f"video screening için bulunamadı: {metadata.media_path}")
        return target


def screening_tool_contract(tool: ScreeningTool) -> ScreeningTool:
    """Tip seviyesinde contract helper; runtime'da no-op'tur."""

    return tool


__all__ = ["LocalCandidateScreeningTool", "ScreeningError", "screening_tool_contract"]
