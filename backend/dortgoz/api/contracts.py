"""REST request/response sözleşmeleri.

Bu modeller, UI ile repository arasındaki sınırı açık tutar. Domain modelleri
doğrudan HTTP detaylarıyla kirletilmez; response modelleri domain nesnelerini
JSON'a güvenli biçimde taşır.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.event import VerifiedEvent
from ..domain.memory import AnalysisResult
from ..domain.provenance import ProcedureSource, TraceRecord
from ..services.analysis_job import AnalysisJobStatus


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(default="mock", min_length=1, deprecated=True)
    config_version: str = Field(default="task-06-v1", min_length=1, deprecated=True)
    feed: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=500)
    system_prompt: str = Field(default="", max_length=20_000)
    task_prompt: str = Field(default="", max_length=20_000)
    # Çalışma kipi: "" | dengeli | temkinli | genis (bkz. runner._mode_flags)
    mode: Literal["", "dengeli", "temkinli", "genis"] = ""


class AnalysisAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    status: AnalysisJobStatus = AnalysisJobStatus.QUEUED
    status_url: str = Field(min_length=1)
    result_url: str = Field(min_length=1)


class AnalysisProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    status: AnalysisJobStatus


class HumanReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(confirm|reject|edit)$")
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=4000)
    event_type: str | None = None
    start_time: float | None = Field(default=None, ge=0)
    peak_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    risk_level: str | None = None


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    dialogue_id: str | None = None
    referenced_event_id: str | None = None


class QueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_tr: str
    event_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    procedure_sources: list[ProcedureSource] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    tool_trace: list[TraceRecord] = Field(default_factory=list)


class SystemMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_analyses: int = Field(ge=0)
    total_videos: int = Field(ge=0)
    total_analyses: int = Field(ge=0)
    total_events: int = Field(ge=0)
    confirmed_events: int = Field(ge=0)
    rejected_events: int = Field(ge=0)
    human_review_events: int = Field(ge=0)


class EventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    events: list[VerifiedEvent]


class ReportResponse(AnalysisResult):
    """Sözleşme adı okunaklı kalsın diye AnalysisResult'ın API görünümü."""


def event_to_json(event: VerifiedEvent) -> dict[str, Any]:
    """Pydantic dışı tüketiciler için tek biçimli event payload'ı."""

    return event.model_dump(mode="json")


__all__ = [
    "AnalysisAccepted",
    "AnalysisProgress",
    "AnalyzeRequest",
    "EventListResponse",
    "HumanReviewInput",
    "QueryRequest",
    "QueryResponse",
    "ReportResponse",
    "SystemMetrics",
    "event_to_json",
]
