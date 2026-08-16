"""Yerel SQLite event memory kalıcılığı."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
    FalseAlarmReason,
)
from dortgoz.domain.memory import AnalysisRecord
from dortgoz.domain.provenance import (
    AnalysisProvenance,
    HumanReview,
    ReviewDecision,
    TraceRecord,
)
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


def test_sqlite_v2_uses_normalized_tables_and_persists_development_gate(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "feedback.sqlite3"
    repository = SqliteEventRepository(database_path)
    repository.create_video(_metadata())
    repository.create_analysis(
        VIDEO_ID,
        AnalysisProvenance(
            contract_version="1.0.0",
            config_version="test-v1",
            code_revision="test-revision",
        ),
        analysis_id=ANALYSIS_ID,
    )
    repository.save_candidate(_candidate())
    repository.save_event(_event())
    review = repository.save_review(
        HumanReview(
            review_id="review-feedback-1",
            event_id="event-offline-1",
            decision=ReviewDecision.REJECT,
            false_alarm_reason=FalseAlarmReason.NORMAL_ACTIVITY,
            intervention_required=False,
            note="Olağan hareket yanlış alarm üretti.",
            reviewer="operator",
            revision=1,
        )
    )
    repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-feedback-1",
            event_id="event-offline-1",
            review_id=review.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[
                DevelopmentUse.THRESHOLD_CALIBRATION,
                DevelopmentUse.EVALUATION,
            ],
            reviewer="operator",
            note="Yanlış alarm kalibrasyon için yararlı.",
        )
    )
    repository.close()

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "videos",
            "analyses",
            "candidates",
            "events",
            "event_revisions",
            "human_reviews",
            "development_approvals",
            "decision_traces",
            "audit_log",
        } <= tables
        assert "repository_snapshot" not in tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM human_reviews").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM development_approvals").fetchone()[0]
            == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 2

    restarted = SqliteEventRepository(database_path)
    reviews = restarted.list_reviews("event-offline-1")
    approvals = restarted.list_development_approvals("event-offline-1")

    assert reviews[0].false_alarm_reason == FalseAlarmReason.NORMAL_ACTIVITY
    assert reviews[0].intervention_required is False
    assert approvals[0].approved_uses == [
        DevelopmentUse.THRESHOLD_CALIBRATION,
        DevelopmentUse.EVALUATION,
    ]


def test_sqlite_v1_snapshot_is_migrated_without_deleting_rollback_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    provenance = AnalysisProvenance(
        contract_version="1.0.0",
        config_version="test-v1",
        code_revision="test-revision",
    )
    payload = {
        "videos": [_metadata().model_dump(mode="json")],
        "analyses": [
            AnalysisRecord(
                analysis_id=ANALYSIS_ID,
                video_id=VIDEO_ID,
                provenance=provenance,
            ).model_dump(mode="json")
        ],
        "candidates": [_candidate().model_dump(mode="json")],
        "events": [_event().model_dump(mode="json")],
        "event_history": {},
        "reviews": [],
        "traces": [],
    }
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE repository_snapshot (
                snapshot_id INTEGER PRIMARY KEY CHECK (snapshot_id = 1),
                schema_version INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO repository_snapshot VALUES (1, 1, ?)",
            (json.dumps(payload, ensure_ascii=False),),
        )

    migrated = SqliteEventRepository(database_path)

    assert migrated.schema_version == 2
    assert migrated.get_video(VIDEO_ID) is not None
    assert migrated.get_event("event-offline-1") is not None
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM repository_snapshot").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'legacy_snapshot_migrated'"
            ).fetchone()[0]
            == 1
        )


def test_sqlite_agent_bundle_is_atomic_and_survives_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "bundle.sqlite3"
    repository = SqliteEventRepository(database_path)
    repository.create_video(_metadata())
    repository.create_analysis(
        VIDEO_ID,
        AnalysisProvenance(
            contract_version="1.0.0",
            config_version="test-v1",
            code_revision="test-revision",
        ),
        analysis_id=ANALYSIS_ID,
    )
    repository.save_agent_bundle(
        _candidate(),
        [
            TraceRecord(
                step=1,
                action="RUN_VLM",
                reason="atomic bundle fixture",
                policy_rule_id="P-TEST",
                success=True,
                policy_version="test-v1",
            )
        ],
        _event(),
    )
    repository.close()

    restarted = SqliteEventRepository(database_path)

    assert restarted.get_event("event-offline-1") is not None
    assert len(restarted.get_trace(ANALYSIS_ID, "candidate-offline-1")) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM decision_traces").fetchone()[0] == 1


def test_development_approval_requires_explicit_use_and_revoke_target() -> None:
    with pytest.raises(ValidationError):
        DevelopmentApproval(
            approval_id="approval-invalid-1",
            event_id="event-offline-1",
            review_id="review-offline-1",
            status=DevelopmentApprovalStatus.APPROVED,
            reviewer="operator",
            note="Kullanım alanı seçilmedi.",
        )

    with pytest.raises(ValidationError):
        DevelopmentApproval(
            approval_id="approval-invalid-2",
            event_id="event-offline-1",
            review_id="review-offline-1",
            status=DevelopmentApprovalStatus.REVOKED,
            reviewer="operator",
            note="Önceki karar belirtilmedi.",
        )
