import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Event, Payload } from "./types/events";
import { DortgozSocket } from "./lib/ws";
import VideoPanel from "./components/VideoPanel";
import Timeline from "./components/Timeline";
import AgentTrace from "./components/AgentTrace";
import ChatPanel from "./components/ChatPanel";
import ActionLog from "./components/ActionLog";
import ExperimentPanel, { type InterpretConfig } from "./components/ExperimentPanel";
import FeedStrip from "./components/FeedStrip";
import { consoleReducer, emptyFeed, initialState } from "./state";

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

  const feed = state.feeds[state.active] ?? emptyFeed;
  const run = feed.runStatus;
  // "Durdur" TÜM akışları keser → meşguliyet de tüm akışlara bakar
  const busy = Object.values(state.feeds).some((f) => f.runStatus?.state === "processing");

  const overrides = useCallback(() => ({
    // Yalnız varsayılandan sapan alanlar gönderilir (boş = backend varsayılanı)
    model: interpretCfg && model !== interpretCfg.default_model ? model : "",
    system_prompt:
      interpretCfg && systemPrompt !== interpretCfg.system_prompt ? systemPrompt : "",
    task_prompt:
      interpretCfg && taskPrompt !== interpretCfg.task_prompt ? taskPrompt : "",
  }), [interpretCfg, model, systemPrompt, taskPrompt]);

  const startRun = useCallback(() => {
    if (!selected || busy) return;
    dispatch({ kind: "run_started", video: selected, feed: "" });
    socketRef.current?.send({ kind: "start_run", video: selected, ...overrides() });
  }, [selected, busy, overrides]);

  // Demo: KAM-1..N etiketleriyle EŞZAMANLI koşar (kapasite ~10 @1×).
  // Uzun `kamera*` kayıtları (make_long_feed üretimi) öncelikli — demo kısa
  // kliplerle değil, gerçekçi sürekli akışlarla anlamlı.
  const startDemo = useCallback((count: number) => {
    if (busy || videos.length === 0) return;
    const long = videos.filter((v) => v.toLowerCase().startsWith("kamera"));
    const pool = long.length >= 2 ? long : videos;
    const picks = Array.from({ length: count }, (_, i) => pool[i % pool.length]);
    picks.forEach((video, i) => {
      const feedName = `KAM-${i + 1}`;
      dispatch({ kind: "run_started", video, feed: feedName });
      socketRef.current?.send({ kind: "start_run", video, feed: feedName, ...overrides() });
    });
  }, [busy, videos, overrides]);

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
          {!busy && videos.length > 1 && (
            <button
              onClick={() => startDemo(Math.min(4, videos.length))}
              title="Çoklu kamera demosu: ilk 4 klip eşzamanlı çözümlenir"
              className="rounded px-2 py-1 border border-indigo-800 text-indigo-300
                         hover:bg-indigo-950/40"
            >
              ⊞ demo ×4
            </button>
          )}
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

      {/* Koşunun nihai kararı — hangi sınıf, hangi risk (backend: RunContext.verdict).
          Renk EN CİDDİ olayın riskini taşır: yeşil kutu içinde "kritik" yazması
          operatörü yanıltıyordu; olaysız koşu yeşil kalır. */}
      {/* Çoklu-akış (demo) kamera duvarı — tek akışta görünmez */}
      <FeedStrip feeds={state.feeds} active={state.active}
                 onSelect={(f) => dispatch({ kind: "select_feed", feed: f })} />

      {run?.state === "done" && run.detail && (() => {
        const order = ["dusuk", "orta", "yuksek", "kritik"] as const;
        const worst = feed.incidents.reduce<string | null>(
          (w, i) => (w === null || order.indexOf(i.risk) > order.indexOf(w as any) ? i.risk : w),
          null);
        const tone = worst === null
          ? "border-emerald-900/60 bg-emerald-950/30 text-emerald-200"
          : worst === "dusuk" ? "border-sky-900/60 bg-sky-950/30 text-sky-200"
          : worst === "orta" ? "border-amber-800/60 bg-amber-950/30 text-amber-200"
          : "border-red-800/60 bg-red-950/30 text-red-200";
        return (
          <div className={`shrink-0 rounded-lg border px-3 py-2 text-sm
                           flex items-center gap-2 ${tone}`}>
            <span className="text-[10px] uppercase tracking-wide font-bold opacity-80">
              Karar
            </span>
            {run.detail}
          </div>
        );
      })()}

      {/* Ana ızgara */}
      {/* 6 sütun: video 1:1 içerik taşıdığı için DAR bir sütuna oturur (2/6);
          artan genişlik zaman çizelgesine gider (4/6) — orada okunacak metin var.
          Alt sıra üçe eşit bölünür. */}
      <div className="flex-1 grid grid-cols-6 grid-rows-2 gap-2 min-h-0">
        <div className="col-span-2 row-span-1 min-h-0">
          <VideoPanel highlight={feed.highlight} seekTo={feed.seekTo} video={feed.video} />
        </div>
        <div className="col-span-4 min-h-0">
          <Timeline
            incidents={feed.incidents}
            reports={feed.reports}
            highlightId={feed.highlight?.incident_id}
            onSelect={(incident) => dispatch({ kind: "select_incident", incident })}
          />
        </div>
        <div className="col-span-2 min-h-0">
          <AgentTrace entries={feed.trace} />
        </div>
        <div className="col-span-2 min-h-0">
          <ChatPanel messages={state.chat} onSend={send.chat} />
        </div>
        <div className="col-span-2 min-h-0">
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
