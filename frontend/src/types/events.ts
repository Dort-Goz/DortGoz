export type Risk = "dusuk" | "orta" | "yuksek" | "kritik";

export type AnomalyType =
  | "kavga" | "saldiri" | "hirsizlik" | "silahli_olay" | "yangin"
  | "patlama" | "arac_kazasi" | "vandalizm" | "normal" | "bilinmeyen";

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
  evidence?: EventEvidenceRef[];
  severity_hint: Risk;
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

export interface WindowSignals {
  durum_p?: number | null;
  anomaly_score?: number | null;
  interaction_score?: number | null;
  fall_score?: number | null;
  fire_smoke_score?: number | null;
  vehicle_conflict_score?: number | null;
  tampering_score?: number | null;
  image_quality?: number | null;
  changed?: number | null;
  fg?: number | null;
  mad?: number | null;
  screening_model?: string;
}

export interface IncidentUpdate {
  type: "incident_update";
  signals?: WindowSignals | null;
  incident_id: string;
  t: number;
  phase: "basladi" | "gelisiyor" | "sonuclandi";
  title: string;
  anomaly_type: AnomalyType;
  risk: Risk;
  detail: string;
  thumbnail?: string | null;
  boxes: BoundingBox[];
  needs_review?: boolean;
  review_reason?: string;
  olay_baslangic?: number | null;
  olay_bitis?: number | null;
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
  speed?: number;
  detail: string;
  video: string;
}

export type Payload =
  | WindowReport | AgentStep | ToolCall | IncidentUpdate
  | ActuatorRequest | ActuatorResult | ChatMessage | UICommand | RunStatus;

export interface Event {
  seq: number;
  ts: number;
  feed?: string;
  payload: Payload;
}

export interface OperatorMessage {
  kind: "chat" | "actuator_response" | "start_run" | "stop_run";
  text?: string;
  request_id?: string;
  approved?: boolean;
  feed?: string;
  video?: string;
  model?: string;
  system_prompt?: string;
  task_prompt?: string;
  mode?: "" | "dengeli" | "temkinli" | "genis";
}
