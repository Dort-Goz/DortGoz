

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
    RuleProposal,
    RuleProposalStatus,
)
from dortgoz.domain.media import IncidentMedia
from dortgoz.domain.memory import AnalysisRecord
from dortgoz.domain.priority import InterventionBand, InterventionPriority
from dortgoz.domain.provenance import (
    AnalysisProvenance,
    HumanReview,
    MaintenanceReview,
    ReviewDecision,
    TraceRecord,
)
from dortgoz.domain.training import (
    FrameReviewResult,
    TrainingFrameReview,
    TrainingSample,
    TrainingSampleStatus,
    VerifiedBoundingBox,
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


def test_sqlite_v8_uses_normalized_tables_and_persists_development_gate(
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
    maintenance_review = repository.save_maintenance_review(
        MaintenanceReview(
            maintenance_review_id="maintenance-feedback-1",
            event_id="event-offline-1",
            operator_review_id=review.review_id,
            decision=ReviewDecision.REJECT,
            false_alarm_reason=FalseAlarmReason.NORMAL_ACTIVITY,
            note="IT kaydı bağımsız inceledi.",
            reviewer="it-operator",
            revision=1,
        )
    )
    repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-feedback-1",
            event_id="event-offline-1",
            review_id=review.review_id,
            maintenance_review_id=maintenance_review.maintenance_review_id,
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
            "maintenance_reviews",
            "development_approvals",
            "rule_proposals",
            "incident_media",
            "intervention_priorities",
            "training_samples",
            "training_jobs",
            "model_versions",
            "decision_traces",
            "audit_log",
        } <= tables
        assert "repository_snapshot" not in tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert connection.execute("SELECT COUNT(*) FROM human_reviews").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM maintenance_reviews").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM development_approvals").fetchone()[0]
            == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 3

    restarted = SqliteEventRepository(database_path)
    reviews = restarted.list_reviews("event-offline-1")
    maintenance_reviews = restarted.list_maintenance_reviews("event-offline-1")
    approvals = restarted.list_development_approvals("event-offline-1")

    assert reviews[0].false_alarm_reason == FalseAlarmReason.NORMAL_ACTIVITY
    assert reviews[0].intervention_required is False
    assert maintenance_reviews[0].operator_review_id == reviews[0].review_id
    assert approvals[0].maintenance_review_id == maintenance_reviews[0].maintenance_review_id
    assert approvals[0].approved_uses == [
        DevelopmentUse.THRESHOLD_CALIBRATION,
        DevelopmentUse.EVALUATION,
    ]


def test_sqlite_persists_incident_media_and_audit(tmp_path: Path) -> None:
    database_path = tmp_path / "incident-media.sqlite3"
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
    event = repository.save_event(_event())
    saved = repository.save_incident_media(
        IncidentMedia(
            media_id="incident-media-1",
            event_id=event.event_id,
            analysis_id=event.analysis_id,
            video_id=event.video_id,
            event_revision=event.revision,
            source_refs=[f"{VIDEO_ID}.mp4"],
            source_file_sha256="b" * 64,
            clip_ref="_incident_media/incident-media-1/incident.mp4",
            thumbnail_ref="_incident_media/incident-media-1/thumbnail.jpg",
            clip_start=2,
            clip_end=23,
            peak_time=12,
            pre_capture_seconds=8,
            post_capture_seconds=8,
            clip_sha256="c" * 64,
            thumbnail_sha256="d" * 64,
            clip_size_bytes=200,
            thumbnail_size_bytes=50,
        )
    )
    repository.close()

    restarted = SqliteEventRepository(database_path)
    restored = restarted.get_incident_media_for_event(event.event_id)

    assert restored == saved
    assert restarted.list_incident_media(ANALYSIS_ID) == [saved]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM incident_media").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'incident_media_saved'"
        ).fetchone()[0] == 1


def test_sqlite_persists_intervention_priority_and_audit(tmp_path: Path) -> None:
    database_path = tmp_path / "intervention-priority.sqlite3"
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
    event = repository.save_event(_event())
    saved = repository.save_intervention_priority(
        InterventionPriority(
            priority_id="priority-offline-1",
            event_id=event.event_id,
            analysis_id=event.analysis_id,
            event_revision=event.revision,
            score=80,
            band=InterventionBand.URGENT,
            reasons=["Olası silahlı olay güvenlik tabanı: 80"],
            risk_input="dusuk",
            event_type_input="possible_armed_incident",
            phase_input="sonuclandi",
            needs_review_input=False,
            model_confidence=0.01,
            ruleset_version="intervention-priority-v1",
        )
    )
    repository.close()

    restarted = SqliteEventRepository(database_path)
    restored = restarted.get_intervention_priority_for_event(event.event_id)

    assert restored == saved
    assert restarted.list_intervention_priorities(ANALYSIS_ID) == [saved]
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM intervention_priorities").fetchone()[0]
            == 1
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE action = 'intervention_priority_saved'"
        ).fetchone()[0] == 1


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

    assert migrated.schema_version == 9
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


