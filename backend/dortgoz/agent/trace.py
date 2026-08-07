"""Deterministik policy kararı ve denetlenebilir çalışma izi."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from .actions import AgentAction


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: AgentAction
    reason: str = Field(min_length=1)
    priority: int = Field(ge=1, le=10)
    policy_rule_id: str = Field(min_length=1)
    expected_tool: str | None = None


class DecisionTraceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: int = Field(ge=1, le=14)
    action: AgentAction
    reason: str = Field(min_length=1)
    policy_rule_id: str = Field(min_length=1)
    tool_name: str | None = None
    input_ref: str | None = None
    output_ref: str | None = None
    success: bool | None = None
    error_code: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    policy_version: str = Field(min_length=1)
