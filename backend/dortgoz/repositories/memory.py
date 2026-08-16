"""Thread-safe, process içi event memory adapter'ı.

Bu adapter Görev 05'in ilk kalıcı sınırıdır. Süreç yeniden başlatılınca veri
silinir; API/SQLite/PostgreSQL adapter'ları aynı protocol'e sonradan bağlanır.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from ..domain.candidate import CandidateEvent
from ..domain.event import EventStatus, VerifiedEvent
from ..domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
)
from ..domain.memory import AnalysisRecord, AnalysisResult, AnalysisStatus
from ..domain.provenance import AnalysisProvenance, HumanReview, ReviewDecision, TraceRecord
from ..domain.training import (
    TrainingFrameReview,
    TrainingSample,
    TrainingSampleStatus,
)
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
        self._development_approvals: dict[str, DevelopmentApproval] = {}
        self._training_samples: dict[str, TrainingSample] = {}
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
            for sample_id, sample in list(self._training_samples.items()):
                if (
                    sample.event_id == stored_review.event_id
                    and sample.status != TrainingSampleStatus.REVOKED
                ):
                    self._training_samples[sample_id] = TrainingSample.model_validate(
                        {
                            **sample.model_dump(),
                            "status": TrainingSampleStatus.REVOKED,
                            "invalidated_by_review_id": stored_review.review_id,
                            "updated_at": max(sample.updated_at, stored_review.created_at),
                            "revision": sample.revision + 1,
                        }
                    )
            return _copy(stored_review)

    def list_reviews(self, event_id: str) -> list[HumanReview]:
        with self._lock:
            if event_id not in self._events:
                raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
            reviews = [
                review for review in self._reviews.values() if review.event_id == event_id
            ]
            return _copy(sorted(reviews, key=lambda review: (review.revision, review.created_at)))

    def save_development_approval(
        self, approval: DevelopmentApproval
    ) -> DevelopmentApproval:
        with self._lock:
            if approval.event_id not in self._events:
                raise RepositoryNotFoundError(f"event bulunamadı: {approval.event_id}")
            review = self._reviews.get(approval.review_id)
            if review is None:
                raise RepositoryNotFoundError(f"review bulunamadı: {approval.review_id}")
            if review.event_id != approval.event_id:
                raise RepositoryConflictError("development approval review ile eşleşmiyor")
            if approval.approval_id in self._development_approvals:
                raise RepositoryDuplicateError(
                    f"approval_id zaten kayıtlı: {approval.approval_id}"
                )

            history = sorted(
                (
                    item
                    for item in self._development_approvals.values()
                    if item.event_id == approval.event_id
                ),
                key=lambda item: (item.created_at, item.approval_id),
            )
            latest = history[-1] if history else None
            if latest is None and approval.supersedes_approval_id is not None:
                raise RepositoryConflictError("supersedes approval bulunamadı")
            if latest is not None and approval.supersedes_approval_id != latest.approval_id:
                raise RepositoryConflictError(
                    "yeni development decision son approval kaydını supersede etmelidir"
                )
            if (
                approval.status == DevelopmentApprovalStatus.REVOKED
                and latest is not None
                and latest.status != DevelopmentApprovalStatus.APPROVED
            ):
                raise RepositoryConflictError("yalnız approved decision revoke edilebilir")

            self._development_approvals[approval.approval_id] = _copy(approval)
            if approval.supersedes_approval_id is not None:
                for sample_id, sample in list(self._training_samples.items()):
                    if (
                        sample.approval_id == approval.supersedes_approval_id
                        and sample.status != TrainingSampleStatus.REVOKED
                    ):
                        self._training_samples[sample_id] = TrainingSample.model_validate(
                            {
                                **sample.model_dump(),
                                "status": TrainingSampleStatus.REVOKED,
                                "revoked_by_approval_id": approval.approval_id,
                                "updated_at": max(sample.updated_at, approval.created_at),
                                "revision": sample.revision + 1,
                            }
                        )
            return _copy(approval)

    def list_development_approvals(self, event_id: str) -> list[DevelopmentApproval]:
        with self._lock:
            if event_id not in self._events:
                raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
            approvals = [
                item
                for item in self._development_approvals.values()
                if item.event_id == event_id
            ]
            return _copy(
                sorted(approvals, key=lambda item: (item.created_at, item.approval_id))
            )

    def create_training_samples(
        self, samples: list[TrainingSample]
    ) -> list[TrainingSample]:
        with self._lock:
            if not samples:
                raise RepositoryConflictError("training sample listesi boş olamaz")
            if len({sample.sample_id for sample in samples}) != len(samples):
                raise RepositoryDuplicateError("training sample batch kimliği tekrar ediyor")
            snapshot = deepcopy(self._training_samples)
            try:
                stored: list[TrainingSample] = []
                for sample in samples:
                    event = self._events.get(sample.event_id)
                    if event is None:
                        raise RepositoryNotFoundError(f"event bulunamadı: {sample.event_id}")
                    review = self._reviews.get(sample.review_id)
                    if review is None:
                        raise RepositoryNotFoundError(f"review bulunamadı: {sample.review_id}")
                    approval = self._development_approvals.get(sample.approval_id)
                    if approval is None:
                        raise RepositoryNotFoundError(
                            f"development approval bulunamadı: {sample.approval_id}"
                        )
                    if (
                        sample.video_id != event.video_id
                        or sample.event_revision != event.revision
                        or review.event_id != event.event_id
                        or approval.event_id != event.event_id
                        or approval.review_id != review.review_id
                    ):
                        raise RepositoryConflictError(
                            "training sample event, review veya approval ile eşleşmiyor"
                        )
                    if (
                        approval.status != DevelopmentApprovalStatus.APPROVED
                        or DevelopmentUse.D_FINE_TRAINING not in approval.approved_uses
                    ):
                        raise RepositoryConflictError(
                            "training sample için etkin D-FINE onayı zorunludur"
                        )
                    if sample.status != TrainingSampleStatus.PENDING_REVIEW or sample.revision != 1:
                        raise RepositoryConflictError(
                            "yeni training sample pending_review ve revision 1 olmalıdır"
                        )
                    existing = self._training_samples.get(sample.sample_id)
                    if existing is not None:
                        if existing.model_dump() != sample.model_dump():
                            raise RepositoryDuplicateError(
                                f"training sample farklı içerikle kayıtlı: {sample.sample_id}"
                            )
                        stored.append(existing)
                        continue
                    self._training_samples[sample.sample_id] = _copy(sample)
                    stored.append(sample)
                return _copy(stored)
            except Exception:
                self._training_samples = snapshot
                raise

    def get_training_sample(self, sample_id: str) -> TrainingSample | None:
        with self._lock:
            sample = self._training_samples.get(sample_id)
            return _copy(sample) if sample is not None else None

    def list_training_samples(self, event_id: str | None = None) -> list[TrainingSample]:
        with self._lock:
            samples = [
                sample
                for sample in self._training_samples.values()
                if event_id is None or sample.event_id == event_id
            ]
            return _copy(
                sorted(samples, key=lambda item: (item.created_at, item.sample_id))
            )

    def verify_training_sample(
        self, sample_id: str, review: TrainingFrameReview
    ) -> TrainingSample:
        with self._lock:
            current = self._training_samples.get(sample_id)
            if current is None:
                raise RepositoryNotFoundError(f"training sample bulunamadı: {sample_id}")
            if current.status != TrainingSampleStatus.PENDING_REVIEW:
                raise RepositoryConflictError(
                    f"training sample inceleme beklemiyor: {sample_id}"
                )
            updated = current.model_copy(
                update={
                    "status": TrainingSampleStatus.VERIFIED,
                    "frame_review": review,
                    "updated_at": datetime.now(UTC),
                    "revision": current.revision + 1,
                }
            )
            updated = TrainingSample.model_validate(updated.model_dump())
            self._training_samples[sample_id] = updated
            return _copy(updated)

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
        """Candidate + trace + event'i tek lock altında atomik kaydeder."""

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
