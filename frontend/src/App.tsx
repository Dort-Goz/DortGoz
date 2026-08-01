import { useEffect, useMemo, useReducer, useRef } from "react";
import type { Event, Payload } from "./types/events";
import { DortgozSocket } from "./lib/ws";
import VideoPanel from "./components/VideoPanel";
import Timeline from "./components/Timeline";
import AgentTrace from "./components/AgentTrace";
import ChatPanel from "./components/ChatPanel";
import ActionLog from "./components/ActionLog";
import { consoleReducer, initialState } from "./state";

export default function App() {
  const [state, dispatch] = useReducer(consoleReducer, initialState);
  const socketRef = useRef<DortgozSocket | null>(null);

  useEffect(() => {
    const socket = new DortgozSocket((e: Event) => dispatch({ kind: "event", event: e }));
    socketRef.current = socket;
    return () => socket.close();
  }, []);

  const send = useMemo(() => ({
    chat: (text: string) => socketRef.current?.send({ kind: "chat", text }),
    actuator: (request_id: string, approved: boolean) =>
      socketRef.current?.send({ kind: "actuator_response", request_id, approved }),
  }), []);

  const run = state.runStatus;

  return (
    <div className="h-screen flex flex-col gap-2 p-2">
      {/* Üst çubuk */}
      <header className="flex items-center gap-3 px-3 py-2 rounded-lg border border-zinc-800 bg-zinc-900/60 shrink-0">
        <span className="text-lg font-bold tracking-tight">
          DÖRTGÖZ <span className="text-zinc-500 font-normal text-sm">operatör konsolu</span>
        </span>
        <div className="ml-auto flex items-center gap-3 text-xs text-zinc-400">
          {run && (
            <>
              <span>{run.detail}</span>
              <div className="w-32 h-1.5 rounded bg-zinc-800 overflow-hidden">
                <div
                  className="h-full bg-emerald-500 transition-all"
                  style={{ width: `${Math.round(run.progress * 100)}%` }}
                />
              </div>
              <span className={run.state === "processing" ? "text-emerald-400" : ""}>
                {run.state}
              </span>
            </>
          )}
        </div>
      </header>

      {/* Ana ızgara */}
      <div className="flex-1 grid grid-cols-3 grid-rows-2 gap-2 min-h-0">
        <div className="col-span-2 row-span-1 min-h-0">
          <VideoPanel highlight={state.highlight} seekTo={state.seekTo} />
        </div>
        <div className="min-h-0">
          <Timeline incidents={state.incidents} highlightId={state.highlight?.incident_id} />
        </div>
        <div className="min-h-0">
          <AgentTrace entries={state.trace} />
        </div>
        <div className="min-h-0">
          <ChatPanel messages={state.chat} onSend={send.chat} />
        </div>
        <div className="min-h-0">
          <ActionLog
            requests={state.actuatorRequests}
            results={state.actuatorResults}
            onRespond={send.actuator}
          />
        </div>
      </div>
    </div>
  );
}

export type { Payload };
