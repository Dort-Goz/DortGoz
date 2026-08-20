from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from ..domain.candidate import CandidateEvent
from ..domain.event import EventStatus, VerifiedEvent
from ..domain.memory import AnalysisRecord, AnalysisResult, AnalysisStatus
from ..domain.provenance import AnalysisProvenance, HumanReview, ReviewDecision, TraceRecord
from ..domain.video import VideoMetadata
from .errors import (
    RepositoryConflictError,
    RepositoryDuplicateError,
    RepositoryNotFoundError,
)


def _copy(value):
    return deepcopy(value)


class InMemoryEventRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._videos: dict[str, VideoMetadata] = {}
        self._analyses: dict[str, AnalysisRecord] = {}
        self._candidates: dict[str, CandidateEvent] = {}
        self._events: dict[str, VerifiedEvent] = {}
        self._event_history: dict[str, list[VerifiedEvent]] = {}
        self._reviews: dict[str, HumanReview] = {}
        self._traces: dict[tuple[str, str], list[TraceRecord]] = {}

    def create_video(self, metadata: VideoMetadata) -> VideoMetadata:
        with self._lock:
            existing = self._videos.get(metadata.video_id)
            if existing is not None:
                if existing.file_hash_sha256 != metadata.file_hash_sha256:
                    raise RepositoryDuplicateError(
                        f"video_id zaten farklı hash ile kayıtlı: {metadata.video_id}"
                    )
                return _copy(existing)
            self._videos[metadata.video_id] = _copy(metadata)
            return _copy(metadata)

    def get_video(self, video_id: str) -> VideoMetadata | None:
        with self._lock:
            item = self._videos.get(video_id)
            return _copy(item) if item is not None else None

    def get_analysis(self, analysis_id: str) -> AnalysisRecord | None:
        with self._lock:
            item = self._analyses.get(analysis_id)
            return _copy(item) if item is not None else None

    def find_video_by_hash(self, file_hash_sha256: str) -> VideoMetadata | None:
        with self._lock:
            item = next(
                (
                    video
                    for video in self._videos.values()
                    if video.file_hash_sha256 == file_hash_sha256
                ),
                None,
            )
            return _copy(item) if item is not None else None

    def create_analysis(
        self,
        video_id: str,
        provenance: AnalysisProvenance,
        analysis_id: str | None = None,
    ) -> AnalysisRecord:
        with self._lock:
            if video_id not in self._videos:
                raise RepositoryNotFoundError(f"video bulunamadı: {video_id}")
            identifier = analysis_id or str(uuid4())
            if identifier in self._analyses:
                raise RepositoryDuplicateError(f"analysis_id zaten kayıtlı: {identifier}")
            record = AnalysisRecord(
                analysis_id=identifier,
                video_id=video_id,
                provenance=provenance,
            )
            self._analyses[identifier] = record
            return _copy(record)

    def update_analysis_status(
        self,
        analysis_id: str,
        status: str,
        progress: float,
        error: str | None = None,
    ) -> AnalysisRecord:
        with self._lock:
            current = self._analyses.get(analysis_id)
            if current is None:
                raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
            try:
                parsed_status = AnalysisStatus(status)
            except ValueError as exc:
                raise RepositoryConflictError(f"geçersiz analysis status: {status}") from exc
            now = datetime.now(UTC)
            data = current.model_dump()
            data.update(
                status=parsed_status,
                progress=progress,
                error=error,
                started_at=current.started_at
                or (now if parsed_status == AnalysisStatus.RUNNING else None),
                finished_at=(
                    now
                    if parsed_status
                    in {
                        AnalysisStatus.COMPLETED,
                        AnalysisStatus.FAILED,
                        AnalysisStatus.REVIEW_REQUIRED,
                    }
                    else current.finished_at
                ),
            )
            updated = AnalysisRecord.model_validate(data)
            self._analyses[analysis_id] = updated
            return _copy(updated)

    def save_candidate(self, candidate: CandidateEvent) -> CandidateEvent:
        with self._lock:
            if candidate.analysis_id not in self._analyses:
                raise RepositoryNotFoundError(f"analysis bulunamadı: {candidate.analysis_id}")
            if candidate.video_id not in self._videos:
                raise RepositoryNotFoundError(f"video bulunamadı: {candidate.video_id}")
            existing = self._candidates.get(candidate.candidate_id)
            if existing is not None and existing.model_dump() != candidate.model_dump():
                raise RepositoryDuplicateError(
                    f"candidate_id farklı içerikle kayıtlı: {candidate.candidate_id}"
                )
            self._candidates[candidate.candidate_id] = _copy(candidate)
            return _copy(candidate)

    def save_trace_item(
        self, analysis_id: str, candidate_id: str, trace_item: TraceRecord
    ) -> TraceRecord:
        with self._lock:
            if analysis_id not in self._analyses:
                raise RepositoryNotFoundError(f"analysis bulunamadı: {analysis_id}")
            if candidate_id not in self._candidates:
                raise RepositoryNotFoundError(f"candidate bulunamadı: {candidate_id}")
            key = (analysis_id, candidate_id)
            items = self._traces.setdefault(key, [])
            if any(item.step == trace_item.step for item in items):
                raise RepositoryDuplicateError(
                    f"trace step zaten kayıtlı: {analysis_id}/{candidate_id}/{trace_item.step}"
                )
            items.append(_copy(trace_item))
            return _copy(trace_item)

    def save_event(self, event: VerifiedEvent) -> VerifiedEvent:
        with self._lock:
            if event.analysis_id not in self._analyses:
                raise RepositoryNotFoundError(f"analysis bulunamadı: {event.analysis_id}")
            if event.candidate_id not in self._candidates:
                raise RepositoryNotFoundError(f"candidate bulunamadı: {event.candidate_id}")
            parent = self._candidates[event.candidate_id]
            if event.analysis_id != parent.analysis_id or event.video_id != parent.video_id:
                raise RepositoryConflictError("event parent candidate ile eşleşmiyor")
            current = self._events.get(event.event_id)
            if current is not None:
                if event.revision <= current.revision:
                    raise RepositoryConflictError(
                        f"event revision ilerlemiyor: {event.event_id}"
                    )
                self._event_history.setdefault(event.event_id, []).append(_copy(current))
            self._events[event.event_id] = _copy(event)
            return _copy(event)

    def get_event(self, event_id: str) -> VerifiedEvent | None:
        with self._lock:
            event = self._events.get(event_id)
            return _copy(event) if event is not None else None

    def list_events(
        self, analysis_id: str, status: str | None = None
    ) -> list[VerifiedEvent]:
        with self._lock:
            parsed = EventStatus(status) if status is not None else None
            events = [
                event
                for event in self._events.values()
                if event.analysis_id == analysis_id
                and (parsed is None or event.status == parsed)
            ]
            return _copy(sorted(events, key=lambda event: event.created_at))

    def save_review(self, review: HumanReview) -> HumanReview:
        with self._lock:
            event = self._events.get(review.event_id)
            if event is None:
                raise RepositoryNotFoundError(f"event bulunamadı: {review.event_id}")
            if review.review_id in self._reviews:
                raise RepositoryDuplicateError(f"review_id zaten kayıtlı: {review.review_id}")
            next_revision = event.revision + 1
            review_data = review.model_dump()
            review_data["revision"] = next_revision
            stored_review = HumanReview.model_validate(review_data)
            event_data = event.model_dump()
            event_data.update(
                revision=next_revision,
                review=stored_review,
                updated_at=datetime.now(UTC),
            )
            if stored_review.event_type is not None:
                event_data["event_type"] = stored_review.event_type
            for field in ("start_time", "peak_time", "end_time"):
                if getattr(stored_review, field) is not None:
                    event_data[field] = getattr(stored_review, field)
            if stored_review.decision == ReviewDecision.CONFIRM:
                if event.validation is None or not event.validation.permits_confirmation:
                    raise RepositoryConflictError(
                        "human confirm de evidence validation kapısını geçemedi"
                    )
                event_data["status"] = EventStatus.CONFIRMED
            elif stored_review.decision == ReviewDecision.REJECT:
                event_data["status"] = EventStatus.REJECTED
            else:
                event_data["status"] = EventStatus.HUMAN_REVIEW
            updated_event = VerifiedEvent.model_validate(event_data)
            self._event_history.setdefault(event.event_id, []).append(_copy(event))
            self._events[event.event_id] = updated_event
            self._reviews[stored_review.review_id] = stored_review
            return _copy(stored_review)

    def list_event_revisions(self, event_id: str) -> list[VerifiedEvent]:
        with self._lock:
            if event_id not in self._events:
                raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
            history = [*self._event_history.get(event_id, []), self._events[event_id]]
            return _copy(sorted(history, key=lambda event: event.revision))

    def get_analysis_result(self, analysis_id: str) -> AnalysisResult | None:
        with self._lock:
            analysis = self._analyses.get(analysis_id)
            if analysis is None:
                return None
            video = self._videos[analysis.video_id]
            candidates = [
                candidate
                for candidate in self._candidates.values()
                if candidate.analysis_id == analysis_id
            ]
            events = self.list_events(analysis_id)
            started = analysis.started_at
            finished = analysis.finished_at
            processing_seconds = (
                (finished - started).total_seconds()
                if started is not None and finished is not None
                else None
            )
            return AnalysisResult(
                analysis_id=analysis_id,
                video=_copy(video),
                status=analysis.status,
                events=events,
                candidate_count=len(candidates),
                confirmed_count=sum(event.status == EventStatus.CONFIRMED for event in events),
                rejected_count=sum(event.status == EventStatus.REJECTED for event in events),
                human_review_count=sum(
                    event.status in {EventStatus.HUMAN_REVIEW, EventStatus.PROCESSING_FAILED}
                    for event in events
                ),
                started_at=started,
                finished_at=finished,
                processing_seconds=processing_seconds,
                provenance=_copy(analysis.provenance),
            )

    def query_event_memory(self, analysis_id: str, query: str) -> list[VerifiedEvent]:
        terms = [term.casefold() for term in query.split() if term.strip()]
        events = self.list_events(analysis_id)
        if not terms:
            return events
        matches: list[VerifiedEvent] = []
        for event in events:
            haystack = " ".join(
                [
                    event.status.value,
                    event.event_type.value,
                    *event.uncertainties,
                    *(item.claim for item in event.evidence),
                ]
            ).casefold()
            if all(term in haystack for term in terms):
                matches.append(event)
        return matches

    def get_trace(self, analysis_id: str, candidate_id: str) -> list[TraceRecord]:
        with self._lock:
            return _copy(self._traces.get((analysis_id, candidate_id), []))

    def snapshot_metrics(self) -> dict[str, int]:
        with self._lock:
            events = list(self._events.values())
            return {
                "total_videos": len(self._videos),
                "total_analyses": len(self._analyses),
                "total_events": len(events),
                "confirmed_events": sum(event.status == EventStatus.CONFIRMED for event in events),
                "rejected_events": sum(event.status == EventStatus.REJECTED for event in events),
                "human_review_events": sum(
                    event.status
                    in {EventStatus.HUMAN_REVIEW, EventStatus.PROCESSING_FAILED}
                    for event in events
                ),
            }

    def save_agent_bundle(
        self,
        candidate: CandidateEvent,
        trace_items: list[TraceRecord],
        event: VerifiedEvent,
    ) -> VerifiedEvent:

        with self._lock:
            snapshot = (
                deepcopy(self._candidates),
                deepcopy(self._events),
                deepcopy(self._event_history),
                deepcopy(self._traces),
            )
            try:
                self.save_candidate(candidate)
                for item in trace_items:
                    self.save_trace_item(event.analysis_id, candidate.candidate_id, item)
                return self.save_event(event)
            except Exception:
                (
                    self._candidates,
                    self._events,
                    self._event_history,
                    self._traces,
                ) = snapshot
                raise
