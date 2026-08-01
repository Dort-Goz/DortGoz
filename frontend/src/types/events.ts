/** WS olay şeması — backend/dortgoz/events.py'nin birebir aynası.
 *  Sözleşme değişiklikleri iki dosyada birden yapılır (docs/interface_design.md). */

export type Risk = "dusuk" | "orta" | "yuksek" | "kritik";

export interface BoundingBox {
  x1: number; y1: number; x2: number; y2: number;
  label: string;
  track_id?: number | null;
  conf?: number | null;
}

export interface WindowReport {
  type: "window_report";
  window_start: number;
  window_end: number;
  summary: string;
  events: { t: number; desc: string; severity_hint?: string }[];
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
  risk: Risk;
  detail: string;
  thumbnail?: string | null;
  boxes: BoundingBox[];
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
  detail: string;
}

export type Payload =
  | WindowReport | AgentStep | ToolCall | IncidentUpdate
  | ActuatorRequest | ActuatorResult | ChatMessage | UICommand | RunStatus;

export interface Event {
  seq: number;
  ts: number;
  payload: Payload;
}

/** frontend → backend */
export interface OperatorMessage {
  kind: "chat" | "actuator_response" | "start_run" | "stop_run";
  text?: string;
  request_id?: string;
  approved?: boolean;
  video?: string;
}
