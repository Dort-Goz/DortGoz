"""Normalized SQLite-backed, local-only event memory adapter.

Schema v7 stores each entity in its own row. Existing schema-v1 JSON snapshots
are imported once and kept untouched as a rollback artifact.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from ..domain.candidate import CandidateEvent
from ..domain.event import VerifiedEvent
from ..domain.feedback import DevelopmentApproval, RuleProposal
from ..domain.media import IncidentMedia
from ..domain.memory import AnalysisRecord
from ..domain.model_lifecycle import ModelVersion, TrainingJob
from ..domain.priority import InterventionPriority
from ..domain.provenance import HumanReview, TraceRecord
from ..domain.training import TrainingFrameReview, TrainingSample
from ..domain.video import VideoMetadata
from .bundles import FeedbackWriteBundle, FeedbackWriteResult
from .errors import RepositoryConflictError, RepositoryError
from .memory import InMemoryEventRepository

_T = TypeVar("_T")
_DATABASE_SCHEMA_VERSION = 7
_LEGACY_SNAPSHOT_VERSION = 1


class SqliteEventRepository(InMemoryEventRepository):
    """Persist the repository contract in indexed, append-friendly SQLite rows."""

    def __init__(self, database_path: Path) -> None:
        super().__init__()
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._batch_mutation = False
        self._migrate_schema()
        self._migrate_legacy_snapshot()
        self._load_database()

    @property
    def persistence_mode(self) -> str:
        return "sqlite"

    @property
    def schema_version(self) -> int:
        row = self._connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._connection.close()

    def _migrate_schema(self) -> None:
        current_version = self.schema_version
        if current_version > _DATABASE_SCHEMA_VERSION:
            raise RepositoryError(f"desteklenmeyen event store şeması: {current_version}")
        try:
            with self._connection:
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS videos (
                        video_id TEXT PRIMARY KEY,
                        file_hash_sha256 TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_file_hash
                        ON videos(file_hash_sha256);

                    CREATE TABLE IF NOT EXISTS analyses (
                        analysis_id TEXT PRIMARY KEY,
                        video_id TEXT NOT NULL REFERENCES videos(video_id),
                        status TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_analyses_video
                        ON analyses(video_id);

                    CREATE TABLE IF NOT EXISTS candidates (
                        candidate_id TEXT PRIMARY KEY,
                        analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
                        video_id TEXT NOT NULL REFERENCES videos(video_id),
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_candidates_analysis
                        ON candidates(analysis_id);

                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
                        video_id TEXT NOT NULL REFERENCES videos(video_id),
                        candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
                        status TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_analysis_status
                        ON events(analysis_id, status, created_at);

                    CREATE TABLE IF NOT EXISTS event_revisions (
                        event_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        PRIMARY KEY(event_id, revision)
                    );

                    CREATE TABLE IF NOT EXISTS human_reviews (
                        review_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL REFERENCES events(event_id),
                        decision TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        reviewer TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_human_reviews_event
                        ON human_reviews(event_id, revision, created_at);

                    CREATE TABLE IF NOT EXISTS development_approvals (
                        approval_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL REFERENCES events(event_id),
                        review_id TEXT NOT NULL REFERENCES human_reviews(review_id),
                        status TEXT NOT NULL,
                        reviewer TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_development_approvals_event
                        ON development_approvals(event_id, created_at);

                    CREATE TABLE IF NOT EXISTS rule_proposals (
                        proposal_id TEXT PRIMARY KEY,
                        feed TEXT NOT NULL,
                        category TEXT NOT NULL,
                        status TEXT NOT NULL,
                        expires_at TEXT,
                        revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_rule_proposals_scope_status
                        ON rule_proposals(feed, category, status, created_at);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_rule_proposals_active_scope
                        ON rule_proposals(feed, category)
                        WHERE status IN ('collecting', 'proposed', 'approved');

                    CREATE TABLE IF NOT EXISTS incident_media (
                        media_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
                        analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
                        video_id TEXT NOT NULL REFERENCES videos(video_id),
                        event_revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_incident_media_analysis
                        ON incident_media(analysis_id, created_at);

                    CREATE TABLE IF NOT EXISTS intervention_priorities (
                        priority_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
                        analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
                        score INTEGER NOT NULL,
                        band TEXT NOT NULL,
                        ruleset_version TEXT NOT NULL,
                        event_revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        calculated_at TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_intervention_priority_queue
                        ON intervention_priorities(score DESC, created_at, priority_id);
                    CREATE INDEX IF NOT EXISTS idx_intervention_priority_analysis
                        ON intervention_priorities(analysis_id, score DESC, created_at);

                    CREATE TABLE IF NOT EXISTS training_samples (
                        sample_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL REFERENCES events(event_id),
                        review_id TEXT NOT NULL REFERENCES human_reviews(review_id),
                        approval_id TEXT NOT NULL REFERENCES development_approvals(approval_id),
                        dataset_id TEXT NOT NULL,
                        dataset_fingerprint TEXT NOT NULL,
                        split TEXT NOT NULL,
                        status TEXT NOT NULL,
                        timestamp_seconds REAL NOT NULL,
                        frame_sha256 TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_training_samples_event_status
                        ON training_samples(event_id, status, created_at);
                    CREATE INDEX IF NOT EXISTS idx_training_samples_dataset
                        ON training_samples(dataset_id, dataset_fingerprint, split);

                    CREATE TABLE IF NOT EXISTS training_jobs (
                        job_id TEXT PRIMARY KEY,
                        dataset_id TEXT NOT NULL,
                        dataset_fingerprint TEXT NOT NULL,
                        export_fingerprint TEXT NOT NULL,
                        architecture TEXT NOT NULL,
                        status TEXT NOT NULL,
                        requested_by TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_training_jobs_dataset_status
                        ON training_jobs(dataset_id, dataset_fingerprint, status, created_at);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_training_jobs_single_running
                        ON training_jobs(status) WHERE status = 'running';

                    CREATE TABLE IF NOT EXISTS model_versions (
                        model_version_id TEXT PRIMARY KEY,
                        training_job_id TEXT NOT NULL REFERENCES training_jobs(job_id),
                        architecture TEXT NOT NULL,
                        checkpoint_sha256 TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_model_versions_job
                        ON model_versions(training_job_id);
                    CREATE INDEX IF NOT EXISTS idx_model_versions_stage_created
                        ON model_versions(stage, created_at);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_model_versions_single_champion
                        ON model_versions(stage) WHERE stage = 'champion';

                    CREATE TABLE IF NOT EXISTS decision_traces (
                        analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
                        candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
                        step INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        PRIMARY KEY(analysis_id, candidate_id, step)
                    );

                    CREATE TABLE IF NOT EXISTS audit_log (
                        entry_id TEXT PRIMARY KEY,
                        action TEXT NOT NULL,
                        subject_type TEXT NOT NULL,
                        subject_id TEXT NOT NULL,
                        actor TEXT,
                        occurred_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_audit_subject
                        ON audit_log(subject_type, subject_id, occurred_at);
                    """
                )
                self._connection.execute(f"PRAGMA user_version = {_DATABASE_SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise RepositoryError(f"event store şeması oluşturulamadı: {exc}") from exc

    def _migrate_legacy_snapshot(self) -> None:
        if self._connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0]:
            return
        has_legacy_table = self._connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'repository_snapshot'
            """
        ).fetchone()
        if has_legacy_table is None:
            return
        row = self._connection.execute(
            "SELECT schema_version, payload FROM repository_snapshot WHERE snapshot_id = 1"
        ).fetchone()
        if row is None:
            return
        if row["schema_version"] != _LEGACY_SNAPSHOT_VERSION:
            raise RepositoryError(
                f"desteklenmeyen legacy event store şeması: {row['schema_version']}"
            )
        data = self._decode_legacy_payload(row["payload"])
        try:
            with self._connection:
                for item in data["videos"]:
                    self._write_video(item)
                for item in data["analyses"]:
                    self._write_analysis(item)
                for item in data["candidates"]:
                    self._write_candidate(item)
                for item in data["events"]:
                    self._write_event(item)
                for revisions in data["event_history"].values():
                    for item in revisions:
                        self._write_event_revision(item)
                for item in data["reviews"]:
                    self._write_review(item)
                for trace_group in data["traces"]:
                    for item in trace_group["items"]:
                        self._write_trace(
                            trace_group["analysis_id"], trace_group["candidate_id"], item
                        )
                self._write_audit(
                    action="legacy_snapshot_migrated",
                    subject_type="database",
                    subject_id=str(self.database_path),
                    actor=None,
                    payload={"from_version": 1, "to_version": 7},
                )
        except sqlite3.Error as exc:
            raise RepositoryError(f"legacy event store taşınamadı: {exc}") from exc

    @staticmethod
    def _decode_legacy_payload(raw_payload: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_payload)
            return {
                "videos": [VideoMetadata.model_validate(item) for item in payload["videos"]],
                "analyses": [AnalysisRecord.model_validate(item) for item in payload["analyses"]],
                "candidates": [
                    CandidateEvent.model_validate(item) for item in payload["candidates"]
                ],
                "events": [VerifiedEvent.model_validate(item) for item in payload["events"]],
                "event_history": {
                    event_id: [VerifiedEvent.model_validate(item) for item in revisions]
                    for event_id, revisions in payload["event_history"].items()
                },
                "reviews": [HumanReview.model_validate(item) for item in payload["reviews"]],
                "traces": [
                    {
                        "analysis_id": item["analysis_id"],
                        "candidate_id": item["candidate_id"],
                        "items": [TraceRecord.model_validate(trace) for trace in item["items"]],
                    }
                    for item in payload["traces"]
                ],
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RepositoryError(f"legacy event store okunamadı: {exc}") from exc

    @staticmethod
    def _model_from_payload(model_type: type[_T], raw_payload: str) -> _T:
        try:
            return model_type.model_validate_json(raw_payload)  # type: ignore[attr-defined,no-any-return]
        except (ValidationError, ValueError) as exc:
            raise RepositoryError(f"event store satırı okunamadı: {exc}") from exc

    def _load_database(self) -> None:
        self._videos = {
            item.video_id: item
            for row in self._connection.execute("SELECT payload FROM videos")
            if (item := self._model_from_payload(VideoMetadata, row["payload"]))
        }
        self._analyses = {
            item.analysis_id: item
            for row in self._connection.execute("SELECT payload FROM analyses")
            if (item := self._model_from_payload(AnalysisRecord, row["payload"]))
        }
        self._candidates = {
            item.candidate_id: item
            for row in self._connection.execute("SELECT payload FROM candidates")
            if (item := self._model_from_payload(CandidateEvent, row["payload"]))
        }
        self._events = {
            item.event_id: item
            for row in self._connection.execute("SELECT payload FROM events")
            if (item := self._model_from_payload(VerifiedEvent, row["payload"]))
        }
        self._event_history = {}
        for row in self._connection.execute(
            "SELECT event_id, payload FROM event_revisions ORDER BY event_id, revision"
        ):
            item = self._model_from_payload(VerifiedEvent, row["payload"])
            self._event_history.setdefault(row["event_id"], []).append(item)
        self._reviews = {
            item.review_id: item
            for row in self._connection.execute("SELECT payload FROM human_reviews")
            if (item := self._model_from_payload(HumanReview, row["payload"]))
        }
        self._development_approvals = {
            item.approval_id: item
            for row in self._connection.execute("SELECT payload FROM development_approvals")
            if (item := self._model_from_payload(DevelopmentApproval, row["payload"]))
        }
        self._rule_proposals = {
            item.proposal_id: item
            for row in self._connection.execute("SELECT payload FROM rule_proposals")
            if (item := self._model_from_payload(RuleProposal, row["payload"]))
        }
        self._incident_media = {
            item.media_id: item
            for row in self._connection.execute("SELECT payload FROM incident_media")
            if (item := self._model_from_payload(IncidentMedia, row["payload"]))
        }
        self._intervention_priorities = {
            item.priority_id: item
            for row in self._connection.execute(
                "SELECT payload FROM intervention_priorities"
            )
            if (item := self._model_from_payload(InterventionPriority, row["payload"]))
        }
        self._training_samples = {
            item.sample_id: item
            for row in self._connection.execute("SELECT payload FROM training_samples")
            if (item := self._model_from_payload(TrainingSample, row["payload"]))
        }
        self._training_jobs = {
            item.job_id: item
            for row in self._connection.execute("SELECT payload FROM training_jobs")
            if (item := self._model_from_payload(TrainingJob, row["payload"]))
        }
        self._model_versions = {
            item.model_version_id: item
            for row in self._connection.execute("SELECT payload FROM model_versions")
            if (item := self._model_from_payload(ModelVersion, row["payload"]))
        }
        self._traces = {}
        for row in self._connection.execute(
            """
            SELECT analysis_id, candidate_id, payload
            FROM decision_traces
            ORDER BY analysis_id, candidate_id, step
            """
        ):
            item = self._model_from_payload(TraceRecord, row["payload"])
            self._traces.setdefault((row["analysis_id"], row["candidate_id"]), []).append(item)

    @staticmethod
    def _json_payload(item: BaseModel) -> str:
        return item.model_dump_json()

    def _write_video(self, item: VideoMetadata) -> None:
        self._connection.execute(
            """
            INSERT INTO videos(video_id, file_hash_sha256, payload) VALUES (?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                file_hash_sha256 = excluded.file_hash_sha256,
                payload = excluded.payload
            """,
            (item.video_id, item.file_hash_sha256, self._json_payload(item)),
        )

    def _write_analysis(self, item: AnalysisRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO analyses(analysis_id, video_id, status, payload) VALUES (?, ?, ?, ?)
            ON CONFLICT(analysis_id) DO UPDATE SET
                status = excluded.status,
                payload = excluded.payload
            """,
            (item.analysis_id, item.video_id, item.status.value, self._json_payload(item)),
        )

    def _write_candidate(self, item: CandidateEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO candidates(candidate_id, analysis_id, video_id, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET payload = excluded.payload
            """,
            (item.candidate_id, item.analysis_id, item.video_id, self._json_payload(item)),
        )

    def _write_event(self, item: VerifiedEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO events(
                event_id, analysis_id, video_id, candidate_id,
                status, revision, created_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                status = excluded.status,
                revision = excluded.revision,
                payload = excluded.payload
            """,
            (
                item.event_id,
                item.analysis_id,
                item.video_id,
                item.candidate_id,
                item.status.value,
                item.revision,
                item.created_at.isoformat(),
                self._json_payload(item),
            ),
        )

    def _write_event_revision(self, item: VerifiedEvent) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO event_revisions(event_id, revision, payload)
            VALUES (?, ?, ?)
            """,
            (item.event_id, item.revision, self._json_payload(item)),
        )

    def _write_review(self, item: HumanReview) -> None:
        self._connection.execute(
            """
            INSERT INTO human_reviews(
                review_id, event_id, decision, revision, reviewer, created_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(review_id) DO UPDATE SET payload = excluded.payload
            """,
            (
                item.review_id,
                item.event_id,
                item.decision.value,
                item.revision,
                item.reviewer,
                item.created_at.isoformat(),
                self._json_payload(item),
            ),
        )

    def _write_development_approval(self, item: DevelopmentApproval) -> None:
        self._connection.execute(
            """
            INSERT INTO development_approvals(
                approval_id, event_id, review_id, status, reviewer, created_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.approval_id,
                item.event_id,
                item.review_id,
                item.status.value,
                item.reviewer,
                item.created_at.isoformat(),
                self._json_payload(item),
            ),
        )

    def _write_rule_proposal(self, item: RuleProposal) -> None:
        self._connection.execute(
            """
            INSERT INTO rule_proposals(
                proposal_id, feed, category, status, expires_at,
                revision, created_at, updated_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proposal_id) DO UPDATE SET
                status = excluded.status,
                expires_at = excluded.expires_at,
                revision = excluded.revision,
                updated_at = excluded.updated_at,
                payload = excluded.payload
            """,
            (
                item.proposal_id,
                item.feed,
                item.category,
                item.status.value,
                item.expires_at.isoformat() if item.expires_at is not None else None,
                item.revision,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
                self._json_payload(item),
            ),
        )

    def _write_incident_media(self, item: IncidentMedia) -> None:
        self._connection.execute(
            """
            INSERT INTO incident_media(
                media_id, event_id, analysis_id, video_id, event_revision,
                created_at, updated_at, revision, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_id) DO UPDATE SET
                event_revision = excluded.event_revision,
                updated_at = excluded.updated_at,
                revision = excluded.revision,
                payload = excluded.payload
            """,
            (
                item.media_id,
                item.event_id,
                item.analysis_id,
                item.video_id,
                item.event_revision,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
                item.revision,
                self._json_payload(item),
            ),
        )

    def _write_intervention_priority(self, item: InterventionPriority) -> None:
        self._connection.execute(
            """
            INSERT INTO intervention_priorities(
                priority_id, event_id, analysis_id, score, band,
                ruleset_version, event_revision, created_at, calculated_at,
                revision, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(priority_id) DO UPDATE SET
                score = excluded.score,
                band = excluded.band,
                ruleset_version = excluded.ruleset_version,
                event_revision = excluded.event_revision,
                calculated_at = excluded.calculated_at,
                revision = excluded.revision,
                payload = excluded.payload
            """,
            (
                item.priority_id,
                item.event_id,
                item.analysis_id,
                item.score,
                item.band.value,
                item.ruleset_version,
                item.event_revision,
                item.created_at.isoformat(),
                item.calculated_at.isoformat(),
                item.revision,
                self._json_payload(item),
            ),
        )

    def _write_training_sample(self, item: TrainingSample) -> None:
        self._connection.execute(
            """
            INSERT INTO training_samples(
                sample_id, event_id, review_id, approval_id,
                dataset_id, dataset_fingerprint, split, status,
                timestamp_seconds, frame_sha256, revision, created_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sample_id) DO UPDATE SET
                status = excluded.status,
                revision = excluded.revision,
                payload = excluded.payload
            """,
            (
                item.sample_id,
                item.event_id,
                item.review_id,
                item.approval_id,
                item.dataset_id,
                item.dataset_fingerprint,
                item.split.value,
                item.status.value,
                item.timestamp_seconds,
                item.frame_sha256,
                item.revision,
                item.created_at.isoformat(),
                self._json_payload(item),
            ),
        )

    def _write_training_job(self, item: TrainingJob) -> None:
        self._connection.execute(
            """
            INSERT INTO training_jobs(
                job_id, dataset_id, dataset_fingerprint, export_fingerprint,
                architecture, status, requested_by, created_at, updated_at,
                revision, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at,
                revision = excluded.revision,
                payload = excluded.payload
            """,
            (
                item.job_id,
                item.dataset_id,
                item.dataset_fingerprint,
                item.export_fingerprint,
                item.architecture.value,
                item.status.value,
                item.requested_by,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
                item.revision,
                self._json_payload(item),
            ),
        )

    def _write_training_job_cas(self, item: TrainingJob) -> None:
        cursor = self._connection.execute(
            """
            UPDATE training_jobs
            SET status = ?, updated_at = ?, revision = ?, payload = ?
            WHERE job_id = ? AND revision = ?
            """,
            (
                item.status.value,
                item.updated_at.isoformat(),
                item.revision,
                self._json_payload(item),
                item.job_id,
                item.revision - 1,
            ),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflictError(
                f"training job başka süreçte değişti: {item.job_id}"
            )

    def _write_model_version(self, item: ModelVersion) -> None:
        self._connection.execute(
            """
            INSERT INTO model_versions(
                model_version_id, training_job_id, architecture,
                checkpoint_sha256, stage, created_at, updated_at, revision, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_version_id) DO UPDATE SET
                stage = excluded.stage,
                updated_at = excluded.updated_at,
                revision = excluded.revision,
                payload = excluded.payload
            """,
            (
                item.model_version_id,
                item.training_job_id,
                item.architecture.value,
                item.checkpoint_sha256,
                item.stage.value,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
                item.revision,
                self._json_payload(item),
            ),
        )

    def _write_trace(self, analysis_id: str, candidate_id: str, item: TraceRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO decision_traces(analysis_id, candidate_id, step, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(analysis_id, candidate_id, step)
            DO UPDATE SET payload = excluded.payload
            """,
            (analysis_id, candidate_id, item.step, self._json_payload(item)),
        )

    def _write_audit(
        self,
        *,
        action: str,
        subject_type: str,
        subject_id: str,
        actor: str | None,
        payload: dict[str, Any],
        occurred_at: datetime | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_log(
                entry_id, action, subject_type, subject_id, actor, occurred_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                action,
                subject_type,
                subject_id,
                actor,
                (occurred_at or datetime.now(UTC)).isoformat(),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def _write_saved_review(self, saved: HumanReview) -> None:
        previous = self._event_history[saved.event_id][-1]
        current = self._events[saved.event_id]
        self._write_event_revision(previous)
        self._write_event(current)
        self._write_review(saved)
        invalidated_samples = [
            sample
            for sample in self._training_samples.values()
            if sample.invalidated_by_review_id == saved.review_id
        ]
        for sample in invalidated_samples:
            self._write_training_sample(sample)
        self._write_audit(
            action="human_review_saved",
            subject_type="event",
            subject_id=saved.event_id,
            actor=saved.reviewer,
            occurred_at=saved.created_at,
            payload={"review_id": saved.review_id, "decision": saved.decision.value},
        )
        for sample in invalidated_samples:
            self._write_audit(
                action="training_sample_invalidated_by_review",
                subject_type="training_sample",
                subject_id=sample.sample_id,
                actor=saved.reviewer,
                occurred_at=saved.created_at,
                payload={"review_id": saved.review_id},
            )

    def _write_saved_development_approval(self, saved: DevelopmentApproval) -> None:
        self._write_development_approval(saved)
        revoked_samples = [
            sample
            for sample in self._training_samples.values()
            if sample.revoked_by_approval_id == saved.approval_id
        ]
        for sample in revoked_samples:
            self._write_training_sample(sample)
        self._write_audit(
            action="development_approval_saved",
            subject_type="event",
            subject_id=saved.event_id,
            actor=saved.reviewer,
            occurred_at=saved.created_at,
            payload={
                "approval_id": saved.approval_id,
                "status": saved.status.value,
                "approved_uses": [item.value for item in saved.approved_uses],
            },
        )
        for sample in revoked_samples:
            self._write_audit(
                action="training_sample_revoked",
                subject_type="training_sample",
                subject_id=sample.sample_id,
                actor=saved.reviewer,
                occurred_at=saved.created_at,
                payload={"revoked_by_approval_id": saved.approval_id},
            )

    def _write_saved_rule_proposal(self, saved: RuleProposal) -> None:
        self._write_rule_proposal(saved)
        created = saved.revision == 1
        payload = (
            {
                "feed": saved.feed,
                "category": saved.category,
                "status": saved.status.value,
                "dismissal_count": saved.dismissal_count,
            }
            if created
            else {
                "feed": saved.feed,
                "category": saved.category,
                "dismissal_count": saved.dismissal_count,
                "auto_applied_count": saved.auto_applied_count,
                "expires_at": (
                    saved.expires_at.isoformat()
                    if saved.expires_at is not None
                    else None
                ),
                "revision": saved.revision,
            }
        )
        self._write_audit(
            action=(
                "rule_proposal_created"
                if created
                else f"rule_proposal_{saved.status.value}"
            ),
            subject_type="rule_proposal",
            subject_id=saved.proposal_id,
            actor=(
                saved.proposed_by
                if created
                else saved.decided_by or saved.proposed_by
            ),
            occurred_at=saved.created_at if created else saved.updated_at,
            payload=payload,
        )

    def _transaction(self, operation: Callable[[], _T], writer: Callable[[_T], None]) -> _T:
        with self._lock:
            try:
                with self._connection:
                    result = operation()
                    writer(result)
                return result
            except sqlite3.Error as exc:
                self._load_database()
                raise RepositoryError(f"event store yazılamadı: {exc}") from exc
            except Exception:
                self._load_database()
                raise

    def create_video(self, metadata: VideoMetadata) -> VideoMetadata:
        if self._batch_mutation:
            return super().create_video(metadata)
        return self._transaction(
            lambda: super(SqliteEventRepository, self).create_video(metadata),
            self._write_video,
        )

    def create_analysis(self, *args: Any, **kwargs: Any) -> AnalysisRecord:
        if self._batch_mutation:
            return super().create_analysis(*args, **kwargs)
        return self._transaction(
            lambda: super(SqliteEventRepository, self).create_analysis(*args, **kwargs),
            self._write_analysis,
        )

    def update_analysis_status(self, *args: Any, **kwargs: Any) -> AnalysisRecord:
        if self._batch_mutation:
            return super().update_analysis_status(*args, **kwargs)
        return self._transaction(
            lambda: super(SqliteEventRepository, self).update_analysis_status(*args, **kwargs),
            self._write_analysis,
        )

    def save_candidate(self, candidate: CandidateEvent) -> CandidateEvent:
        if self._batch_mutation:
            return super().save_candidate(candidate)
        return self._transaction(
            lambda: super(SqliteEventRepository, self).save_candidate(candidate),
            self._write_candidate,
        )

    def save_trace_item(self, *args: Any, **kwargs: Any) -> TraceRecord:
        if self._batch_mutation:
            return super().save_trace_item(*args, **kwargs)

        analysis_id = args[0] if args else kwargs["analysis_id"]
        candidate_id = args[1] if len(args) > 1 else kwargs["candidate_id"]
        return self._transaction(
            lambda: super(SqliteEventRepository, self).save_trace_item(*args, **kwargs),
            lambda item: self._write_trace(analysis_id, candidate_id, item),
        )

    def save_event(self, event: VerifiedEvent) -> VerifiedEvent:
        if self._batch_mutation:
            return super().save_event(event)

        def write(saved: VerifiedEvent) -> None:
            history = self._event_history.get(saved.event_id, [])
            if history:
                self._write_event_revision(history[-1])
            self._write_event(saved)

        return self._transaction(
            lambda: super(SqliteEventRepository, self).save_event(event), write
        )

    def save_review(self, review: HumanReview) -> HumanReview:
        if self._batch_mutation:
            return super().save_review(review)

        def write(saved: HumanReview) -> None:
            self._write_saved_review(saved)

        return self._transaction(
            lambda: super(SqliteEventRepository, self).save_review(review), write
        )

    def save_development_approval(self, approval: DevelopmentApproval) -> DevelopmentApproval:
        def write(saved: DevelopmentApproval) -> None:
            self._write_saved_development_approval(saved)

        return self._transaction(
            lambda: super(SqliteEventRepository, self).save_development_approval(approval),
            write,
        )

    def save_feedback_bundle(self, bundle: FeedbackWriteBundle) -> FeedbackWriteResult:
        def operation() -> FeedbackWriteResult:
            return super(SqliteEventRepository, self).save_feedback_bundle(bundle)

        def write(saved: FeedbackWriteResult) -> None:
            for review in saved.reviews:
                if review.review_id in saved.written_review_ids:
                    self._write_saved_review(review)
            for approval in saved.development_approvals:
                if approval.approval_id in saved.written_approval_ids:
                    self._write_saved_development_approval(approval)
            for proposal in saved.rule_proposals:
                if proposal.proposal_id in saved.written_proposal_ids:
                    self._write_saved_rule_proposal(proposal)

        return self._transaction(operation, write)

    def create_rule_proposal(self, proposal: RuleProposal) -> RuleProposal:
        def write(saved: RuleProposal) -> None:
            self._write_saved_rule_proposal(saved)

        return self._transaction(
            lambda: super(SqliteEventRepository, self).create_rule_proposal(proposal),
            write,
        )

    def update_rule_proposal(self, proposal: RuleProposal) -> RuleProposal:
        def write(saved: RuleProposal) -> None:
            self._write_saved_rule_proposal(saved)

        return self._transaction(
            lambda: super(SqliteEventRepository, self).update_rule_proposal(proposal),
            write,
        )

    def save_incident_media(self, media: IncidentMedia) -> IncidentMedia:
        def write(saved: IncidentMedia) -> None:
            self._write_incident_media(saved)
            self._write_audit(
                action="incident_media_saved",
                subject_type="incident_media",
                subject_id=saved.media_id,
                actor="system",
                occurred_at=saved.updated_at,
                payload={
                    "event_id": saved.event_id,
                    "event_revision": saved.event_revision,
                    "clip_sha256": saved.clip_sha256,
                    "thumbnail_sha256": saved.thumbnail_sha256,
                    "revision": saved.revision,
                },
            )

        return self._transaction(
            lambda: super(SqliteEventRepository, self).save_incident_media(media),
            write,
        )

    def save_intervention_priority(
        self, priority: InterventionPriority
    ) -> InterventionPriority:
        def write(saved: InterventionPriority) -> None:
            self._write_intervention_priority(saved)
            self._write_audit(
                action="intervention_priority_saved",
                subject_type="intervention_priority",
                subject_id=saved.priority_id,
                actor="system",
                occurred_at=saved.calculated_at,
                payload={
                    "event_id": saved.event_id,
                    "score": saved.score,
                    "band": saved.band.value,
                    "ruleset_version": saved.ruleset_version,
                    "revision": saved.revision,
                },
            )

        return self._transaction(
            lambda: super(SqliteEventRepository, self).save_intervention_priority(
                priority
            ),
            write,
        )

    def create_training_samples(self, samples: list[TrainingSample]) -> list[TrainingSample]:
        def write(saved: list[TrainingSample]) -> None:
            for item in saved:
                self._write_training_sample(item)
                self._write_audit(
                    action="training_sample_prepared",
                    subject_type="training_sample",
                    subject_id=item.sample_id,
                    actor=item.prepared_by,
                    occurred_at=item.created_at,
                    payload={
                        "event_id": item.event_id,
                        "approval_id": item.approval_id,
                        "frame_sha256": item.frame_sha256,
                    },
                )

        return self._transaction(
            lambda: super(SqliteEventRepository, self).create_training_samples(samples),
            write,
        )

    def verify_training_sample(self, sample_id: str, review: TrainingFrameReview) -> TrainingSample:
        def write(saved: TrainingSample) -> None:
            self._write_training_sample(saved)
            assert saved.frame_review is not None
            self._write_audit(
                action="training_sample_verified",
                subject_type="training_sample",
                subject_id=saved.sample_id,
                actor=saved.frame_review.reviewer,
                occurred_at=saved.frame_review.reviewed_at,
                payload={
                    "review_result": saved.frame_review.review_result.value,
                    "box_count": len(saved.frame_review.boxes),
                    "revision": saved.revision,
                },
            )

        return self._transaction(
            lambda: super(SqliteEventRepository, self).verify_training_sample(sample_id, review),
            write,
        )

    def create_training_job(self, job: TrainingJob) -> TrainingJob:
        def write(saved: TrainingJob) -> None:
            self._write_training_job(saved)
            self._write_audit(
                action="training_job_queued",
                subject_type="training_job",
                subject_id=saved.job_id,
                actor=saved.requested_by,
                occurred_at=saved.created_at,
                payload={
                    "dataset_fingerprint": saved.dataset_fingerprint,
                    "export_fingerprint": saved.export_fingerprint,
                    "architecture": saved.architecture.value,
                    "max_gpu_minutes": saved.max_gpu_minutes,
                },
            )

        return self._transaction(
            lambda: super(SqliteEventRepository, self).create_training_job(job),
            write,
        )

    def update_training_job(self, job: TrainingJob) -> TrainingJob:
        def write(saved: TrainingJob) -> None:
            self._write_training_job_cas(saved)
            self._write_audit(
                action=f"training_job_{saved.status.value}",
                subject_type="training_job",
                subject_id=saved.job_id,
                actor=saved.requested_by,
                occurred_at=saved.updated_at,
                payload={
                    "revision": saved.revision,
                    "elapsed_seconds": saved.elapsed_seconds,
                    "checkpoint_sha256": saved.checkpoint_sha256,
                    "error_code": saved.error_code,
                },
            )

        return self._transaction(
            lambda: super(SqliteEventRepository, self).update_training_job(job),
            write,
        )

    def create_model_version(self, version: ModelVersion) -> ModelVersion:
        def write(saved: ModelVersion) -> None:
            self._write_model_version(saved)
            self._write_audit(
                action="model_candidate_created",
                subject_type="model_version",
                subject_id=saved.model_version_id,
                actor=None,
                occurred_at=saved.created_at,
                payload={
                    "training_job_id": saved.training_job_id,
                    "checkpoint_sha256": saved.checkpoint_sha256,
                },
            )

        return self._transaction(
            lambda: super(SqliteEventRepository, self).create_model_version(version),
            write,
        )

    def update_model_version(self, version: ModelVersion) -> ModelVersion:
        def write(saved: ModelVersion) -> None:
            self._write_model_version(saved)
            if saved.evaluation is not None:
                self._write_audit(
                    action="model_evaluation_saved",
                    subject_type="model_version",
                    subject_id=saved.model_version_id,
                    actor=saved.evaluation.evaluator,
                    occurred_at=saved.evaluation.measured_at,
                    payload={
                        "evaluation_id": saved.evaluation.evaluation_id,
                        "metrics_fingerprint": saved.evaluation.metrics_fingerprint,
                        "shadow_passed": saved.evaluation.shadow_passed,
                    },
                )
            else:
                assert saved.deployment is not None
                self._write_audit(
                    action="model_deployment_exported",
                    subject_type="model_version",
                    subject_id=saved.model_version_id,
                    actor=None,
                    occurred_at=saved.deployment.exported_at,
                    payload={
                        "onnx_sha256": saved.deployment.onnx_sha256,
                        "artifact_fingerprint": saved.deployment.artifact_fingerprint,
                    },
                )

        return self._transaction(
            lambda: super(SqliteEventRepository, self).update_model_version(version),
            write,
        )

    def switch_champion(
        self,
        champion: ModelVersion,
        previous_champion: ModelVersion | None,
    ) -> ModelVersion:
        def operation() -> ModelVersion:
            return super(SqliteEventRepository, self).switch_champion(champion, previous_champion)

        def write(saved: ModelVersion) -> None:
            if previous_champion is not None:
                self._write_model_version(previous_champion)
            self._write_model_version(saved)
            self._write_audit(
                action="model_champion_switched",
                subject_type="model_version",
                subject_id=saved.model_version_id,
                actor=saved.approved_by,
                occurred_at=saved.promoted_at,
                payload={
                    "previous_model_version_id": (
                        previous_champion.model_version_id
                        if previous_champion is not None
                        else None
                    ),
                    "policy_version": saved.promotion_policy_version,
                    "reason": saved.promotion_reason,
                },
            )

        return self._transaction(operation, write)

    def save_agent_bundle(
        self,
        candidate: CandidateEvent,
        trace_items: list[TraceRecord],
        event: VerifiedEvent,
    ) -> VerifiedEvent:
        def operation() -> VerifiedEvent:
            self._batch_mutation = True
            try:
                return super(SqliteEventRepository, self).save_agent_bundle(
                    candidate, trace_items, event
                )
            finally:
                self._batch_mutation = False

        def write(saved: VerifiedEvent) -> None:
            self._write_candidate(candidate)
            for item in trace_items:
                self._write_trace(event.analysis_id, candidate.candidate_id, item)
            self._write_event(saved)

        return self._transaction(operation, write)
