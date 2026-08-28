import type {
  ActivityStrip, ActuatorRequest, ActuatorResult, AgentStep, ChatMessage, Event,
  IncidentUpdate, RunStatus, ToolCall, WindowReport,
} from "./types/events";

export const ACTIVITY_WINDOW_SECONDS = 120;

export interface TraceEntry {
  seq: number;
  kind: "step" | "tool";
  step?: AgentStep;
  tool?: ToolCall;
}

export interface StoredIncident extends IncidentUpdate {
  boxT?: number;
}

export interface FeedState {
  incidents: StoredIncident[];
  incidentMap: Map<string, StoredIncident>;
  reports: WindowReport[];
  trace: TraceEntry[];
  runStatus: RunStatus | null;
  highlight: StoredIncident | null;
  seekTo: number | null;
  seekNonce: number;
  reportsPulse: number;
  video: string | null;
  live: boolean;
  activity: ActivityStrip[];
}

export const emptyFeed: FeedState = {
  incidents: [],
  incidentMap: new Map(),
  reports: [],
  trace: [],
  runStatus: null,
  highlight: null,
  seekTo: null,
  seekNonce: 0,
  reportsPulse: 0,
  video: null,
  live: false,
  activity: [],
};

export function pruneActivity(
  strips: ActivityStrip[], now = Date.now() / 1000,
): ActivityStrip[] {
  const cutoff = now - ACTIVITY_WINDOW_SECONDS;
  const kept = strips.filter((strip) => strip.wall_end >= cutoff);
  return kept.length > CAPS.activity ? kept.slice(kept.length - CAPS.activity) : kept;
}

export interface ConsoleState {
  feeds: Record<string, FeedState>;
  active: string;
  activeLive: string;
  chat: ChatMessage[];
  actuatorRequests: ActuatorRequest[];
  actuatorResults: ActuatorResult[];
  liveActuatorRequests: ActuatorRequest[];
  liveActuatorResults: ActuatorResult[];
}

export const initialState: ConsoleState = {
  feeds: {},
  active: "",
  activeLive: "",
  chat: [],
  actuatorRequests: [],
  actuatorResults: [],
  liveActuatorRequests: [],
  liveActuatorResults: [],
};

export function isLiveFeed(state: ConsoleState, feed: string): boolean {
  return state.feeds[feed]?.live ?? false;
}

export function feedNames(state: ConsoleState, live: boolean): string[] {
  return Object.keys(state.feeds).filter(
    (name) => name !== "" && state.feeds[name].live === live,
  );
}

export const CAPS = {
  reports: 400,
  trace: 300,
  incidents: 200,
  chat: 200,
  actuators: 100,
  activity: 40,
} as const;

export function actionBelongsToMode(
  action: Pick<ActuatorRequest | ActuatorResult, "run_id">,
  fixtureMode: boolean,
): boolean {
  const mockRun = action.run_id.startsWith("fixture-ui-")
    || action.run_id.startsWith("canli-mock-");
  return mockRun === fixtureMode;
}

// Analiz çalışma alanında açık olan koşular. Aksiyon günlüğü bu kümeye
// dayanır: biten koşunun kartı yeni koşunun günlüğünde kalmaz.
export function analysisRunIds(state: ConsoleState): Set<string> {
  const ids = new Set<string>();
  for (const feed of Object.values(state.feeds)) {
    const runId = feed.live ? "" : feed.runStatus?.run_id ?? "";
    if (runId && runId !== "-") ids.add(runId);
  }
  return ids;
}

function cap<T>(arr: T[], n: number): T[] {
  return arr.length > n ? arr.slice(arr.length - n) : arr;
}

