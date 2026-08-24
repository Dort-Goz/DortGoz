

from __future__ import annotations

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.priority import InterventionBand, intervention_band_for_score
from dortgoz.domain.provenance import AnalysisProvenance
from dortgoz.domain.taxonomy import VerifiedEventType
from dortgoz.domain.video import VideoMetadata
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.intervention_priority import (
    InterventionPriorityService,
    calculate_priority_score,
)

VIDEO_ID = "00000000-0000-0000-0000-000000000701"
ANALYSIS_ID = "analysis-priority-test"
EVENT_ID = "event-priority-test"


def _repository(*, confidence: float = 0.01) -> InMemoryEventRepository:
    repository = InMemoryEventRepository()
    repository.create_video(
        VideoMetadata(
            video_id=VIDEO_ID,
            original_filename="priority-test.mp4",
            stored_filename=f"{VIDEO_ID}.mp4",
            media_path=f"{VIDEO_ID}.mp4",
            file_size_bytes=100,
            file_hash_sha256="a" * 64,
            container="mp4",
            codec="h264",
            width=640,
            height=360,
            fps=25,
            duration_seconds=60,
            has_audio=False,
            time_base="1/25",
        )
    )
    repository.create_analysis(
        VIDEO_ID,
        AnalysisProvenance(
            contract_version="1", config_version="test", code_revision="test"
        ),
        analysis_id=ANALYSIS_ID,
    )
    repository.save_candidate(
        CandidateEvent(
            candidate_id="candidate-priority-test",
            analysis_id=ANALYSIS_ID,
            video_id=VIDEO_ID,
            start_time=1,
            peak_time=2,
            end_time=3,
            candidate_type=CandidateType.UNKNOWN_ANOMALY,
            peak_score=0.5,
            anomaly_score=0.5,
            trigger_signals=["test"],
            screening_model_id="test",
            threshold_version="test",
        )
    )
    repository.save_event(
        VerifiedEvent(
            event_id=EVENT_ID,
            analysis_id=ANALYSIS_ID,
            video_id=VIDEO_ID,
            candidate_id="candidate-priority-test",
            status=EventStatus.HUMAN_REVIEW,
            event_type=VerifiedEventType.POSSIBLE_ARMED_INCIDENT,
            start_time=1,
            peak_time=2,
            end_time=3,
            confidence=confidence,
        )
    )
    return repository


def test_score_is_deterministic_and_explainable() -> None:
    first = calculate_priority_score(
        risk="orta",
        event_type="vandalizm",
        phase="basladi",
        needs_review=False,
    )
    second = calculate_priority_score(
        risk="orta",
        event_type="vandalizm",
        phase="basladi",
        needs_review=False,
    )

    assert first == second
    assert first.score == 45
    assert first.reasons == (
        "Orta etki seviyesi: +30",
        "Vandalizm bağlamı: +5",
        "Olay devam ediyor: +10",
    )


def test_safety_critical_event_types_have_minimum_score() -> None:
    cases = {
        "silahli_olay": (80, InterventionBand.URGENT),
        "yangin": (80, InterventionBand.URGENT),
        "patlama": (80, InterventionBand.URGENT),
        "saldiri": (70, InterventionBand.HIGH),
        "arac_kazasi": (70, InterventionBand.HIGH),
        "kavga": (60, InterventionBand.HIGH),
    }

    for event_type, (expected_score, expected_band) in cases.items():
        result = calculate_priority_score(
            risk="dusuk",
            event_type=event_type,
            phase="sonuclandi",
            needs_review=False,
        )
        assert result.score == expected_score
        assert intervention_band_for_score(result.score) == expected_band


def test_low_model_confidence_never_reduces_armed_incident_priority() -> None:
    repository = _repository(confidence=0.01)
    saved = InterventionPriorityService(repository).assess_and_save(
        EVENT_ID,
        risk="dusuk",
        event_type="silahli_olay",
        phase="sonuclandi",
        needs_review=False,
    )

    assert saved.score == 80
    assert saved.band == InterventionBand.URGENT
    assert saved.model_confidence == 0.01


def test_same_inputs_are_idempotent_and_changed_inputs_create_revision() -> None:
    repository = _repository()
    service = InterventionPriorityService(repository)
    first = service.assess_and_save(
        EVENT_ID,
        risk="orta",
        event_type="vandalizm",
        phase="basladi",
        needs_review=False,
    )
    repeated = service.assess_and_save(
        EVENT_ID,
        risk="orta",
        event_type="vandalizm",
        phase="basladi",
        needs_review=False,
    )
    changed = service.assess_and_save(
        EVENT_ID,
        risk="kritik",
        event_type="vandalizm",
        phase="sonuclandi",
        needs_review=False,
    )

    assert repeated == first
    assert changed.priority_id == first.priority_id
    assert changed.revision == 2
    assert changed.score == 75
    assert changed.band == InterventionBand.HIGH
