"""Yerel SQLite event memory kalıcılığı."""

from __future__ import annotations

from pathlib import Path

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.provenance import AnalysisProvenance, HumanReview, ReviewDecision, TraceRecord
from dortgoz.domain.video import VideoMetadata
from dortgoz.repositories.sqlite import SqliteEventRepository

VIDEO_ID = "00000000-0000-0000-0000-000000000101"
ANALYSIS_ID = "00000000-0000-0000-0000-000000000102"


def _metadata() -> VideoMetadata:
    return VideoMetadata(
        video_id=VIDEO_ID,
        original_filename="offline-fixture.mp4",
        stored_filename=f"{VIDEO_ID}.mp4",
        media_path=f"{VIDEO_ID}.mp4",
        file_size_bytes=100,
        file_hash_sha256="b" * 64,
        container="mov",
        codec="h264",
        width=640,
        height=480,
        fps=25,
        duration_seconds=60,
        has_audio=False,
        time_base="1/12800",
    )


def _candidate() -> CandidateEvent:
    return CandidateEvent(
        candidate_id="candidate-offline-1",
        analysis_id=ANALYSIS_ID,
        video_id=VIDEO_ID,
        start_time=10,
        peak_time=12,
        end_time=15,
        candidate_type=CandidateType.POSSIBLE_FIGHT,
        peak_score=0.8,
        anomaly_score=0.8,
        trigger_signals=["fixture"],
        screening_model_id="fixture-screening",
        threshold_version="test-v1",
    )


def _event() -> VerifiedEvent:
    return VerifiedEvent(
        event_id="event-offline-1",
        analysis_id=ANALYSIS_ID,
        video_id=VIDEO_ID,
        candidate_id="candidate-offline-1",
        status=EventStatus.HUMAN_REVIEW,
        event_type=VerifiedEventType.PHYSICAL_FIGHT,
        start_time=10,
        peak_time=12,
        end_time=15,
        confidence=0.8,
    )


def test_sqlite_repository_persists_event_review_and_trace_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "event_memory.sqlite3"
    first = SqliteEventRepository(database_path)
    first.create_video(_metadata())
    first.create_analysis(
        VIDEO_ID,
        AnalysisProvenance(
            contract_version="1.0.0",
            config_version="test-v1",
            code_revision="test-revision",
        ),
        analysis_id=ANALYSIS_ID,
    )
    first.save_candidate(_candidate())
    first.save_trace_item(
        ANALYSIS_ID,
        "candidate-offline-1",
        TraceRecord(
            step=1,
            action="RUN_VLM",
            reason="offline fixture",
            policy_rule_id="P-TEST",
            success=True,
            policy_version="test-v1",
        ),
    )
    first.save_event(_event())
    first.save_review(
        HumanReview(
            review_id="review-offline-1",
            event_id="event-offline-1",
            decision=ReviewDecision.REJECT,
            note="Yerel restart testi",
            reviewer="operator",
            revision=1,
        )
    )

    restarted = SqliteEventRepository(database_path)

    assert restarted.persistence_mode == "sqlite"
    assert restarted.get_video(VIDEO_ID) is not None
    assert restarted.get_analysis(ANALYSIS_ID) is not None
    assert restarted.get_event("event-offline-1").status == EventStatus.REJECTED
    assert len(restarted.get_trace(ANALYSIS_ID, "candidate-offline-1")) == 1
    assert [item.revision for item in restarted.list_event_revisions("event-offline-1")] == [1, 2]
