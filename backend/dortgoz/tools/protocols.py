from __future__ import annotations

from typing import Protocol

from ..agent.state import EventAgentState
from ..domain.candidate import CandidateEvent
from ..domain.video import VideoMetadata


class ToolExecutionError(RuntimeError):

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VlmSchemaError(ToolExecutionError):

    def __init__(self, message: str, *, code: str = "VLM_SCHEMA_INVALID") -> None:
        super().__init__(code, message)


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


__all__ = ["AgentToolset", "ScreeningTool", "ToolExecutionError", "VlmSchemaError"]