def test_rule_proposal_lifecycle_persists_with_audit_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rules.sqlite3"
    repository = SqliteEventRepository(database_path)
    created = repository.create_rule_proposal(
        RuleProposal(
            proposal_id="proposal-1",
            feed="KAM-1",
            category="vandalizm",
            source_event_ids=["event-1"],
            source_review_ids=["review-1"],
            reason="İlk ret toplandı.",
        )
    )
    proposed = RuleProposal.model_validate(
        {
            **created.model_dump(),
            "status": RuleProposalStatus.PROPOSED,
            "dismissal_count": 3,
            "source_review_ids": ["review-1", "review-2", "review-3"],
            "source_event_ids": ["event-1", "event-2", "event-3"],
            "reason": "Üç operatör reddi toplandı.",
            "revision": 2,
        }
    )
    repository.update_rule_proposal(proposed)
    repository.close()

    restarted = SqliteEventRepository(database_path)
    stored = restarted.get_rule_proposal("proposal-1")
    assert stored is not None
    assert stored.status == RuleProposalStatus.PROPOSED
    assert stored.dismissal_count == 3
    restarted.close()

    with sqlite3.connect(database_path) as connection:
        actions = {
            row[0]
            for row in connection.execute(
                "SELECT action FROM audit_log WHERE subject_id = 'proposal-1'"
            )
        }
    assert actions == {"rule_proposal_created", "rule_proposal_proposed"}


