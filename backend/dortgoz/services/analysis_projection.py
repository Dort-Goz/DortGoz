"""Project runtime incidents into the canonical, persistent event repository."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..domain.candidate import CandidateEvent, CandidateType
from ..domain.event import EventStatus, VerifiedEvent
from ..domain.memory import AnalysisStatus
from ..domain.provenance import AnalysisProvenance, ModelRunRef
from ..domain.taxonomy import VerifiedEventType, canonical_event_type_from_ws_label
from ..events import Event, IncidentUpdate, RunStatus
from ..repositories.protocols import EventRepository

LOGGER = logging.getLogger(__name__)

_RISK_SCORE = {"dusuk": 0.25, "orta": 0.5, "yuksek": 0.75, "kritik": 1.0}


class RuntimeAnalysisProjection:
    """Keep the WS runtime and canonical feedback store on one event identity."""

    def __init__(self, repository: EventRepository, runs_dir: Path) -> None:
        self.repository = repository
        self.runs_dir = runs_dir
        self._run_by_feed: dict[str, str] = {}
        self._incidents: dict[str, dict[str, IncidentUpdate]] = {}

    def observe(self, envelope: Event) -> None:
        """ConnectionManager observer; failures are logged and never break analysis."""

        try:
            payload = envelope.payload
            if isinstance(payload, RunStatus):
                self._observe_run(envelope.feed or "", payload)
            elif isinstance(payload, IncidentUpdate):
                run_id = self._run_by_feed.get(envelope.feed or "")
                if run_id is not None:
                    self._incidents.setdefault(run_id, {})[payload.incident_id] = payload
        except Exception:
            LOGGER.exception("runtime incident projection failed")

    def _observe_run(self, feed: str, status: RunStatus) -> None:
        if status.run_id == "-":
            return
        if status.state == "processing":
            video = self.repository.find_video_by_stored_filename(status.video)
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

    def _persist_incident(self, analysis_id: str, incident: IncidentUpdate) -> None:
        analysis = self.repository.get_analysis(analysis_id)
        if analysis is None:
            return
        event_id = f"{analysis_id}:{incident.incident_id}"
        if self.repository.get_event(event_id) is not None:
            return

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
