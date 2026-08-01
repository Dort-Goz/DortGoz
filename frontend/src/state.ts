import type {
  ActuatorRequest, ActuatorResult, AgentStep, ChatMessage, Event,
  IncidentUpdate, RunStatus, ToolCall,
} from "./types/events";

export interface TraceEntry {
  seq: number;
  kind: "step" | "tool";
  step?: AgentStep;
  tool?: ToolCall;
}

export interface ConsoleState {
  incidents: IncidentUpdate[];
  trace: TraceEntry[];
  chat: ChatMessage[];
  actuatorRequests: ActuatorRequest[];
  actuatorResults: ActuatorResult[];
  runStatus: RunStatus | null;
  highlight: IncidentUpdate | null;
  seekTo: number | null;
}

export const initialState: ConsoleState = {
  incidents: [],
  trace: [],
  chat: [],
  actuatorRequests: [],
  actuatorResults: [],
  runStatus: null,
  highlight: null,
  seekTo: null,
};

export type Action = { kind: "event"; event: Event };

export function consoleReducer(state: ConsoleState, action: Action): ConsoleState {
  const { seq, payload: p } = action.event;
  switch (p.type) {
    case "incident_update": {
      const others = state.incidents.filter((i) => i.incident_id !== p.incident_id);
      return { ...state, incidents: [...others, p].sort((a, b) => a.t - b.t) };
    }
    case "agent_step":
      return { ...state, trace: [...state.trace, { seq, kind: "step", step: p }] };
    case "tool_call":
      return { ...state, trace: [...state.trace, { seq, kind: "tool", tool: p }] };
    case "chat_message": {
      // streaming: son ajan mesajına parça ekle
      if (p.streaming && p.role === "agent") {
        const chat = [...state.chat];
        const last = chat[chat.length - 1];
        if (last && last.role === "agent" && last.streaming) {
          chat[chat.length - 1] = { ...last, text: last.text + p.text };
          return { ...state, chat };
        }
        return { ...state, chat: [...chat, p] };
      }
      if (!p.streaming && p.text === "" && p.role === "agent") {
        // akış sonu işareti: son mesajı kalıcılaştır
        const chat = [...state.chat];
        const last = chat[chat.length - 1];
        if (last?.streaming) chat[chat.length - 1] = { ...last, streaming: false };
        return { ...state, chat };
      }
      return { ...state, chat: [...state.chat, p] };
    }
    case "actuator_request":
      return { ...state, actuatorRequests: [...state.actuatorRequests, p] };
    case "actuator_result":
      return { ...state, actuatorResults: [...state.actuatorResults, p] };
    case "run_status":
      return { ...state, runStatus: p };
    case "ui_command": {
      if (p.action === "seek_video") {
        return { ...state, seekTo: Number(p.args.t ?? 0) };
      }
      if (p.action === "highlight_incident") {
        const inc = state.incidents.find((i) => i.incident_id === p.args.incident_id) ?? null;
        return { ...state, highlight: inc };
      }
      return state;
    }
    default:
      return state;
  }
}
