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


class AgentStep(BaseModel):

    type: Literal["agent_step"] = "agent_step"
    node: str
    status: Literal["start", "end", "error"]
    detail: str = ""


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool: str
    args: dict[str, Any] = {}
    rationale: str = ""
    result: str | None = None


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


class ActuatorResult(BaseModel):
    type: Literal["actuator_result"] = "actuator_result"
    request_id: str
    actuator: str
    approved: bool
    detail: str = ""


class ChatMessage(BaseModel):
    type: Literal["chat_message"] = "chat_message"
    role: Literal["operator", "agent"]
    text: str
    streaming: bool = False


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


Payload = (
    WindowReport
    | AgentStep
    | ToolCall
    | IncidentUpdate
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
    payload: Payload = Field(discriminator="type")

    @staticmethod
    def wrap(payload: Payload, seq: int = 0, feed: str = "") -> Event:
        return Event(seq=seq, feed=feed, payload=payload)


class OperatorMessage(BaseModel):

    kind: Literal["chat", "actuator_response", "start_run", "stop_run"]
    text: str = ""
    request_id: str = ""
    approved: bool = False
    video: str = ""
    model: str = ""
    system_prompt: str = ""
    task_prompt: str = ""
    feed: str = ""
    mode: Literal["", "dengeli", "temkinli", "genis"] = ""
