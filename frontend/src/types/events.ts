/** WS olay şeması — backend/dortgoz/events.py'nin birebir aynası.
 *  Sözleşme değişiklikleri iki dosyada birden yapılır. */

export type Risk = "dusuk" | "orta" | "yuksek" | "kritik";

/** A1 kararının olay taksonomisi; `bilinmeyen` = A1'deki `unknown_anomaly`. */
export type AnomalyType =
  | "kavga" | "saldiri" | "hirsizlik" | "silahli_olay" | "yangin"
  | "patlama" | "arac_kazasi" | "vandalizm" | "normal" | "bilinmeyen";

/** Canonical internal taxonomy'nin frontend aynası; WS legacy Türkçe değer taşır. */
export type CanonicalEventType =
  | "normal" | "uncertain" | "unknown_anomaly"
  | "physical_fight" | "assault"
  | "possible_theft" | "possible_armed_incident"
  | "fire_smoke" | "explosion" | "vehicle_collision" | "vandalism";

export interface BoundingBox {
  x1: number; y1: number; x2: number; y2: number;
  label: string;
  track_id?: number | null;
  conf?: number | null;
}

export interface EventEvidenceRef {
  frame_id: string;
  timestamp: number;
  claim: string;
}

export interface WindowEvent {
  t: number;
  desc: string;
  /** Eski WS kayıtlarında bulunmayabilir; gerçek VLM olaylarında doludur. */
  evidence?: EventEvidenceRef[];
  severity_hint: Risk;
  /** Canonical internal tip; eski WS kayıtlarında bulunmayabilir. */
  event_type?: CanonicalEventType | null;
}

export interface WindowReport {
  type: "window_report";
  window_start: number;
  window_end: number;
  anomaly_type: AnomalyType;
  summary: string;
  events: WindowEvent[];
  uncertainties: string[];
}

export interface AgentStep {
  type: "agent_step";
  node: string;
  status: "start" | "end" | "error";
  detail: string;
}

export interface ToolCall {
  type: "tool_call";
  tool: string;
  args: Record<string, unknown>;
  rationale: string;
  result?: string | null;
}

export interface IncidentUpdate {
  type: "incident_update";
  incident_id: string;
  t: number;
  phase: "basladi" | "gelisiyor" | "sonuclandi";
  title: string;
  anomaly_type: AnomalyType;
  risk: Risk;
  detail: string;
  thumbnail?: string | null;
  boxes: BoundingBox[];
  /** Model emin değil → insan incelemesi istiyor; gerekçesi gösterilir. */
  needs_review?: boolean;
  review_reason?: string;
}

export interface ActuatorRequest {
  type: "actuator_request";
  request_id: string;
  actuator: string;
  reason: string;
  incident_id?: string | null;
}

export interface ActuatorResult {
  type: "actuator_result";
  request_id: string;
  actuator: string;
  approved: boolean;
  detail: string;
}

export interface ChatMessage {
  type: "chat_message";
  role: "operator" | "agent";
  text: string;
  streaming: boolean;
}

export interface UICommand {
  type: "ui_command";
  action: "seek_video" | "highlight_incident" | "show_report";
  args: Record<string, unknown>;
}

export interface RunStatus {
  type: "run_status";
  run_id: string;
  state: "idle" | "processing" | "done" | "error";
  progress: number;
  /** İşleme hızı, × gerçek zaman; ≥1 = akış gerçek zamanda taşınıyor. */
  speed?: number;
  detail: string;
  /** Koşulan klip — koşuyu başlatmayan istemci (yenileme, 2. izleyici) için */
  video: string;
}

export type Payload =
  | WindowReport | AgentStep | ToolCall | IncidentUpdate
  | ActuatorRequest | ActuatorResult | ChatMessage | UICommand | RunStatus;

export interface Event {
  seq: number;
  ts: number;
  /** Çoklu-akış (demo) kipinde kamera etiketi; boş/yok = tek akış. */
  feed?: string;
  payload: Payload;
}

/** frontend → backend */
export interface OperatorMessage {
  kind: "chat" | "actuator_response" | "start_run" | "stop_run";
  text?: string;
  request_id?: string;
  approved?: boolean;
  feed?: string;
  video?: string;
  /** start_run deney seçenekleri — boş/undefined = varsayılan (bkz. events.py) */
  model?: string;
  system_prompt?: string;
  task_prompt?: string;
  /** Çalışma kipi: "" = dengeli varsayılan; hassas = ikinci-okuma doğrulaması;
   *  genis = çift okuma + son tarama (max-recall). */
  mode?: "" | "dengeli" | "hassas" | "genis";
}
