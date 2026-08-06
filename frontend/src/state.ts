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

/** Tek bir akışın (kameranın) durumu. Tek-akış kipinde anahtar "" kullanılır. */
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

/** Konsol durumu: akış başına kova + akışlar-üstü sohbet/aksiyonlar.
 *  Sohbet küresel çünkü ajan TÜM kameraların brifingiyle konuşur (demo kipi);
 *  aktüatör istek/sonuçları da akıştan bağımsız operatör kuyruğudur. */
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

export type Action =
  | { kind: "event"; event: Event }
  | { kind: "run_started"; video: string; feed: string }
  | { kind: "select_incident"; incident: IncidentUpdate }
  | { kind: "select_feed"; feed: string };

function withFeed(state: ConsoleState, feed: string,
                  patch: (f: FeedState) => FeedState): ConsoleState {
  const cur = state.feeds[feed] ?? emptyFeed;
  // İlk kova açılırken etkin akış boşsa oraya odaklan (yenilenen sayfa,
  // 2. operatör: olaylar hangi akıştan geliyorsa onu göster)
  const active = state.feeds[state.active] || state.active === feed
    ? state.active : feed;
  return { ...state, active, feeds: { ...state.feeds, [feed]: patch(cur) } };
}

export function consoleReducer(state: ConsoleState, action: Action): ConsoleState {
  if (action.kind === "select_feed") {
    return { ...state, active: action.feed };
  }
  if (action.kind === "select_incident") {
    // Operatör olaya tıkladı → videoyu o ana sar + kutuları vurgula.
    // (Ajan da aynı duruma `ui_command` ile ulaşır — iki yol tek state'e bağlı.)
    return withFeed(state, state.active, (f) => ({
      ...f, highlight: action.incident, seekTo: action.incident.t,
    }));
  }
  if (action.kind === "run_started") {
    // Yeni koşu önceki koşunun kalıntılarını taşımamalı (akış bazında)
    return {
      ...withFeed(state, action.feed, () => ({ ...emptyFeed, video: action.video })),
      active: action.feed,
    };
  }

  const { seq, payload: p } = action.event;
  const feed = action.event.feed ?? "";
  switch (p.type) {
    case "window_report":
      return withFeed(state, feed, (f) => ({ ...f, reports: [...f.reports, p] }));
    case "incident_update":
      return withFeed(state, feed, (f) => {
        const others = f.incidents.filter((i) => i.incident_id !== p.incident_id);
        return { ...f, incidents: [...others, p].sort((a, b) => a.t - b.t) };
      });
    case "agent_step":
      return withFeed(state, feed, (f) => ({
        ...f, trace: [...f.trace, { seq, kind: "step", step: p }],
      }));
    case "tool_call":
      // Araç çağrıları sohbet ajanından gelir (akışsız) → etkin akışın izine
      return withFeed(state, feed || state.active, (f) => ({
        ...f, trace: [...f.trace, { seq, kind: "tool", tool: p }],
      }));
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
      return withFeed(state, feed, (f) => {
        // Koşuyu BAŞLATMAYAN istemci (sayfa yenileme, 2. operatör) videoyu ve
        // yeni koşuyu buradan öğrenir (2026-08-05 QA bulgusu).
        const newRun = p.run_id !== "-" && p.run_id !== f.runStatus?.run_id;
        if (newRun && !f.video) {
          return { ...emptyFeed, video: p.video || null, runStatus: p };
        }
        return { ...f, runStatus: p, video: f.video ?? (p.video || null) };
      });
    case "ui_command": {
      // Ajan araçları etkin akışın konsolunu yönlendirir
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
