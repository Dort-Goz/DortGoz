from __future__ import annotations

from hashlib import sha256

from ..agent.actions import AgentAction
from ..agent.state import EventAgentState
from ..domain.context import (
    ContextClip,
    DenseAnalysisResult,
    KeyframeRef,
    SignalObservation,
)
from ..domain.evidence import EvidenceClaim, VerifiedEventType, VLMResult, VLMStatus
from ..services.evidence_validator import validate_evidence


class MockToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _replace(state: EventAgentState, **updates: object) -> EventAgentState:
    data = state.model_dump()
    data.update(updates)
    return EventAgentState.model_validate(data)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _frame(
    state: EventAgentState,
    label: str,
    timestamp: float,
    quality: float,
) -> KeyframeRef:
    frame_id = f"{state.candidate_id}-{label}"
    return KeyframeRef(
        frame_id=frame_id,
        timestamp=timestamp,
        frame_path=f"runs/{state.analysis_id}/{state.candidate_id}/{label}.jpg",
        hash_sha256=_digest(frame_id),
        selection_reason=label,
        quality_score=quality,
    )


class MockAgentTools:

    def __init__(self, fail_on: set[AgentAction] | None = None) -> None:
        self.fail_on = fail_on or set()

    def _maybe_fail(self, action: AgentAction) -> None:
        if action in self.fail_on:
            raise MockToolError("MOCK_TOOL_FAILURE", f"{action} mock hatası")

    async def run_cv_only(self, state: EventAgentState) -> EventAgentState:
        self._maybe_fail(AgentAction.RUN_CV_ONLY)
        peak = _frame(state, "technical-peak", state.candidate.peak_time, 0.94)
        return _replace(
            state,
            cv_status=VLMStatus.CONFIRMED,
            cv_event_type=VerifiedEventType.CAMERA_FREEZE,
            cv_confidence=0.98,
            cv_evidence=[
                EvidenceClaim(
                    timestamp=peak.timestamp,
                    frame_id=peak.frame_id,
                    claim="Ardışık görüntülerde sahne içeriği değişmeden kalıyor.",
                )
            ],
            keyframes=[peak],
            validation=None,
        )

    async def run_dense_analysis(self, state: EventAgentState) -> EventAgentState:
        self._maybe_fail(AgentAction.RUN_DENSE_ANALYSIS)
        dense = DenseAnalysisResult(
            candidate_id=state.candidate_id,
            motion_signals=[
                SignalObservation(
                    signal="occluded_motion", timestamp=state.candidate.peak_time, confidence=0.57
                )
            ],
            cv_confidence=0.57,
            image_quality=0.64,
            warnings=["Kişiler kısmen örtüşüyor; kimlik çıkarımı yapılmadı."],
            tool_version="mock-dense-v1",
        )
        return _replace(state, dense_result=dense, image_quality=dense.image_quality)

    async def expand_context(self, state: EventAgentState) -> EventAgentState:
        self._maybe_fail(AgentAction.EXPAND_CONTEXT)
        clip_start = max(0.0, state.candidate.start_time - 2.0)
        clip_end = min(state.video_duration, state.candidate.end_time + 2.0)
        clip_key = f"{state.candidate_id}:{clip_start:.3f}:{clip_end:.3f}"
        clip = ContextClip(
            candidate_id=state.candidate_id,
            clip_start=clip_start,
            clip_end=clip_end,
            clip_path=f"runs/{state.analysis_id}/{state.candidate_id}/context.mp4",
            frame_count=max(1, round((clip_end - clip_start) * 2)),
            fps=2.0,
            hash_sha256=_digest(clip_key),
            expanded=True,
        )
        frames = [
            _frame(state, "before", clip_start, state.image_quality),
            _frame(state, "peak", state.candidate.peak_time, state.image_quality),
            _frame(state, "after", clip_end, state.image_quality),
        ]
        return _replace(state, context_clip=clip, keyframes=frames)

    async def run_vlm(
        self, state: EventAgentState, *, strict_schema: bool
    ) -> EventAgentState:
        action = AgentAction.RETRY_VLM_STRICT if strict_schema else AgentAction.RUN_VLM
        self._maybe_fail(action)
        attempt = state.vlm_attempts + 1
        prompt_version = "mock-vlm-strict-v1" if strict_schema else "mock-vlm-v1"
        if state.candidate_id.endswith("-normal"):
            result = VLMResult(
                candidate_id=state.candidate_id,
                event_type=VerifiedEventType.NORMAL_INTERACTION,
                status=VLMStatus.REJECTED,
                confidence=0.94,
                uncertainties=[],
                model_id="mock-local-vlm-v1",
                prompt_version=prompt_version,
                attempt=attempt,
                raw_response_hash=_digest(f"{state.candidate_id}:rejected:{attempt}"),
            )
        elif state.candidate_id.endswith("-ambiguous"):
            result = VLMResult(
                candidate_id=state.candidate_id,
                event_type=VerifiedEventType.UNCERTAIN,
                status=VLMStatus.UNCERTAIN,
                confidence=0.48 if strict_schema else 0.42,
                uncertainties=["Örtüşme nedeniyle fiziksel temasın niteliği seçilemiyor."],
                model_id="mock-local-vlm-v1",
                prompt_version=prompt_version,
                attempt=attempt,
                raw_response_hash=_digest(f"{state.candidate_id}:uncertain:{attempt}"),
            )
        else:
            peak = _frame(state, "vlm-peak", state.candidate.peak_time, state.image_quality)
            result = VLMResult(
                candidate_id=state.candidate_id,
                event_type=VerifiedEventType.UNKNOWN_ANOMALY,
                status=VLMStatus.CONFIRMED,
                confidence=0.86,
                start_time=state.candidate.start_time,
                peak_time=state.candidate.peak_time,
                end_time=state.candidate.end_time,
                evidence=[
                    EvidenceClaim(
                        timestamp=peak.timestamp,
                        frame_id=peak.frame_id,
                        claim="Tepe anında olağan dışı hareket örüntüsü gözleniyor.",
                    )
                ],
                model_id="mock-local-vlm-v1",
                prompt_version=prompt_version,
                attempt=attempt,
                raw_response_hash=_digest(f"{state.candidate_id}:confirmed:{attempt}"),
            )
            return _replace(state, vlm_result=result, keyframes=[*state.keyframes, peak])
        return _replace(state, vlm_result=result)

    async def validate_evidence(self, state: EventAgentState) -> EventAgentState:
        self._maybe_fail(AgentAction.VALIDATE_EVIDENCE)
        return _replace(state, validation=validate_evidence(state))
