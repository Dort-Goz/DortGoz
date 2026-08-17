"""WebSocket olay şeması — backend ↔ frontend arasındaki TEK sözleşme.

Her olay `Event` zarfı içinde gider; `type` alanı ayrımcıdır (discriminator).
Frontend'teki `src/types/events.ts` bu dosyanın birebir aynasıdır — birini
değiştiren diğerini de değiştirir.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain.taxonomy import CanonicalEventType, canonical_event_type_from_ws_label

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


class FrameReference(BaseModel):
    """Uygulamanın tek VLM isteği içinde ürettiği kare kimliği."""

    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(pattern=r"^f_[0-9]{3,}$")
    timestamp: float = Field(ge=0, allow_inf_nan=False)


class EventEvidenceRef(BaseModel):
    """VLM olay iddiasını kendisine gösterilen tek kareye bağlar."""

    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(pattern=r"^f_[0-9]{3,}$")
    timestamp: float = Field(ge=0, allow_inf_nan=False)
    claim: str = Field(min_length=5, max_length=500)


class WindowEvent(BaseModel):
    """Pencere içinde tespit edilen tek olay — GBNF şemasının yaprak düğümü."""

    t: float                          # video zamanı (sn)
    desc: str                         # Türkçe kısa betim
    evidence: list[EventEvidenceRef] = Field(default_factory=list)
    severity_hint: Risk               # modelin ilk risk sezgisi (defter yeniden değerlendirir)
    # Eski WS fixture'ları bu alanı taşımayabilir; gerçek VLM şeması zorunlu kılar.
    event_type: CanonicalEventType | None = None


class WindowReport(BaseModel):
    """Bir 30 sn'lik pencerenin VLM yorumu (şema-garantili JSON'dan)."""

    type: Literal["window_report"] = "window_report"
    window_start: float
    window_end: float
    anomaly_type: AnomalyType = "normal"   # pencerenin baskın olay sınıfı
    summary: str                      # Türkçe pencere özeti
    events: list[WindowEvent] = []
    uncertainties: list[str] = []

    @property
    def canonical_event_type(self) -> CanonicalEventType:
        """Legacy WS label'ını değiştirmeden canonical tipe erişim sağlar."""

        return canonical_event_type_from_ws_label(self.anomaly_type)


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
    # Model EMİN DEĞİL → insan incelemesi istiyor (Bengisu tasarımındaki
    # operator_review_required durumu). Gerekçesi operatöre gösterilir.
    needs_review: bool = False
    review_reason: str = ""
    # 2. geçiş incelemesinin sayısal olay aralığı (None = henüz incelenmedi).
    # Jüri metriği (zamansal IoU) ve dışa aktarım bu aralığı kullanır.
    olay_baslangic: float | None = None
    olay_bitis: float | None = None

    @property
    def canonical_event_type(self) -> CanonicalEventType:
        """Legacy WS label'ını değiştirmeden canonical tipe erişim sağlar."""

        return canonical_event_type_from_ws_label(self.anomaly_type)


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
    # İşleme hızı, × gerçek zaman (işlenen görüntü sn / geçen duvar sn).
    # ≥1 = akış gerçek zamanda taşınıyor; çoklu-akışta toplam kapasiteyi
    # akış başına hızların toplamı verir. 0 = henüz ölçülmedi.
    speed: float = 0.0
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
    """Tel üzerindeki zarf.

    `feed`: çoklu-akış (demo) kipinde olayın hangi kamera akışına ait olduğu.
    Boş dize = tek-akış davranışı (geriye uyumlu). Yük tiplerine tek tek alan
    eklemek yerine ZARF etiketlenir — sözleşme tek noktadan genişler.
    """

    seq: int = 0
    ts: float = Field(default_factory=time.time)
    feed: str = ""
    payload: Payload = Field(discriminator="type")

    @staticmethod
    def wrap(payload: Payload, seq: int = 0, feed: str = "") -> Event:
        return Event(seq=seq, feed=feed, payload=payload)


# ---- Operatörden gelen mesajlar (frontend → backend) ----

class OperatorMessage(BaseModel):
    """WS üzerinden istemciden gelen komutlar."""

    kind: Literal["chat", "actuator_response", "start_run", "stop_run", "sync"]
    text: str = ""                    # chat için
    request_id: str = ""              # actuator_response için
    approved: bool = False
    from_seq: int = Field(default=0, ge=0)  # sync: istemcinin işlediği son olay
    video: str = ""                   # start_run için /media altı yol
    # start_run deney seçenekleri (boş = varsayılan): model model sunucusu profil adı,
    # istemler interpret.py şablonlarının yerine geçer ({start}/{end} korunur)
    model: str = ""
    system_prompt: str = ""
    task_prompt: str = ""
    feed: str = ""                    # start_run: çoklu-akış (demo) kamera etiketi
    # Çalışma kipi (2026-08-12 ölçülü cephe): "" = dengeli varsayılan.
    # dengeli 99/140@12 · temkinli ~88/140@5 (ikinci okuma doğrulaması) ·
    # genis ~116/140@23 (çift okuma + son tarama). Kayıt: iyilestirme_kampanyasi.
    mode: Literal["", "dengeli", "temkinli", "genis"] = ""
