"""Görev 04 mock araç/orchestrator dikey entegrasyon testleri."""

from __future__ import annotations

from dortgoz.agent.actions import AgentAction
from dortgoz.agent.orchestrator import EventOrchestrator
from dortgoz.agent.state import EventAgentState
from dortgoz.domain.video import VideoMetadata
from dortgoz.services.mock_vertical import MockVerticalAnalysisService
from dortgoz.tools.mock_agent import MockAgentTools
from dortgoz.tools.mock_screening import MockScreeningTool


def metadata() -> VideoMetadata:
    return VideoMetadata(
        video_id="00000000-0000-0000-0000-000000000001",
        original_filename="fixture.mp4",
        stored_filename="00000000-0000-0000-0000-000000000001.mp4",
        media_path="00000000-0000-0000-0000-000000000001.mp4",
        file_size_bytes=1024,
        file_hash_sha256="a" * 64,
        container="mov",
        codec="h264",
        width=640,
        height=480,
        fps=25,
        duration_seconds=90,
        has_audio=False,
        time_base="1/12800",
    )


async def test_mock_vertical_produces_three_distinct_terminal_routes() -> None:
    result = await MockVerticalAnalysisService().analyze(metadata())

    assert len(result.candidates) == 3
    assert (result.confirmed_count, result.rejected_count, result.human_review_count) == (
        1,
        1,
        1,
    )
    all_actions = {trace.action for state in result.candidates for trace in state.decision_trace}
    assert {
        AgentAction.RUN_CV_ONLY,
        AgentAction.RUN_VLM,
        AgentAction.RUN_DENSE_ANALYSIS,
        AgentAction.EXPAND_CONTEXT,
        AgentAction.RETRY_VLM_STRICT,
        AgentAction.VALIDATE_EVIDENCE,
    } <= all_actions
    confirmed = next(state for state in result.candidates if state.confirmed)
    assert confirmed.validation is not None
    assert confirmed.validation.permits_confirmation
    assert all(item.success is True for item in confirmed.decision_trace)
    assert all(item.reason and item.policy_rule_id for item in confirmed.decision_trace)


async def test_tool_failure_is_traced_and_routes_to_human_review() -> None:
    video = metadata()
    analysis_id = "analysis-failure"
    candidate = (await MockScreeningTool().screen(video, analysis_id))[0]
    state = EventAgentState(
        analysis_id=analysis_id,
        video_id=video.video_id,
        candidate_id=candidate.candidate_id,
        candidate=candidate,
        video_duration=video.duration_seconds,
        image_quality=candidate.image_quality,
    )
    orchestrator = EventOrchestrator(
        MockAgentTools(fail_on={AgentAction.RUN_CV_ONLY})
    )

    result = await orchestrator.run(state)

    assert result.completed and result.human_review_required
    assert result.processing_error is not None
    assert len(result.decision_trace) == 1
    assert result.decision_trace[0].success is False
    assert result.decision_trace[0].error_code == "MOCK_TOOL_FAILURE"
