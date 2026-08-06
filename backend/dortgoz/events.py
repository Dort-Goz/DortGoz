"""WebSocket olay şeması — backend ↔ frontend arasındaki TEK sözleşme.

Her olay `Event` zarfı içinde gider; `type` alanı ayrımcıdır (discriminator).
Frontend'teki `src/types/events.ts` bu dosyanın birebir aynasıdır — birini
değiştiren diğerini de değiştirir (bkz. docs/interface_design.md).
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

Risk = Literal["dusuk", "orta", "yuksek", "kritik"]

# A1 kararının olay taksonomisi (UCF-Crime sınıflarını kapsar).
# `bilinmeyen` = A1'deki `unknown_anomaly`: dikkat gerektiriyor ama sınıfa oturmuyor.
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


class WindowEvent(BaseModel):
    """Pencere içinde tespit edilen tek olay — GBNF şemasının yaprak düğümü."""

    t: float                          # video zamanı (sn)
    desc: str                         # Türkçe kısa betim
    severity_hint: Risk               # modelin ilk risk sezgisi (defter yeniden değerlendirir)


class WindowReport(BaseModel):
    """Bir 30 sn'lik pencerenin VLM yorumu (şema-garantili JSON'dan)."""

    type: Literal["window_report"] = "window_report"
    window_start: float
    window_end: float
    anomaly_type: AnomalyType = "normal"   # pencerenin baskın olay sınıfı
    summary: str                      # Türkçe pencere özeti
    events: list[WindowEvent] = []
    uncertainties: list[str] = []


class AgentStep(BaseModel):
    """LangGraph düğüm geçişi — ajan konsolu bu olaylarla canlanır."""

    type: Literal["agent_step"] = "agent_step"
    node: str                         # perceive | triage | interpret | ledger | oversight | respond
    status: Literal["start", "end", "error"]
    detail: str = ""


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool: str
    args: dict[str, Any] = {}
    rationale: str = ""               # her araç çağrısının gerekçesi — açıklanabilirlik
    result: str | None = None


class IncidentUpdate(BaseModel):
    """Olay defteri güncellemesi (yaşam döngüsü: basladi → gelisiyor → sonuclandi)."""

    type: Literal["incident_update"] = "incident_update"
    incident_id: str
    t: float                          # video zamanı (sn)
    phase: Literal["basladi", "gelisiyor", "sonuclandi"]
    title: str                        # ör. "Forklift devrilmesi"
    anomaly_type: AnomalyType = "bilinmeyen"
    risk: Risk
    detail: str = ""
    thumbnail: str | None = None      # /media altında kare yolu
    boxes: list[BoundingBox] = []


class ActuatorRequest(BaseModel):
    """Ajan bir aksiyon öneriyor — operatör onayı bekliyor (human-in-the-loop)."""

    type: Literal["actuator_request"] = "actuator_request"
    request_id: str
    actuator: str                     # saglik_ekibi_cagir | alarm_ver | alan_kapat | kayit_baslat
    reason: str                       # prosedür referanslı gerekçe (ör. "İSG-7: ...")
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
    streaming: bool = False           # True ise aynı mesajın devam parçası


class UICommand(BaseModel):
    """Ajanın arayüzü yönlendirmesi — 'ajan arayüzü kullanıyor'."""

    type: Literal["ui_command"] = "ui_command"
    action: Literal["seek_video", "highlight_incident", "show_report"]
    args: dict[str, Any] = {}


class RunStatus(BaseModel):
    type: Literal["run_status"] = "run_status"
    run_id: str
    state: Literal["idle", "processing", "done", "error"]
    progress: float = 0.0             # 0..1
    detail: str = ""
    # Koşuyu BAŞLATMAYAN istemci de videoyu bilmeli: sayfayı yenileyen operatör
    # ya da ikinci bir izleyici aksi halde boş oynatıcı görür (2026-08-05 QA).
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
    """Tel üzerindeki zarf."""

    seq: int = 0
    ts: float = Field(default_factory=time.time)
    payload: Payload = Field(discriminator="type")

    @staticmethod
    def wrap(payload: Payload, seq: int = 0) -> Event:
        return Event(seq=seq, payload=payload)


# ---- Operatörden gelen mesajlar (frontend → backend) ----

class OperatorMessage(BaseModel):
    """WS üzerinden istemciden gelen komutlar."""

    kind: Literal["chat", "actuator_response", "start_run", "stop_run"]
    text: str = ""                    # chat için
    request_id: str = ""              # actuator_response için
    approved: bool = False
    video: str = ""                   # start_run için /media altı yol
    # start_run deney seçenekleri (boş = varsayılan): model model sunucusu profil adı,
    # istemler interpret.py şablonlarının yerine geçer ({start}/{end} korunur)
    model: str = ""
    system_prompt: str = ""
    task_prompt: str = ""
