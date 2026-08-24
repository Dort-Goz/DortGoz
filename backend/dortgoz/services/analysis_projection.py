

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from ..domain.candidate import CandidateEvent, CandidateType
from ..domain.event import EventStatus, VerifiedEvent
from ..domain.memory import AnalysisStatus
from ..domain.provenance import AnalysisProvenance, ModelRunRef
from ..domain.taxonomy import VerifiedEventType, canonical_event_type_from_ws_label
from ..domain.video import VideoMetadata, VideoProbe
from ..events import Event, IncidentUpdate, RunStatus
from ..infrastructure.ffmpeg import probe_video
from ..repositories.protocols import EventRepository

LOGGER = logging.getLogger(__name__)
ProbeFunction = Callable[[Path], Awaitable[VideoProbe]]

_RISK_SCORE = {"dusuk": 0.25, "orta": 0.5, "yuksek": 0.75, "kritik": 1.0}


class RuntimeAnalysisProjection:


    def __init__(
        self,
        repository: EventRepository,
        runs_dir: Path,
        *,
        allow_virtual_sources: bool = False,
        source_probe: ProbeFunction = probe_video,
    ) -> None:
        self.repository = repository
        self.runs_dir = runs_dir
        self.allow_virtual_sources = allow_virtual_sources
        self.source_probe = source_probe
        self._run_by_feed: dict[str, str] = {}
        self._video_by_run: dict[str, str] = {}
        self._incidents: dict[str, dict[str, IncidentUpdate]] = {}
        self._event_by_incident: dict[tuple[str, str], str] = {}

    def observe(self, envelope: Event) -> None:


        try:
            payload = envelope.payload
            if isinstance(payload, RunStatus):
                self._observe_run(envelope.feed or "", payload)
            elif isinstance(payload, IncidentUpdate):
                feed = envelope.feed or ""
                run_id = self._run_by_feed.get(feed)
                if run_id is not None:
                    self._incidents.setdefault(run_id, {})[payload.incident_id] = payload
                    event_id = self._persist_incident(run_id, payload)
                    if event_id is not None:
                        self._event_by_incident[(feed, payload.incident_id)] = event_id
        except Exception:
            LOGGER.exception("runtime incident projection failed")

    def _observe_run(self, feed: str, status: RunStatus) -> None:
        if status.run_id == "-":
            return
        if status.state == "processing":
            video_id = self._video_by_run.get(status.run_id)
            video = self.repository.get_video(video_id) if video_id is not None else None
            if video is None:
                video = self.repository.find_video_by_stored_filename(status.video)
            if video is None and self.allow_virtual_sources:
                video = self._virtual_video(status.run_id)
                self.repository.create_video(video)
            if video is None:
                return
            if self.repository.get_analysis(status.run_id) is None:
                self.repository.create_analysis(
                    video.video_id,
                    self._provenance(status.run_id),
                    analysis_id=status.run_id,
                )
            self.repository.update_analysis_status(status.run_id, AnalysisStatus.RUNNING.value, 0.0)
            self._run_by_feed[feed] = status.run_id
            self._incidents.setdefault(status.run_id, {})
            return

        run_id = self._run_by_feed.get(feed)
        if run_id != status.run_id or self.repository.get_analysis(run_id) is None:
            return
        if status.state == "done":
            incidents = self._incidents.get(run_id, {})
            for incident in incidents.values():
                self._persist_incident(run_id, incident)
            final = AnalysisStatus.REVIEW_REQUIRED if incidents else AnalysisStatus.COMPLETED
            self.repository.update_analysis_status(run_id, final.value, 1.0)
        elif status.state == "error":
            self.repository.update_analysis_status(
                run_id, AnalysisStatus.FAILED.value, 1.0, error=status.detail
            )
        elif status.state == "idle":
            self.repository.update_analysis_status(
                run_id, AnalysisStatus.FAILED.value, 1.0, error="Operatör analizi durdurdu."
            )
        else:
            return
        self._run_by_feed.pop(feed, None)
        self._incidents.pop(run_id, None)
        self._video_by_run.pop(run_id, None)

    async def register_runtime_source(
        self, run_id: str, media_path: str, source: Path
    ) -> VideoMetadata:


        resolved = source.resolve()
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError("runtime video kaynağı güvenli bir dosya değil")
        digest = await asyncio.to_thread(self._file_hash, resolved)
        existing = self.repository.find_video_by_hash(digest)
        if existing is not None:
            self._video_by_run[run_id] = existing.video_id
            return existing
        probed = await self.source_probe(resolved)
        video_id = str(uuid5(NAMESPACE_URL, f"dortgoz-runtime:{digest}"))
        metadata = VideoMetadata(
            video_id=video_id,
            original_filename=resolved.name,
            stored_filename=f"{video_id}{resolved.suffix.lower()}",
            media_path=media_path,
            file_size_bytes=resolved.stat().st_size,
            file_hash_sha256=digest,
            warnings=["RUNTIME_SOURCE_REFERENCE", "MEDIA_MAY_BE_PRUNED"],
            **probed.model_dump(),
        )
        stored = self.repository.create_video(metadata)
        self._video_by_run[run_id] = stored.video_id
        return stored

    def event_id_for(self, feed: str, incident_id: str) -> str | None:


        return self._event_by_incident.get((feed, incident_id))

    def _persist_incident(self, analysis_id: str, incident: IncidentUpdate) -> str | None:
        analysis = self.repository.get_analysis(analysis_id)
        if analysis is None:
            return None
        event_id = f"{analysis_id}:{incident.incident_id}"
        current = self.repository.get_event(event_id)

        if current is not None:


            if current.review is not None:
                return event_id
            start = (
                incident.olay_baslangic
                if incident.olay_baslangic is not None
                else current.start_time if current.start_time is not None else incident.t
            )
            raw_end = (
                incident.olay_bitis
                if incident.olay_bitis is not None
                else max(current.end_time or 0.0, incident.t)
            )
            end = max(start, raw_end, 0.001)
            peak = min(max(incident.t, start), end)
            updated = current.model_copy(
                update={
                    "event_type": VerifiedEventType(
                        canonical_event_type_from_ws_label(incident.anomaly_type).value
                    ),
                    "start_time": start,
                    "peak_time": peak,
                    "end_time": end,
                    "uncertainties": (
                        [incident.review_reason] if incident.needs_review else []
                    ),
                    "legacy_event_type": incident.anomaly_type,
                    "during": incident.detail or incident.title,
                    "updated_at": datetime.now(UTC),
                    "revision": current.revision + 1,
                }
            )
            self.repository.save_event(updated)
            return event_id

        start = incident.olay_baslangic if incident.olay_baslangic is not None else incident.t
        raw_end = incident.olay_bitis if incident.olay_bitis is not None else incident.t
        end = max(start, raw_end, 0.001)
        peak = min(max(incident.t, start), end)
        score = _RISK_SCORE[incident.risk]
        candidate_id = f"{event_id}:candidate"
        self.repository.save_candidate(
            CandidateEvent(
                candidate_id=candidate_id,
                analysis_id=analysis_id,
                video_id=analysis.video_id,
                start_time=start,
                peak_time=peak,
                end_time=end,
                candidate_type=CandidateType.UNKNOWN_ANOMALY,
                peak_score=score,
                anomaly_score=score,
                trigger_signals=[
                    "runtime_incident",
                    f"event_type:{incident.anomaly_type}",
                    f"risk:{incident.risk}",
                ],
                screening_model_id="runtime-incident-projection",
                threshold_version="runtime-v1",
            )
        )
        canonical = canonical_event_type_from_ws_label(incident.anomaly_type)
        self.repository.save_event(
            VerifiedEvent(
                event_id=event_id,
                analysis_id=analysis_id,
                video_id=analysis.video_id,
                candidate_id=candidate_id,
                status=EventStatus.HUMAN_REVIEW,
                event_type=VerifiedEventType(canonical.value),
                start_time=start,
                peak_time=peak,
                end_time=end,
                uncertainties=[incident.review_reason] if incident.needs_review else [],
                legacy_event_type=incident.anomaly_type,
                during=incident.detail or incident.title,
            )
        )
        return event_id

    def _provenance(self, analysis_id: str) -> AnalysisProvenance:
        metadata: dict = {}
        path = self.runs_dir / f"{analysis_id}.meta.json"
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        model = str(metadata.get("model") or "runtime-model")
        mode = str(metadata.get("mode") or "dengeli")
        return AnalysisProvenance(
            contract_version="1.0.0",
            config_version=f"runtime-{mode}",
            code_revision="runtime-projection-v1",
            model_runs=[
                ModelRunRef(
                    model_id=model,
                    role="vlm",
                    config_version=f"runtime-{mode}",
                    code_revision="runtime-projection-v1",
                )
            ],
        )

    @staticmethod
    def _virtual_video(run_id: str) -> VideoMetadata:


        video_id = str(uuid5(NAMESPACE_URL, f"dortgoz-mock:{run_id}"))
        return VideoMetadata(
            video_id=video_id,
            original_filename=f"{run_id}.mp4",
            stored_filename=f"{video_id}.mp4",
            media_path=f"mock/{video_id}.mp4",
            file_size_bytes=1,
            file_hash_sha256=hashlib.sha256(f"mock:{run_id}".encode()).hexdigest(),
            container="mp4",
            codec="mock",
            width=1,
            height=1,
            fps=1,
            duration_seconds=1,
            has_audio=False,
            time_base="1/1",
            warnings=["MOCK_VIRTUAL_SOURCE", "NOT_TRAINING_ELIGIBLE"],
        )

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
