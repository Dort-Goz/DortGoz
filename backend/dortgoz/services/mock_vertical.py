from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..agent.orchestrator import EventOrchestrator
from ..agent.policy import RoutingConfig
from ..agent.state import EventAgentState
from ..domain.video import VideoMetadata
from ..tools.mock_agent import MockAgentTools
from ..tools.mock_screening import MockScreeningTool
from ..tools.protocols import AgentToolset, ScreeningTool


class MockVerticalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    candidates: list[EventAgentState]
    confirmed_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    human_review_count: int = Field(ge=0)

    @model_validator(mode="after")
    def outcomes_are_consistent(self) -> MockVerticalResult:
        if any(not state.completed for state in self.candidates):
            raise ValueError("tüm candidate akışları terminal olmalıdır")
        counts = (
            sum(state.confirmed for state in self.candidates),
            sum(state.rejected for state in self.candidates),
            sum(state.human_review_required for state in self.candidates),
        )
        if counts != (
            self.confirmed_count,
            self.rejected_count,
            self.human_review_count,
        ):
            raise ValueError("sonuç sayaçları candidate durumlarıyla eşleşmiyor")
        return self


class MockVerticalAnalysisService:
    def __init__(
        self,
        screening: ScreeningTool | None = None,
        tools: AgentToolset | None = None,
        config: RoutingConfig | None = None,
    ) -> None:
        self.screening = screening or MockScreeningTool()
        self.tools = tools or MockAgentTools()
        self.config = config or RoutingConfig()

    async def analyze(
        self, metadata: VideoMetadata, analysis_id: str | None = None
    ) -> MockVerticalResult:
        analysis_id = analysis_id or str(uuid4())
        candidates = await self.screening.screen(metadata, analysis_id)
        orchestrator = EventOrchestrator(self.tools, self.config)
        outcomes: list[EventAgentState] = []
        for candidate in candidates:
            initial = EventAgentState(
                analysis_id=analysis_id,
                video_id=metadata.video_id,
                candidate_id=candidate.candidate_id,
                candidate=candidate,
                video_processable=metadata.processable,
                video_duration=metadata.duration_seconds,
                image_quality=candidate.image_quality,
            )
            outcomes.append(await orchestrator.run(initial))

        return MockVerticalResult(
            analysis_id=analysis_id,
            video_id=metadata.video_id,
            candidates=outcomes,
            confirmed_count=sum(state.confirmed for state in outcomes),
            rejected_count=sum(state.rejected for state in outcomes),
            human_review_count=sum(state.human_review_required for state in outcomes),
        )
