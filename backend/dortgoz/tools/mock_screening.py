"""GPU gerektirmeyen, üç farklı routing dalı üreten screening mock'u."""

from __future__ import annotations

from ..domain.candidate import CandidateEvent, CandidateType
from ..domain.video import VideoMetadata


class MockScreeningTool:
    model_id = "mock-screening-v1"
    threshold_version = "mock-thresholds-v1"

    async def screen(
        self, metadata: VideoMetadata, analysis_id: str
    ) -> list[CandidateEvent]:
        if metadata.duration_seconds < 6:
            return []

        def interval(fraction: float) -> tuple[float, float, float]:
            peak = metadata.duration_seconds * fraction
            radius = max(0.5, min(3.0, metadata.duration_seconds * 0.04))
            return max(0.0, peak - radius), peak, min(
                metadata.duration_seconds, peak + radius
            )

        freeze_start, freeze_peak, freeze_end = interval(0.15)
        normal_start, normal_peak, normal_end = interval(0.48)
        review_start, review_peak, review_end = interval(0.80)
        common = {
            "analysis_id": analysis_id,
            "video_id": metadata.video_id,
            "screening_model_id": self.model_id,
            "threshold_version": self.threshold_version,
        }
        return [
            CandidateEvent(
                candidate_id=f"{analysis_id}-freeze",
                start_time=freeze_start,
                peak_time=freeze_peak,
                end_time=freeze_end,
                candidate_type=CandidateType.CAMERA_FREEZE,
                peak_score=0.97,
                anomaly_score=0.97,
                tampering_score=0.96,
                image_quality=0.94,
                trigger_signals=["frame_difference_near_zero"],
                **common,
            ),
            CandidateEvent(
                candidate_id=f"{analysis_id}-normal",
                start_time=normal_start,
                peak_time=normal_peak,
                end_time=normal_end,
                candidate_type=CandidateType.INTENSE_PERSON_INTERACTION,
                peak_score=0.68,
                anomaly_score=0.62,
                interaction_score=0.68,
                image_quality=0.88,
                trigger_signals=["brief_person_proximity"],
                **common,
            ),
            CandidateEvent(
                candidate_id=f"{analysis_id}-ambiguous",
                start_time=review_start,
                peak_time=review_peak,
                end_time=review_end,
                candidate_type=CandidateType.POSSIBLE_FIGHT,
                peak_score=0.91,
                anomaly_score=0.89,
                interaction_score=0.91,
                image_quality=0.20,
                trigger_signals=["occluded_rapid_interaction"],
                **common,
            ),
        ]