export type Action =
  | { kind: "event"; event: Event }
  | { kind: "sync_reset" }
  | { kind: "clear_chat" }
  | { kind: "clear_analysis" }
  | { kind: "hydrate_actions"; requests: ActuatorRequest[]; results: ActuatorResult[] }
  | { kind: "run_started"; video: string; feed: string }
  | { kind: "select_incident"; incident: IncidentUpdate }
  | { kind: "seek"; feed: string; timestamp: number; video?: string }
  | { kind: "select_feed"; feed: string; live?: boolean };

function withFeed(state: ConsoleState, feed: string, live: boolean,
                  patch: (f: FeedState) => FeedState): ConsoleState {
  const cur = state.feeds[feed] ?? { ...emptyFeed, live };
  const current = live ? state.activeLive : state.active;
  const active = state.feeds[current] || current === feed ? current : feed;
  const feeds = { ...state.feeds, [feed]: { ...patch(cur), live } };
  return live
    ? { ...state, activeLive: active, feeds }
    : { ...state, active, feeds };
}

export function consoleReducer(state: ConsoleState, action: Action): ConsoleState {
  if (action.kind === "sync_reset") {
    return initialState;
  }
  if (action.kind === "clear_chat") {
    return { ...state, chat: [] };
  }
  // Operatörün "temizle" düğmesi: analiz çalışma alanı boşaltılır, canlı izleme
  // el değmeden kalır. Sunucudaki kayıtlar silinmez.
  if (action.kind === "clear_analysis") {
    return {
      ...state,
      feeds: Object.fromEntries(
        Object.entries(state.feeds).filter(([, feed]) => feed.live),
      ),
      active: "",
      chat: [],
      actuatorRequests: [],
      actuatorResults: [],
    };
  }
  if (action.kind === "hydrate_actions") {
    return {
      ...state,
      actuatorRequests: cap(
        action.requests.filter((request) => !request.live), CAPS.actuators,
      ),
      actuatorResults: cap(
        action.results.filter((result) => !result.live), CAPS.actuators,
      ),
      liveActuatorRequests: cap(
        action.requests.filter((request) => request.live), CAPS.actuators,
      ),
      liveActuatorResults: cap(
        action.results.filter((result) => result.live), CAPS.actuators,
      ),
    };
  }
  if (action.kind === "select_feed") {
    return action.live ?? isLiveFeed(state, action.feed)
      ? { ...state, activeLive: action.feed }
      : { ...state, active: action.feed };
  }
  if (action.kind === "select_incident") {
    return withFeed(state, state.active, false, (f) => ({
      ...f, highlight: action.incident,
      seekTo: action.incident.olay_baslangic ?? action.incident.t,
      seekNonce: f.seekNonce + 1,
    }));
  }
  if (action.kind === "seek") {
    if (isLiveFeed(state, action.feed)) return state;
    return {
      ...withFeed(state, action.feed, false, (f) => ({
        ...f,
        seekTo: action.timestamp,
        seekNonce: f.seekNonce + 1,
        video: f.video ?? action.video ?? null,
      })),
      active: action.feed,
    };
  }
  if (action.kind === "run_started") {
    return {
      ...withFeed(state, action.feed, false,
                  () => ({ ...emptyFeed, video: action.video })),
      active: action.feed,
      chat: [],
    };
  }

  const { seq, payload: p } = action.event;
  const feed = action.event.feed ?? "";
  const live = action.event.live ?? false;
  switch (p.type) {
    case "window_report":
      return withFeed(state, feed, live, (f) => ({
        ...f, reports: cap([...f.reports, p], CAPS.reports),
      }));
    case "activity_strip":
      return withFeed(state, feed, live, (f) => ({
        ...f, activity: pruneActivity([...f.activity, p]),
      }));
    case "incident_update":
      return withFeed(state, feed, live, (f) => {
        const prev = f.incidentMap.get(p.incident_id);
        const merged: StoredIncident = p.boxes.length > 0
          ? { ...p, boxT: p.t }
          : { ...p, boxes: prev?.boxes ?? [], boxT: prev?.boxT ?? prev?.t };
        const map = new Map(f.incidentMap);
        map.set(p.incident_id, merged);
        let incidents: StoredIncident[];
        if (prev) {
          incidents = f.incidents.map((i) => i.incident_id === p.incident_id ? merged : i);
        } else {
          let idx = f.incidents.findIndex((i) => i.t > merged.t);
          if (idx === -1) idx = f.incidents.length;
          incidents = [...f.incidents.slice(0, idx), merged, ...f.incidents.slice(idx)];
        }
        if (incidents.length > CAPS.incidents) {
          const drop = incidents.length - CAPS.incidents;
          for (const gone of incidents.slice(0, drop)) map.delete(gone.incident_id);
          incidents = incidents.slice(drop);
        }
        return {
          ...f,
          incidentMap: map,
          highlight: f.highlight?.incident_id === p.incident_id ? merged : f.highlight,
          incidents,
        };
      });
    case "agent_step":
      return withFeed(state, feed, live, (f) => ({
        ...f, trace: cap([...f.trace, { seq, kind: "step" as const, step: p }], CAPS.trace),
      }));
    case "tool_call":
      return withFeed(state, feed || (live ? state.activeLive : state.active), live, (f) => ({
        ...f, trace: cap([...f.trace, { seq, kind: "tool" as const, tool: p }], CAPS.trace),
      }));
    case "chat_message": {
      if (live) return state;
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
      const actionLive = live || p.live;
      const request = p.feed || !feed ? p : { ...p, feed };
      if (actionLive) {
        const others = state.liveActuatorRequests.filter(
          (item) => item.request_id !== p.request_id,
        );
        return {
          ...state,
          liveActuatorRequests: cap([...others, request], CAPS.actuators),
        };
      }
      const others = state.actuatorRequests.filter(
        (item) => item.request_id !== p.request_id,
      );
      return { ...state, actuatorRequests: cap([...others, request], CAPS.actuators) };
    }
    case "actuator_result": {
      const actionLive = live || p.live;
      const result = p.feed || !feed ? p : { ...p, feed };
      if (actionLive) {
        const others = state.liveActuatorResults.filter(
          (item) => item.request_id !== p.request_id,
        );
        return {
          ...state,
          liveActuatorResults: cap([...others, result], CAPS.actuators),
        };
      }
      const others = state.actuatorResults.filter(
        (item) => item.request_id !== p.request_id,
      );
      return { ...state, actuatorResults: cap([...others, result], CAPS.actuators) };
    }
    case "run_status": {
      const newRun = p.run_id !== "-" && p.run_id !== state.feeds[feed]?.runStatus?.run_id;
      const next = withFeed(state, feed, live, (f) => {
        const newRun = p.run_id !== "-" && p.run_id !== f.runStatus?.run_id;
        if (newRun) {
          // Canlıda her segment ayrı bir koşudur. Etkinlik şeridi akışın
          // sürekli geçmişidir; segment sınırında sıfırlanmaz.
          return {
            ...emptyFeed,
            live,
            video: p.video || null,
            runStatus: p,
            activity: live ? f.activity : [],
          };
        }
        return { ...f, runStatus: p, video: f.video ?? (p.video || null) };
      });
      return newRun && !live ? { ...next, chat: [] } : next;
    }
    case "ui_command": {
      if (live) return state;
      const target = feed || state.active;
      if (p.action === "seek_video") {
        return withFeed(state, target, false, (f) => ({
          ...f, seekTo: Number(p.args.t ?? 0), seekNonce: f.seekNonce + 1,
        }));
      }
      if (p.action === "highlight_incident") {
        return withFeed(state, target, false, (f) => ({
          ...f,
          highlight: f.incidents.find((i) => i.incident_id === p.args.incident_id) ?? null,
        }));
      }
      if (p.action === "show_report") {
        return withFeed(state, target, false, (f) => ({ ...f, reportsPulse: f.reportsPulse + 1 }));
      }
      return state;
    }
    default:
      return state;
  }
}
