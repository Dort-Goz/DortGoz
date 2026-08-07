"""Hibrit ön-kapı: aday-aralık kapsaması + koşucu karar bileşenleri."""

from dortgoz.config import settings
from dortgoz.pipeline.candidate_intervals import IntervalConfig
from dortgoz.pipeline.candidate_model import MotionBaselineModel
from dortgoz.pipeline.ingest import MotionSample
from dortgoz.pipeline.runner import screening_covers


def test_screening_covers_overlap_semantics():
    spans = [(10.0, 40.0), (100.0, 130.0)]
    assert screening_covers(0.0, 30.0, spans)        # kısmi örtüşme yeter
    assert screening_covers(30.0, 60.0, spans)
    assert screening_covers(95.0, 125.0, spans)
    assert not screening_covers(60.0, 90.0, spans)   # aralıklar arası boşluk
    assert not screening_covers(140.0, 170.0, spans)
    assert not screening_covers(40.0, 100.0, [])     # aday yoksa kapsama yok


def test_settings_map_to_interval_config():
    cfg = IntervalConfig(
        start_threshold=settings.candidate_start_threshold,
        continue_threshold=settings.candidate_continue_threshold,
        end_patience=settings.candidate_end_patience,
        merge_gap_seconds=settings.candidate_merge_gap_seconds,
        min_duration_seconds=settings.candidate_min_duration_seconds,
        threshold_version=settings.candidate_threshold_version,
    )
    assert cfg.start_threshold >= cfg.continue_threshold


def test_baseline_scorer_produces_spans_over_active_profile():
    """Hareketli bölge aday olur, ölü bölge olmaz — hibrit kapının temel sözü."""
    profile = [
        MotionSample(t=float(t), fg=0.0, mad=0.0,
                     changed=(0.5 if 30 <= t < 60 else 0.001))
        for t in range(120)
    ]
    ivs = MotionBaselineModel().candidates(
        profile, analysis_id="t", video_id="v", duration_seconds=120.0)
    spans = [(iv.start_time, iv.end_time) for iv in ivs]
    assert spans, "hareketli bölge aday üretmeli"
    assert screening_covers(30.0, 60.0, spans)
    assert not screening_covers(90.0, 120.0, spans)