def test_sqlite_v2_database_adds_training_samples_without_losing_events(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "schema-v2.sqlite3"
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
    repository.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE training_samples")
        connection.execute("PRAGMA user_version = 2")

    migrated = SqliteEventRepository(database_path)

    assert migrated.schema_version == 9
    assert migrated.get_event("event-offline-1") is not None
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "training_samples" in tables


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


def test_sqlite_training_sample_review_and_revocation_survive_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "training-samples.sqlite3"
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
    human_review = repository.save_review(
        HumanReview(
            review_id="review-training-1",
            event_id="event-offline-1",
            decision=ReviewDecision.EDIT,
            start_time=10,
            peak_time=12,
            end_time=15,
            note="Kare aralığı doğrulandı.",
            reviewer="operator",
            revision=1,
        )
    )
    approval = repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-training-1",
            event_id="event-offline-1",
            review_id=human_review.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.D_FINE_TRAINING],
            reviewer="operator",
            note="D-FINE için kullanılabilir.",
        )
    )
    sample = repository.create_training_samples(
        [
            TrainingSample(
                sample_id="sample-training-1",
                event_id="event-offline-1",
                event_revision=2,
                review_id=human_review.review_id,
                approval_id=approval.approval_id,
                video_id=VIDEO_ID,
                source_video_sha256="b" * 64,
                dataset_id="owned-approved",
                dataset_fingerprint="c" * 64,
                dataset_video_id="owned/train",
                source_video_ref="videos/train.mp4",
                split="train",
                timestamp_seconds=12,
                selection_reason="event_peak",
                frame_ref="_training_samples/sample-training-1.jpg",
                frame_sha256="d" * 64,
                frame_size_bytes=1000,
                image_width=640,
                image_height=360,
                status=TrainingSampleStatus.PENDING_REVIEW,
                prepared_by="operator",
            )
        ]
    )[0]
    verified = repository.verify_training_sample(
        sample.sample_id,
        TrainingFrameReview(
            annotation_id=sample.sample_id,
            dataset_id=sample.dataset_id,
            dataset_fingerprint=sample.dataset_fingerprint,
            dataset_video_id=sample.dataset_video_id,
            source_video_ref=sample.source_video_ref,
            frame_ref=sample.frame_ref,
            frame_sha256=sample.frame_sha256,
            frame_size_bytes=sample.frame_size_bytes,
            timestamp_seconds=sample.timestamp_seconds,
            image_width=sample.image_width,
            image_height=sample.image_height,
            split=sample.split,
            review_result=FrameReviewResult.VERIFIED_BOXES,
            boxes=[
                VerifiedBoundingBox(
                    category_name="person", x=10, y=20, width=30, height=40
                )
            ],
            human_verified=True,
            reviewer="operator",
            annotation_tool="Dortgoz UI",
            reviewed_at="2026-08-16T12:00:00Z",
        ),
    )
    assert verified.status == TrainingSampleStatus.VERIFIED
    revocation = repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-training-revoked",
            event_id="event-offline-1",
            review_id=human_review.review_id,
            status=DevelopmentApprovalStatus.REVOKED,
            reviewer="operator",
            note="İzin geri çekildi.",
            supersedes_approval_id=approval.approval_id,
        )
    )
    replacement = repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-training-replacement",
            event_id="event-offline-1",
            review_id=human_review.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.D_FINE_TRAINING],
            reviewer="operator",
            note="Yeni D-FINE izni verildi.",
            supersedes_approval_id=revocation.approval_id,
        )
    )
    second_sample = repository.create_training_samples(
        [
            TrainingSample(
                sample_id="sample-training-2",
                event_id="event-offline-1",
                event_revision=2,
                review_id=human_review.review_id,
                approval_id=replacement.approval_id,
                video_id=VIDEO_ID,
                source_video_sha256="b" * 64,
                dataset_id="owned-approved",
                dataset_fingerprint="c" * 64,
                dataset_video_id="owned/train",
                source_video_ref="videos/train.mp4",
                split="train",
                timestamp_seconds=13,
                selection_reason="operator_selected",
                frame_ref="_training_samples/sample-training-2.jpg",
                frame_sha256="e" * 64,
                frame_size_bytes=1000,
                image_width=640,
                image_height=360,
                status=TrainingSampleStatus.PENDING_REVIEW,
                prepared_by="operator",
            )
        ]
    )[0]
    newer_review = repository.save_review(
        HumanReview(
            review_id="review-training-newer",
            event_id="event-offline-1",
            decision=ReviewDecision.EDIT,
            note="Olay yeniden incelendi.",
            reviewer="operator",
            revision=1,
        )
    )
    repository.close()

    restarted = SqliteEventRepository(database_path)
    stored = restarted.get_training_sample(sample.sample_id)

    assert stored is not None
    assert stored.status == TrainingSampleStatus.REVOKED
    assert stored.frame_review is not None
    assert stored.revision == 3
    invalidated = restarted.get_training_sample(second_sample.sample_id)
    assert invalidated is not None
    assert invalidated.status == TrainingSampleStatus.REVOKED
    assert invalidated.invalidated_by_review_id == newer_review.review_id
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM training_samples").fetchone()[0] == 2
        actions = {
            row[0]
            for row in connection.execute(
                "SELECT action FROM audit_log WHERE subject_type = 'training_sample'"
            )
        }
    assert actions == {
        "training_sample_prepared",
        "training_sample_verified",
        "training_sample_revoked",
        "training_sample_invalidated_by_review",
    }


def test_duplicate_content_registers_as_own_video(tmp_path: Path) -> None:
    database_path = tmp_path / "event_memory.sqlite3"
    repository = SqliteEventRepository(database_path)
    repository.create_video(_metadata())
    duplicate_id = "00000000-0000-0000-0000-000000000201"
    duplicate = _metadata().model_copy(
        update={
            "video_id": duplicate_id,
            "stored_filename": f"{duplicate_id}.mp4",
            "media_path": f"{duplicate_id}.mp4",
        }
    )

    stored = repository.create_video(duplicate)

    assert stored.video_id == duplicate_id
    restarted = SqliteEventRepository(database_path)
    assert restarted.get_video(VIDEO_ID) is not None
    assert restarted.get_video(duplicate_id) is not None
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 2


def test_migration_drops_unique_hash_index(tmp_path: Path) -> None:
    database_path = tmp_path / "event_memory.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                file_hash_sha256 TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX idx_videos_file_hash ON videos(file_hash_sha256)"
        )
        connection.execute("PRAGMA user_version = 8")

    repository = SqliteEventRepository(database_path)
    repository.create_video(_metadata())
    duplicate_id = "00000000-0000-0000-0000-000000000202"
    repository.create_video(
        _metadata().model_copy(
            update={
                "video_id": duplicate_id,
                "stored_filename": f"{duplicate_id}.mp4",
                "media_path": f"{duplicate_id}.mp4",
            }
        )
    )

    assert repository.schema_version == 9
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 2
        unique_flags = connection.execute("PRAGMA index_list(videos)").fetchall()
        assert all(row[2] == 0 for row in unique_flags if row[1] == "idx_videos_file_hash")
