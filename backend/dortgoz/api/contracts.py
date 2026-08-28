from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain.event import VerifiedEvent
from ..domain.feedback import (
    DevelopmentApprovalStatus,
    DevelopmentUse,
    FalseAlarmReason,
)
from ..domain.media import IncidentMedia
from ..domain.memory import AnalysisResult
from ..domain.model_lifecycle import DfineArchitecture
from ..domain.provenance import ProcedureSource, TraceRecord
from ..domain.training import FrameReviewResult, TrainingSample, VerifiedBoundingBox
from ..services.analysis_job import AnalysisJobStatus


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(default="canonical", min_length=1, deprecated=True)
    config_version: str = Field(default="task-06-v1", min_length=1, deprecated=True)
    feed: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=500)
    system_prompt: str = Field(default="", max_length=20_000)
    task_prompt: str = Field(default="", max_length=20_000)
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

    decision: Literal["confirm", "reject", "edit"]
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=4000)
    event_type: str | None = None
    start_time: float | None = Field(default=None, ge=0)
    peak_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    risk_level: str | None = None
    false_alarm_reason: FalseAlarmReason | None = None
    intervention_required: bool | None = None


class MaintenanceReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator_review_id: str = Field(min_length=1)
    decision: Literal["confirm", "reject", "edit"]
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=4000)
    event_type: str | None = None
    start_time: float | None = Field(default=None, ge=0)
    peak_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    risk_level: str | None = None
    false_alarm_reason: FalseAlarmReason | None = None

    @model_validator(mode="after")
    def review_fields_are_consistent(self) -> MaintenanceReviewInput:
        times = (self.start_time, self.peak_time, self.end_time)
        if any(value is not None for value in times) and not all(
            value is not None for value in times
        ):
            raise ValueError("IT inceleme zamanları birlikte verilmelidir")
        if all(value is not None for value in times):
            start, peak, end = times
            assert start is not None and peak is not None and end is not None
            if not start <= peak <= end:
                raise ValueError("IT inceleme zamanları sıralı olmalıdır")
        if self.decision in {"confirm", "edit"}:
            if self.event_type is None:
                raise ValueError("IT anomali kararı kategori gerektirir")
        elif self.event_type is not None:
            raise ValueError("IT anomali yok kararı kategori taşıyamaz")
        return self


class TriageDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=400)
    verdict: Literal["anomali", "sorun_degil"]
    category: Literal[
        "kavga",
        "saldiri",
        "hirsizlik",
        "silahli_olay",
        "yangin",
        "patlama",
        "arac_kazasi",
        "vandalizm",
        "bilinmeyen",
    ] | None = None
    risk_level: Literal["dusuk", "orta", "yuksek", "kritik"] | None = None
    start_time: float | None = Field(default=None, ge=0)
    peak_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    false_alarm_reason: FalseAlarmReason | None = None
    intervention_required: bool
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="operator-console", min_length=1, max_length=120)

    @model_validator(mode="after")
    def decision_fields_match_verdict(self) -> TriageDecisionInput:
        times = (self.start_time, self.peak_time, self.end_time)
        if any(value is not None for value in times) and not all(
            value is not None for value in times
        ):
            raise ValueError("olay başlangıç, zirve ve bitiş zamanı birlikte verilmelidir")
        if all(value is not None for value in times):
            start, peak, end = times
            assert start is not None and peak is not None and end is not None
            if not start <= peak <= end:
                raise ValueError("beklenen sıra: start_time <= peak_time <= end_time")
        if self.verdict == "anomali":
            if self.category is None or self.risk_level is None:
                raise ValueError("anomali kararı kategori ve risk düzeyi gerektirir")
            if not all(value is not None for value in times):
                raise ValueError("anomali kararı doğrulanmış olay zamanlarını gerektirir")
            if self.false_alarm_reason is not None:
                raise ValueError("anomali kararı yanlış alarm nedeni taşıyamaz")
        else:
            if self.false_alarm_reason is None:
                raise ValueError("sorun değil kararı yanlış alarm nedeni gerektirir")
            if self.category is not None or self.risk_level is not None:
                raise ValueError("sorun değil kararı kategori veya risk düzeyi taşıyamaz")
            if any(value is not None for value in times):
                raise ValueError("sorun değil kararı olay zamanı düzeltmesi taşıyamaz")
        if self.false_alarm_reason == FalseAlarmReason.OTHER and not self.note.strip():
            raise ValueError("diğer yanlış alarm nedeni açıklama gerektirir")
        return self


class OperatorReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feed: str = Field(default="", max_length=120)
    live: bool = False
    category: Literal[
        "kavga",
        "saldiri",
        "hirsizlik",
        "silahli_olay",
        "yangin",
        "patlama",
        "arac_kazasi",
        "vandalizm",
        "bilinmeyen",
    ]
    risk: Literal["dusuk", "orta", "yuksek", "kritik"]
    note: str = Field(min_length=3, max_length=500)
    reviewer: str = Field(min_length=1, max_length=120)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    run_id: str = Field(default="", max_length=200)
    video: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def window_is_valid(self) -> OperatorReportInput:
        if self.end <= self.start:
            raise ValueError("bildirim bitişi başlangıçtan sonra olmalıdır")
        if self.end - self.start > 600:
            raise ValueError("bildirim penceresi 10 dakikayı aşamaz")
        if self.live and not self.feed:
            raise ValueError("canlı bildirim kamera adı gerektirir")
        return self


class DevelopmentApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1)
    maintenance_review_id: str | None = Field(default=None, min_length=1)
    status: DevelopmentApprovalStatus
    approved_uses: list[DevelopmentUse] = Field(default_factory=list)
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=4000)
    supersedes_approval_id: str | None = None

    @model_validator(mode="after")
    def approval_fields_match_status(self) -> DevelopmentApprovalInput:
        if len(set(self.approved_uses)) != len(self.approved_uses):
            raise ValueError("approved_uses tekrar eden değer içeremez")
        if self.status == DevelopmentApprovalStatus.APPROVED and not self.approved_uses:
            raise ValueError("onay kararı en az bir kullanım gerektirir")
        if self.status != DevelopmentApprovalStatus.APPROVED and self.approved_uses:
            raise ValueError("ret veya geri alma kararı kullanım izni taşıyamaz")
        if (
            self.status == DevelopmentApprovalStatus.REVOKED
            and self.supersedes_approval_id is None
        ):
            raise ValueError("geri alma kararı önceki onay kaydını belirtmelidir")
        return self


class TrainingSamplePrepareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    dataset_manifest_name: str = Field(min_length=6, max_length=255)
    prepared_by: str = Field(min_length=1, max_length=120)
    timestamps: list[float] | None = Field(default=None, min_length=1, max_length=9)


class TrainingSampleReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_result: FrameReviewResult
    boxes: list[VerifiedBoundingBox] = Field(default_factory=list, max_length=500)
    reviewer: str = Field(min_length=1, max_length=120)
    annotation_tool: str = Field(min_length=1, max_length=120)


class TrainingSampleView(TrainingSample):
    frame_url: str = Field(pattern=r"^/media/[A-Za-z0-9_./-]+$")


class IncidentMediaView(IncidentMedia):
    clip_url: str = Field(pattern=r"^/media/[A-Za-z0-9_./-]+$")
    thumbnail_url: str = Field(pattern=r"^/media/[A-Za-z0-9_./-]+$")


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
    pass


class BatchApprovalInput(BaseModel):
    """Approve one development use across many reviewed events at once."""

    model_config = ConfigDict(extra="forbid")

    event_ids: list[str] = Field(min_length=1, max_length=200)
    approved_uses: list[DevelopmentUse] = Field(min_length=1)
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def entries_are_unique(self) -> BatchApprovalInput:
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event_ids tekrar eden değer içeremez")
        if len(set(self.approved_uses)) != len(self.approved_uses):
            raise ValueError("approved_uses tekrar eden değer içeremez")
        return self


class BatchApprovalFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    reason: str


class BatchApprovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approved_event_ids: list[str]
    failures: list[BatchApprovalFailure]


class TrainingJobPlanInput(BaseModel):
    """Paket oluştur: verified samples → COCO export → queued training job."""

    model_config = ConfigDict(extra="forbid")

    architecture: DfineArchitecture
    requested_by: str = Field(min_length=1, max_length=120)
    epochs: int = Field(default=10, ge=1, le=500)
    batch_size: int = Field(default=2, ge=1, le=128)
    workers: int = Field(default=2, ge=0, le=64)
    gpu_index: int = Field(default=0, ge=0, le=31)
    max_gpu_minutes: int = Field(default=60, ge=1, le=1440)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)


class ModelPromotionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=4000)


def event_to_json(event: VerifiedEvent) -> dict[str, Any]:
    return event.model_dump(mode="json")


__all__ = [
    "AnalysisAccepted",
    "AnalysisProgress",
    "AnalyzeRequest",
    "BatchApprovalFailure",
    "BatchApprovalInput",
    "BatchApprovalResult",
    "DevelopmentApprovalInput",
    "EventListResponse",
    "HumanReviewInput",
    "IncidentMediaView",
    "ModelPromotionInput",
    "OperatorReportInput",
    "QueryRequest",
    "QueryResponse",
    "ReportResponse",
    "SystemMetrics",
    "TrainingJobPlanInput",
    "TrainingSamplePrepareInput",
    "TrainingSampleReviewInput",
    "TrainingSampleView",
    "TriageDecisionInput",
    "event_to_json",
]
