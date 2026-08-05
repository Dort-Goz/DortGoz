import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Event, Payload } from "./types/events";
import { DortgozSocket } from "./lib/ws";
import VideoPanel from "./components/VideoPanel";
import Timeline from "./components/Timeline";
import AgentTrace from "./components/AgentTrace";
import ChatPanel from "./components/ChatPanel";
import ActionLog from "./components/ActionLog";
import ExperimentPanel, { type InterpretConfig } from "./components/ExperimentPanel";
import { consoleReducer, initialState } from "./state";

const EXPERIMENT_KEY = "dortgoz.experiment";

export default function App() {
  const [state, dispatch] = useReducer(consoleReducer, initialState);
  const socketRef = useRef<DortgozSocket | null>(null);
  const [videos, setVideos] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  // Deney paneli: model + istem override'ları (boş = henüz yüklenmedi)
  const [interpretCfg, setInterpretCfg] = useState<InterpretConfig | null>(null);
  const [showExperiment, setShowExperiment] = useState(false);
  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [taskPrompt, setTaskPrompt] = useState("");

  useEffect(() => {
    const socket = new DortgozSocket((e: Event) => dispatch({ kind: "event", event: e }));
    socketRef.current = socket;
    return () => socket.close();
  }, []);

  // İşlenebilir klipler — backend /media altını tarar
  useEffect(() => {
    fetch("/api/videos")
      .then((r) => r.json())
      .then((list: string[]) => {
        setVideos(list);
        setSelected((s) => s || list[0] || "");
      })
      .catch(() => setVideos([]));
  }, []);

  // Deney varsayılanları + localStorage'daki override'lar (iterasyon reload'a dayansın)
  useEffect(() => {
    fetch("/api/interpret_config")
      .then((r) => r.json())
      .then((cfg: InterpretConfig) => {
        setInterpretCfg(cfg);
        let saved: Record<string, string> = {};
        try { saved = JSON.parse(localStorage.getItem(EXPERIMENT_KEY) ?? "{}"); } catch { /* bozuk kayıt → varsayılan */ }
        setModel(saved.model || cfg.default_model);
        setSystemPrompt(saved.system_prompt || cfg.system_prompt);
        setTaskPrompt(saved.task_prompt || cfg.task_prompt);
        if (saved.model || saved.system_prompt || saved.task_prompt) setShowExperiment(true);
      })
      .catch(() => setInterpretCfg(null));
  }, []);

  // Yalnız varsayılandan sapanlar saklanır — varsayılan değişirse eskisi yapışıp kalmasın
  useEffect(() => {
    if (!interpretCfg) return;
    localStorage.setItem(EXPERIMENT_KEY, JSON.stringify({
      model: model !== interpretCfg.default_model ? model : "",
      system_prompt: systemPrompt !== interpretCfg.system_prompt ? systemPrompt : "",
      task_prompt: taskPrompt !== interpretCfg.task_prompt ? taskPrompt : "",
    }));
  }, [model, systemPrompt, taskPrompt, interpretCfg]);

  const send = useMemo(() => ({
    chat: (text: string) => socketRef.current?.send({ kind: "chat", text }),
    actuator: (request_id: string, approved: boolean) =>
      socketRef.current?.send({ kind: "actuator_response", request_id, approved }),
  }), []);

  const run = state.runStatus;
  const busy = run?.state === "processing";

  const startRun = useCallback(() => {
    if (!selected || busy) return;
    dispatch({ kind: "run_started", video: selected });
    // Yalnız varsayılandan sapan alanlar gönderilir (boş = backend varsayılanı)
    socketRef.current?.send({
      kind: "start_run",
      video: selected,
      model: interpretCfg && model !== interpretCfg.default_model ? model : "",
      system_prompt:
        interpretCfg && systemPrompt !== interpretCfg.system_prompt ? systemPrompt : "",
      task_prompt:
        interpretCfg && taskPrompt !== interpretCfg.task_prompt ? taskPrompt : "",
    });
  }, [selected, busy, interpretCfg, model, systemPrompt, taskPrompt]);

  const stopRun = useCallback(() => socketRef.current?.send({ kind: "stop_run" }), []);

  return (
    <div className="h-screen flex flex-col gap-2 p-2">
      {/* Üst çubuk */}
      <header className="flex items-center gap-3 px-3 py-2 rounded-lg border border-zinc-800 bg-zinc-900/60 shrink-0">
        <span className="text-lg font-bold tracking-tight">
          DÖRTGÖZ <span className="text-zinc-500 font-normal text-sm">operatör konsolu</span>
        </span>
        <div className="ml-auto flex items-center gap-3 text-xs text-zinc-400">
          {interpretCfg && (
            <button
              onClick={() => setShowExperiment((s) => !s)}
              title="Model ve istem deneyleri"
              className={`rounded px-2 py-1 border ${
                showExperiment
                  ? "border-amber-700 text-amber-300 bg-amber-950/40"
                  : "border-zinc-700 hover:border-zinc-500"
              }`}
            >
              ⚗ deney
              {(model !== interpretCfg.default_model ||
                systemPrompt !== interpretCfg.system_prompt ||
                taskPrompt !== interpretCfg.task_prompt) && (
                <span className="ml-1 text-amber-400">●</span>
              )}
            </button>
          )}
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={busy || videos.length === 0}
            className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 max-w-56
                       disabled:opacity-50"
          >
            {videos.length === 0 && <option value="">media/ boş</option>}
            {videos.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <button
            onClick={busy ? stopRun : startRun}
            disabled={!selected && !busy}
            className={`rounded px-3 py-1 font-medium disabled:opacity-40 ${
              busy ? "bg-red-600 hover:bg-red-500" : "bg-emerald-600 hover:bg-emerald-500"
            } text-white`}
          >
            {busy ? "Durdur" : "Başlat"}
          </button>
          {run && (
            <>
              <div className="w-32 h-1.5 rounded bg-zinc-800 overflow-hidden">
                <div
                  className="h-full bg-emerald-500 transition-all"
                  style={{ width: `${Math.round(run.progress * 100)}%` }}
                />
              </div>
              <span className={busy ? "text-emerald-400" : ""}>{run.state}</span>
              {run.detail && <span className="text-zinc-500">{run.detail}</span>}
            </>
          )}
        </div>
      </header>

      {/* Deney paneli — model + istemler, koşu başına (bkz. ExperimentPanel) */}
      {showExperiment && interpretCfg && (
        <ExperimentPanel
          config={interpretCfg}
          model={model}
          systemPrompt={systemPrompt}
          taskPrompt={taskPrompt}
          busy={busy}
          onModel={setModel}
          onSystemPrompt={setSystemPrompt}
          onTaskPrompt={setTaskPrompt}
        />
      )}

      {/* Koşunun nihai kararı — hangi sınıf, hangi risk (backend: RunContext.verdict) */}
      {run?.state === "done" && run.detail && (
        <div className="shrink-0 rounded-lg border border-emerald-900/60 bg-emerald-950/30
                        px-3 py-2 text-sm text-emerald-200 flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide font-bold text-emerald-500">
            Karar
          </span>
          {run.detail}
        </div>
      )}

      {/* Ana ızgara */}
      <div className="flex-1 grid grid-cols-3 grid-rows-2 gap-2 min-h-0">
        <div className="col-span-2 row-span-1 min-h-0">
          <VideoPanel highlight={state.highlight} seekTo={state.seekTo} video={state.video} />
        </div>
        <div className="min-h-0">
          <Timeline
            incidents={state.incidents}
            reports={state.reports}
            highlightId={state.highlight?.incident_id}
            onSelect={(incident) => dispatch({ kind: "select_incident", incident })}
          />
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
