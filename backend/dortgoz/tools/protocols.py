"""Mock ve gerçek implementasyonların paylaştığı araç sınırları."""

from __future__ import annotations

from typing import Protocol

from ..agent.state import EventAgentState
from ..domain.candidate import CandidateEvent
from ..domain.video import VideoMetadata


class ScreeningTool(Protocol):
    async def screen(
        self, metadata: VideoMetadata, analysis_id: str
    ) -> list[CandidateEvent]: ...


class AgentToolset(Protocol):
    async def run_cv_only(self, state: EventAgentState) -> EventAgentState: ...

    async def run_dense_analysis(self, state: EventAgentState) -> EventAgentState: ...

    async def expand_context(self, state: EventAgentState) -> EventAgentState: ...

    async def run_vlm(
        self, state: EventAgentState, *, strict_schema: bool
    ) -> EventAgentState: ...

    async def validate_evidence(self, state: EventAgentState) -> EventAgentState: ...
