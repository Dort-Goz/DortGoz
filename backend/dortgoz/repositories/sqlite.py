from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from ..domain.candidate import CandidateEvent
from ..domain.event import VerifiedEvent
from ..domain.memory import AnalysisRecord
from ..domain.provenance import HumanReview, TraceRecord
from ..domain.video import VideoMetadata
from .errors import RepositoryError
from .memory import InMemoryEventRepository

_T = TypeVar("_T")
_SCHEMA_VERSION = 1


class SqliteEventRepository(InMemoryEventRepository):

    def __init__(self, database_path: Path) -> None:
        super().__init__()
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repository_snapshot (
                snapshot_id INTEGER PRIMARY KEY CHECK (snapshot_id = 1),
                schema_version INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._connection.commit()
        self._batch_mutation = False
        self._load_snapshot()

    @property
    def persistence_mode(self) -> str:
        return "sqlite"

    def _load_snapshot(self) -> None:
        row = self._connection.execute(
            "SELECT schema_version, payload FROM repository_snapshot WHERE snapshot_id = 1"
        ).fetchone()
        if row is None:
            return
        schema_version, raw_payload = row
        if schema_version != _SCHEMA_VERSION:
            raise RepositoryError(f"desteklenmeyen event store şeması: {schema_version}")
        try:
            payload = json.loads(raw_payload)
            videos = [VideoMetadata.model_validate(item) for item in payload["videos"]]
            analyses = [AnalysisRecord.model_validate(item) for item in payload["analyses"]]
            candidates = [CandidateEvent.model_validate(item) for item in payload["candidates"]]
            events = [VerifiedEvent.model_validate(item) for item in payload["events"]]
            history = {
                event_id: [VerifiedEvent.model_validate(item) for item in revisions]
                for event_id, revisions in payload["event_history"].items()
            }
            reviews = [HumanReview.model_validate(item) for item in payload["reviews"]]
            traces = payload["traces"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryError(f"event store okunamadı: {exc}") from exc

        self._videos = {item.video_id: item for item in videos}
        self._analyses = {item.analysis_id: item for item in analyses}
        self._candidates = {item.candidate_id: item for item in candidates}
        self._events = {item.event_id: item for item in events}
        self._event_history = history
        self._reviews = {item.review_id: item for item in reviews}
        self._traces = {
            (item["analysis_id"], item["candidate_id"]): [
                TraceRecord.model_validate(trace) for trace in item["items"]
            ]
            for item in traces
        }

    def _payload(self) -> dict[str, Any]:
        return {
            "videos": [item.model_dump(mode="json") for item in self._videos.values()],
            "analyses": [item.model_dump(mode="json") for item in self._analyses.values()],
            "candidates": [item.model_dump(mode="json") for item in self._candidates.values()],
            "events": [item.model_dump(mode="json") for item in self._events.values()],
            "event_history": {
                event_id: [item.model_dump(mode="json") for item in revisions]
                for event_id, revisions in self._event_history.items()
            },
            "reviews": [item.model_dump(mode="json") for item in self._reviews.values()],
            "traces": [
                {
                    "analysis_id": analysis_id,
                    "candidate_id": candidate_id,
                    "items": [item.model_dump(mode="json") for item in items],
                }
                for (analysis_id, candidate_id), items in self._traces.items()
            ],
        }

    def _persist(self) -> None:
        payload = json.dumps(self._payload(), ensure_ascii=False, separators=(",", ":"))
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO repository_snapshot(snapshot_id, schema_version, payload)
                    VALUES (1, ?, ?)
                    ON CONFLICT(snapshot_id) DO UPDATE SET
                        schema_version = excluded.schema_version,
                        payload = excluded.payload
                    """,
                    (_SCHEMA_VERSION, payload),
                )
        except sqlite3.Error as exc:
            raise RepositoryError(f"event store yazılamadı: {exc}") from exc

    def _mutate(self, operation: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        result = operation(*args, **kwargs)
        if not self._batch_mutation:
            self._persist()
        return result

    def create_video(self, metadata: VideoMetadata) -> VideoMetadata:
        return self._mutate(super().create_video, metadata)

    def create_analysis(self, *args: Any, **kwargs: Any) -> AnalysisRecord:
        return self._mutate(super().create_analysis, *args, **kwargs)

    def update_analysis_status(self, *args: Any, **kwargs: Any) -> AnalysisRecord:
        return self._mutate(super().update_analysis_status, *args, **kwargs)

    def save_candidate(self, candidate: CandidateEvent) -> CandidateEvent:
        return self._mutate(super().save_candidate, candidate)

    def save_trace_item(self, *args: Any, **kwargs: Any) -> TraceRecord:
        return self._mutate(super().save_trace_item, *args, **kwargs)

    def save_event(self, event: VerifiedEvent) -> VerifiedEvent:
        return self._mutate(super().save_event, event)

    def save_review(self, review: HumanReview) -> HumanReview:
        return self._mutate(super().save_review, review)

    def save_agent_bundle(
        self,
        candidate: CandidateEvent,
        trace_items: list[TraceRecord],
        event: VerifiedEvent,
    ) -> VerifiedEvent:
        self._batch_mutation = True
        try:
            result = super().save_agent_bundle(candidate, trace_items, event)
        finally:
            self._batch_mutation = False
        self._persist()
        return result
