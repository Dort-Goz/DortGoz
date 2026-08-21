import type {
  ActuatorRequest, ActuatorResult, AgentStep, ChatMessage, Event,
  IncidentUpdate, RunStatus, ToolCall, WindowReport,
} from "./types/events";

export interface TraceEntry {
  seq: number;
  kind: "step" | "tool";
  step?: AgentStep;
  tool?: ToolCall;
}

export interface FeedState {
  incidents: IncidentUpdate[];
  reports: WindowReport[];
  trace: TraceEntry[];
  runStatus: RunStatus | null;
  highlight: IncidentUpdate | null;
  seekTo: number | null;
  video: string | null;
}

export const emptyFeed: FeedState = {
  incidents: [],
  reports: [],
  trace: [],
  runStatus: null,
  highlight: null,
  seekTo: null,
  video: null,
};

export interface ConsoleState {
  feeds: Record<string, FeedState>;
  active: string;
  chat: ChatMessage[];
  actuatorRequests: ActuatorRequest[];
  actuatorResults: ActuatorResult[];
}

export const initialState: ConsoleState = {
  feeds: {},
  active: "",
  chat: [],
  actuatorRequests: [],
  actuatorResults: [],
};

export const CAPS = {
  reports: 400,
  trace: 300,
  incidents: 200,
  chat: 200,
  actuators: 100,
} as const;

function cap<T>(arr: T[], n: number): T[] {
  return arr.length > n ? arr.slice(arr.length - n) : arr;
}

export type Action =
  | { kind: "event"; event: Event }
  | { kind: "hydrate_actions"; requests: ActuatorRequest[]; results: ActuatorResult[] }
  | { kind: "run_started"; video: string; feed: string }
  | { kind: "select_incident"; incident: IncidentUpdate }
  | { kind: "select_feed"; feed: string };

function withFeed(state: ConsoleState, feed: string,
                  patch: (f: FeedState) => FeedState): ConsoleState {
  const cur = state.feeds[feed] ?? emptyFeed;
  const active = state.feeds[state.active] || state.active === feed
    ? state.active : feed;
  return { ...state, active, feeds: { ...state.feeds, [feed]: patch(cur) } };
}

export function consoleReducer(state: ConsoleState, action: Action): ConsoleState {
  if (action.kind === "hydrate_actions") {
    return {
      ...state,
      actuatorRequests: cap(action.requests, CAPS.actuators),
      actuatorResults: cap(action.results, CAPS.actuators),
    };
  }
  if (action.kind === "select_feed") {
    return { ...state, active: action.feed };
  }
  if (action.kind === "select_incident") {
    return withFeed(state, state.active, (f) => ({
      ...f, highlight: action.incident, seekTo: action.incident.t,
    }));
  }
  if (action.kind === "run_started") {
    return {
      ...withFeed(state, action.feed, () => ({ ...emptyFeed, video: action.video })),
      active: action.feed,
    };
  }

  const { seq, payload: p } = action.event;
  const feed = action.event.feed ?? "";
  switch (p.type) {
    case "window_report":
      return withFeed(state, feed, (f) => ({
        ...f, reports: cap([...f.reports, p], CAPS.reports),
      }));
    case "incident_update":
      return withFeed(state, feed, (f) => {
        const others = f.incidents.filter((i) => i.incident_id !== p.incident_id);
        return {
          ...f,
          incidents: cap([...others, p].sort((a, b) => a.t - b.t), CAPS.incidents),
        };
      });
    case "agent_step":
      return withFeed(state, feed, (f) => ({
        ...f, trace: cap([...f.trace, { seq, kind: "step" as const, step: p }], CAPS.trace),
      }));
    case "tool_call":
      return withFeed(state, feed || state.active, (f) => ({
        ...f, trace: cap([...f.trace, { seq, kind: "tool" as const, tool: p }], CAPS.trace),
      }));
    case "chat_message": {
      if (p.streaming && p.role === "agent") {
        const chat = [...state.chat];
        const last = chat[chat.length - 1];
        if (last && last.role === "agent" && last.streaming) {
          chat[chat.length - 1] = { ...last, text: last.text + p.text };
          return { ...state, chat };
        }
        return { ...state, chat: cap([...chat, p], CAPS.chat) };
      }
      if (!p.streaming && p.text === "" && p.role === "agent") {
        const chat = [...state.chat];
        const last = chat[chat.length - 1];
        if (last?.streaming) chat[chat.length - 1] = { ...last, streaming: false };
        return { ...state, chat };
      }
      return { ...state, chat: cap([...state.chat, p], CAPS.chat) };
    }
    case "actuator_request": {
      const request = p.feed || !feed ? p : { ...p, feed };
      const others = state.actuatorRequests.filter((item) => item.request_id !== p.request_id);
      return {
        ...state,
        actuatorRequests: cap([...others, request], CAPS.actuators),
      };
    }
    case "actuator_result": {
      const result = p.feed || !feed ? p : { ...p, feed };
      const others = state.actuatorResults.filter((item) => item.request_id !== p.request_id);
      return {
        ...state,
        actuatorResults: cap([...others, result], CAPS.actuators),
      };
    }
    case "run_status":
      return withFeed(state, feed, (f) => {
        const newRun = p.run_id !== "-" && p.run_id !== f.runStatus?.run_id;
        if (newRun && !f.video) {
          return { ...emptyFeed, video: p.video || null, runStatus: p };
        }
        return { ...f, runStatus: p, video: f.video ?? (p.video || null) };
      });
    case "ui_command": {
      const target = feed || state.active;
      if (p.action === "seek_video") {
        return withFeed(state, target, (f) => ({ ...f, seekTo: Number(p.args.t ?? 0) }));
      }
      if (p.action === "highlight_incident") {
        return withFeed(state, target, (f) => ({
          ...f,
          highlight: f.incidents.find((i) => i.incident_id === p.args.incident_id) ?? null,
        }));
      }
      return state;
    }
    default:
      return state;
  }
}
