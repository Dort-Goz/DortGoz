from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain.taxonomy import CanonicalEventType, canonical_event_type_from_ws_label

Risk = Literal["dusuk", "orta", "yuksek", "kritik"]

AnomalyType = Literal[
    "kavga", "saldiri", "hirsizlik", "silahli_olay", "yangin",
    "patlama", "arac_kazasi", "vandalizm", "normal", "bilinmeyen",
]


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    label: str
    track_id: int | None = None
    conf: float | None = None


class FrameReference(BaseModel):

    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(pattern=r"^f_[0-9]{3,}$")
    timestamp: float = Field(ge=0, allow_inf_nan=False)


class EventEvidenceRef(BaseModel):

    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(pattern=r"^f_[0-9]{3,}$")
    timestamp: float = Field(ge=0, allow_inf_nan=False)
    claim: str = Field(min_length=5, max_length=500)


class WindowEvent(BaseModel):

    t: float
    desc: str
    evidence: list[EventEvidenceRef] = Field(default_factory=list)
    severity_hint: Risk
    event_type: CanonicalEventType | None = None


class WindowReport(BaseModel):

    type: Literal["window_report"] = "window_report"
    window_start: float
    window_end: float
    anomaly_type: AnomalyType = "normal"
    summary: str
    events: list[WindowEvent] = []
    uncertainties: list[str] = []

    @property
    def canonical_event_type(self) -> CanonicalEventType:

        return canonical_event_type_from_ws_label(self.anomaly_type)


ActivityStatus = Literal["sakin", "eleme", "hareket", "dikkat", "anomali"]


class ActivityStrip(BaseModel):

    type: Literal["activity_strip"] = "activity_strip"
    window_start: float
    window_end: float
    wall_end: float = Field(default_factory=time.time)
    content_start: float = 0.0
    gate: float = 0.0
    peak: float = 0.0
    status: ActivityStatus = "sakin"
    risk: Risk | None = None
    levels: list[int] = Field(default_factory=list, max_length=600)


class AgentStep(BaseModel):

    type: Literal["agent_step"] = "agent_step"
    node: str
    status: Literal["start", "end", "error"]
    detail: str = Field(default="", max_length=500)
    dialogue_id: str = Field(default="", max_length=128)


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool: str = Field(max_length=128)
    args: dict[str, Any] = {}
    rationale: str = Field(default="", max_length=500)
    result: str | None = None
    dialogue_id: str = Field(default="", max_length=128)


OPERATOR_INCIDENT_PREFIX = "op-"


class WindowSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    durum_p: float | None = None
    anomaly_score: float | None = None
    interaction_score: float | None = None
    fall_score: float | None = None
    fire_smoke_score: float | None = None
    vehicle_conflict_score: float | None = None
    tampering_score: float | None = None
    image_quality: float | None = None
    changed: float | None = None
    fg: float | None = None
    mad: float | None = None
    screening_model: str = ""


class IncidentUpdate(BaseModel):

    type: Literal["incident_update"] = "incident_update"
    signals: WindowSignals | None = None
    incident_id: str
    t: float
    phase: Literal["basladi", "gelisiyor", "sonuclandi"]
    title: str
    anomaly_type: AnomalyType = "bilinmeyen"
    risk: Risk
    detail: str = ""
    thumbnail: str | None = None
    evidence: str | None = None
    boxes: list[BoundingBox] = []
    needs_review: bool = False
    review_reason: str = ""
    olay_baslangic: float | None = None
    olay_bitis: float | None = None

    @property
    def canonical_event_type(self) -> CanonicalEventType:

        return canonical_event_type_from_ws_label(self.anomaly_type)


class ActuatorRequest(BaseModel):

    type: Literal["actuator_request"] = "actuator_request"
    request_id: str
    actuator: str
    reason: str
    incident_id: str | None = None
    action_label: str = ""
    incident_title: str = ""
    run_id: str = ""
    feed: str = ""
    live: bool = False
    anomaly_type: AnomalyType = "bilinmeyen"
    risk: Risk | None = None
    evidence_timestamps: list[float] = Field(default_factory=list)
    mode: Literal["preview"] = "preview"
    status: Literal["pending"] = "pending"
    requested_at: float | None = None


class ActuatorResult(BaseModel):
    type: Literal["actuator_result"] = "actuator_result"
    request_id: str
    actuator: str
    approved: bool
    detail: str = ""
    action_label: str = ""
    status: Literal["prepared", "rejected", "failed"] | None = None
    incident_id: str | None = None
    run_id: str = ""
    feed: str = ""
    live: bool = False
    mode: Literal["preview"] = "preview"
    delivered: Literal[False] = False
    external_side_effect: Literal[False] = False
    artifact_url: str | None = None
    operator: str = ""
    resolved_at: float | None = None


class ChatMessage(BaseModel):
    type: Literal["chat_message"] = "chat_message"
    role: Literal["operator", "agent"]
    text: str = Field(max_length=8000)
    streaming: bool = False
    dialogue_id: str = Field(default="", max_length=128)


class UICommand(BaseModel):

    type: Literal["ui_command"] = "ui_command"
    action: Literal["seek_video", "highlight_incident", "show_report"]
    args: dict[str, Any] = {}


class RunStatus(BaseModel):

    type: Literal["run_status"] = "run_status"
    run_id: str
    state: Literal["idle", "processing", "done", "error"]
    progress: float = 0.0
    speed: float = 0.0
    detail: str = ""
    video: str = ""


class ReviewSample(BaseModel):

    type: Literal["review_sample"] = "review_sample"
    sample_id: str
    t: float
    window_start: float
    window_end: float
    summary: str = ""
    signals: WindowSignals | None = None
    thumbnail: str | None = None
    evidence: str | None = None


Payload = (
    WindowReport
    | ActivityStrip
    | AgentStep
    | ToolCall
    | IncidentUpdate
    | ReviewSample
    | ActuatorRequest
    | ActuatorResult
    | ChatMessage
    | UICommand
    | RunStatus
)


class Event(BaseModel):

    seq: int = 0
    ts: float = Field(default_factory=time.time)
    feed: str = ""
    live: bool = False
    payload: Payload = Field(discriminator="type")

    @staticmethod
    def wrap(
        payload: Payload, seq: int = 0, feed: str = "", live: bool = False
    ) -> Event:
        return Event(seq=seq, feed=feed, live=live, payload=payload)


class OperatorMessage(BaseModel):

    kind: Literal["chat", "actuator_response", "start_run", "stop_run", "sync"]
    text: str = Field(default="", max_length=8000)
    request_id: str = ""
    approved: bool = False
    operator: str = ""
    from_seq: int = Field(default=0, ge=0)
    video: str = ""
    model: str = ""
    system_prompt: str = ""
    task_prompt: str = ""
    feed: str = Field(default="", max_length=128)
    dialogue_id: str = Field(default="", max_length=128)
    referenced_event_id: str = Field(default="", max_length=128)
    mode: Literal["", "dengeli", "temkinli", "genis"] = ""
