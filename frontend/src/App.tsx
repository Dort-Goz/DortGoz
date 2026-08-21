import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { ActuatorRequest, ActuatorResult, Event, Payload } from "./types/events";
import { DortgozSocket, type ConnectionState } from "./lib/ws";
import VideoPanel from "./components/VideoPanel";
import Timeline from "./components/Timeline";
import AgentTrace from "./components/AgentTrace";
import ChatPanel from "./components/ChatPanel";
import ActionLog from "./components/ActionLog";
import ExperimentPanel, { type InterpretConfig } from "./components/ExperimentPanel";
import FeedStrip from "./components/FeedStrip";
import LiveGrid from "./components/LiveGrid";
import LearningOrchestratorPanel from "./components/LearningOrchestratorPanel";
import UploadPanel from "./components/UploadPanel";
import TrainingReviewPanel from "./components/TrainingReviewPanel";
import TriagePanel from "./components/TriagePanel";
import { includeUploadedVideo, startCanonicalRun } from "./lib/canonicalRun";
import { consoleReducer, emptyFeed, initialState } from "./state";

const EXPERIMENT_KEY = "dortgoz.experiment";

// Bağlantı rozeti: kopukken operatör BAYAT veriye baktığını görmelidir.
const CONNECTION_TR: Record<ConnectionState, string> = {
  connecting: "bağlanıyor…",
  open: "bağlı",
  reconnecting: "bağlantı koptu — yeniden deneniyor",
  closed: "bağlantı kapalı",
};

const CONNECTION_CLS: Record<ConnectionState, string> = {
  connecting: "border-amber-700 text-amber-300 bg-amber-950/40",
  open: "border-emerald-800 text-emerald-300 bg-emerald-950/30",
  reconnecting: "border-red-600 text-red-100 bg-red-900/70 animate-pulse",
  closed: "border-red-700 text-red-200 bg-red-950/60",
};

export default function App() {
  const [state, dispatch] = useReducer(consoleReducer, initialState);
  const socketRef = useRef<DortgozSocket | null>(null);
  const [videos, setVideos] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [interpretCfg, setInterpretCfg] = useState<InterpretConfig | null>(null);
  const [showExperiment, setShowExperiment] = useState(false);
  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [taskPrompt, setTaskPrompt] = useState("");
  const [runMode, setRunMode] = useState<"" | "temkinli" | "genis">("");
  const [demoCount, setDemoCount] = useState(4);
  const startPendingRef = useRef(false);
  const [startPending, setStartPending] = useState(false);
  const [importNote, setImportNote] = useState("");
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [liveView, setLiveView] = useState(false);
  const [trainingEventId, setTrainingEventId] = useState("");
  const [showLearningOrchestrator, setShowLearningOrchestrator] = useState(false);
  const [fixtureMode, setFixtureMode] = useState(false);
  // Sunucu bağlantısı — üst çubuktaki kalıcı rozet
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  useEffect(() => {
    const socket = new DortgozSocket(
      (e: Event) => dispatch({ kind: "event", event: e }),
      () => dispatch({ kind: "sync_reset" }),
      { onState: setConnection },
    );
    socketRef.current = socket;
    return () => socket.close();
  }, []);

  useEffect(() => {
    fetch("/api/videos")
      .then((r) => r.json())
      .then((list: string[]) => {
        setVideos((current) =>
          current.reduce(
            (available, video) => includeUploadedVideo(available, video),
            list,
          ),
        );
        setSelected((s) => s || list[0] || "");
      })
      .catch(() => setVideos([]));
  }, []);

  useEffect(() => {
    fetch("/api/actions")
      .then((r) => r.json())
      .then((body: { requests: ActuatorRequest[]; results: ActuatorResult[] }) => {
        dispatch({
          kind: "hydrate_actions",
          requests: body.requests ?? [],
          results: body.results ?? [],
        });
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    fetch("/health")
      .then((r) => r.json())
      .then((body: { analysis_mode?: string }) => {
        const fixture = body.analysis_mode === "ui_fixture_replay";
        setFixtureMode(fixture);
        if (fixture) setLiveView(false);
      })
      .catch(() => setFixtureMode(false));
  }, []);

  useEffect(() => {
    fetch("/api/interpret_config")
      .then((r) => r.json())
      .then((cfg: InterpretConfig) => {
        setInterpretCfg(cfg);
        let saved: Record<string, string> = {};
        try { saved = JSON.parse(localStorage.getItem(EXPERIMENT_KEY) ?? "{}"); } catch {}
        setModel(saved.model || cfg.default_model);
        setSystemPrompt(saved.system_prompt || cfg.system_prompt);
        setTaskPrompt(saved.task_prompt || cfg.task_prompt);
        if (saved.model || saved.system_prompt || saved.task_prompt) setShowExperiment(true);
      })
      .catch(() => setInterpretCfg(null));
  }, []);

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
      socketRef.current?.send({
        kind: "actuator_response",
        request_id,
        approved,
        operator: localStorage.getItem("dortgoz.reviewer") ?? "",
      }),
  }), []);

  const feed = state.feeds[state.active] ?? emptyFeed;
  const run = feed.runStatus;
  const busy = Object.values(state.feeds).some((f) => f.runStatus?.state === "processing");

  const overrides = useCallback(() => ({
    model: interpretCfg && model !== interpretCfg.default_model ? model : "",
    system_prompt:
      interpretCfg && systemPrompt !== interpretCfg.system_prompt ? systemPrompt : "",
    task_prompt:
      interpretCfg && taskPrompt !== interpretCfg.task_prompt ? taskPrompt : "",
    mode: runMode,
  }), [interpretCfg, model, systemPrompt, taskPrompt, runMode]);

  const startRun = useCallback(() => {
    const started = startCanonicalRun({
      selected,
      busy,
      gate: startPendingRef,
      overrides: overrides(),
      dispatchStarted: (video) => dispatch({ kind: "run_started", video, feed: "" }),
      send: (message) => socketRef.current?.send(message),
    });
    if (started) setStartPending(true);
  }, [selected, busy, overrides]);

  useEffect(() => {
    if (!run?.state) return;
    startPendingRef.current = false;
    setStartPending(false);
  }, [run]);

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

  const importPackage = useCallback(async (file: File) => {
    setImportNote("içe alınıyor…");
    try {
      const r = await fetch("/api/runs/import", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: file,
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail ?? r.statusText);
      setImportNote(`✔ ${body.run_id}: ${body.verdict}`);
    } catch (err) {
      setImportNote(`✖ içe aktarma başarısız: ${(err as Error).message}`);
    }
  }, []);

  return (
    <div className="h-screen flex flex-col gap-2 p-2">
      {fixtureMode && (
        <div className="rounded border border-amber-700 bg-amber-950/50 px-3 py-1.5 text-center text-xs font-bold text-amber-200">
          ARAYÜZ TEST AKIŞI · VİDEOYU SEÇİP “BAŞLAT”A BASIN · VİDEO ANALİZ EDİLMEZ
        </div>
      )}
      <header className="flex items-center flex-wrap gap-x-3 gap-y-1.5 px-3 py-2 rounded-lg border border-zinc-800 bg-zinc-900/60 shrink-0">
        <span className="text-lg font-bold tracking-tight">
          DÖRTGÖZ <span className="text-zinc-500 font-normal text-sm">operatör konsolu</span>
        </span>
        {/* Bağlantı rozeti HER kipte görünür (canlı ızgarada da): kopuk
            bağlantıda ekrandaki her şey bayattır, bunu gizlemek yanıltır. */}
        <span
          title={connection === "open"
            ? "Sunucu bağlantısı açık — olaylar canlı akıyor"
            : "Sunucu bağlantısı yok — ekrandaki veri BAYAT olabilir"}
          className={`rounded px-2 py-1 border text-xs font-medium ${CONNECTION_CLS[connection]}`}
        >
          {connection === "open" ? "●" : "○"} {CONNECTION_TR[connection]}
        </span>
        <div className="ml-auto flex items-center flex-wrap gap-x-3 gap-y-1.5 text-xs text-zinc-400">
          {!fixtureMode && <button
            onClick={() => setLiveView((v) => !v)}
            title="Canlı CCTV ızgarası: config/live_feeds.json'daki gerçek akışlar, işlenme durumu ve gecikme rozetleriyle"
            className={`rounded px-2 py-1 border ${
              liveView
                ? "border-sky-700 text-sky-300 bg-sky-950/40"
                : "border-zinc-700 hover:border-zinc-500"
            }`}
          >
            📡 canlı
          </button>}
          {!fixtureMode && !liveView && feed.highlight && run?.run_id && run.run_id !== "-" && (
            <button
              onClick={() => setTrainingEventId(`${run.run_id}:${feed.highlight!.incident_id}`)}
              disabled={run.state !== "done"}
              title={run.state === "done"
                ? "Seçili olayı insan incelemesine ve kontrollü eğitim verisi hazırlığına aç"
                : "Eğitim verisi hazırlamak için analiz tamamlanmalıdır"}
              className="rounded border border-sky-800 px-2 py-1 text-sky-300 hover:bg-sky-950/40 disabled:cursor-not-allowed disabled:opacity-40"
            >
              ◎ eğitim verisi
            </button>
          )}
          {!fixtureMode && (
            <button
              type="button"
              onClick={() => setShowLearningOrchestrator(true)}
              title="İnsan kapılı öğrenme rotalarını, öncelik kuyruğunu ve gölge kayma gözcüsünü aç"
              className="rounded border border-sky-800 px-2 py-1 text-sky-300 hover:bg-sky-950/40"
            >
              ◈ öğrenme
            </button>
          )}
          {interpretCfg && !liveView && !fixtureMode && (
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
          {!liveView && (
            <UploadPanel onUploaded={(video) => {
              setVideos((current) => includeUploadedVideo(current, video.stored_filename));
              setSelected(video.stored_filename);
            }} />
          )}
          <input
            ref={importInputRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) importPackage(file);
              e.target.value = "";
            }}
          />
          <button
            onClick={() => importInputRef.current?.click()}
            title="Dışa aktarılmış analiz paketini (.zip) içe al — sohbet paket üzerinde tam yetenekle çalışır"
            className="rounded px-2 py-1 border border-zinc-700 hover:border-zinc-500"
          >
            ⇪ paket al
          </button>
          {importNote && (
            <button
              onClick={() => setImportNote("")}
              title={`${importNote} — kapatmak için tıklayın`}
              className="max-w-72 truncate text-zinc-400 hover:text-zinc-200"
            >
              {importNote}
            </button>
          )}
          {!liveView && (<>
          {!fixtureMode && <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={busy || videos.length === 0}
            className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 max-w-56
                       disabled:opacity-50"
          >
            {videos.length === 0 && <option value="">media/ boş</option>}
            {videos.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>}
          <select
            value={runMode}
            onChange={(e) => setRunMode(e.target.value as "" | "temkinli" | "genis")}
            disabled={busy}
            title="Çalışma kipi — dengeli: varsayılan; temkinli: alarm ikinci okumayla doğrulanır (az yanlış alarm); geniş: çift okuma + son tarama (en yüksek yakalama)"
            className="bg-zinc-800 border border-zinc-700 rounded px-1 py-1 disabled:opacity-50"
          >
            <option value="">dengeli</option>
            <option value="temkinli">temkinli</option>
            <option value="genis">geniş</option>
          </select>
          <button
            onClick={busy ? stopRun : startRun}
            disabled={(!selected && !busy) || (startPending && !busy)}
            className={`rounded px-3 py-1 font-medium disabled:opacity-40 ${
              busy ? "bg-red-600 hover:bg-red-500" : "bg-emerald-600 hover:bg-emerald-500"
            } text-white`}
          >
            {busy ? "Durdur" : startPending ? "Başlatılıyor…" : "Başlat"}
          </button>
          {!fixtureMode && !busy && videos.length > 1 && (
            <span className="flex items-center gap-1">
              <button
                onClick={() => startDemo(demoCount)}
                title="Çoklu kamera demosu: N akış eşzamanlı çözümlenir (24 = şartname senaryosu)"
                className="rounded px-2 py-1 border border-indigo-800 text-indigo-300
                           hover:bg-indigo-950/40"
              >
                ⊞ demo
              </button>
              <select
                value={demoCount}
                onChange={(e) => setDemoCount(Number(e.target.value))}
                className="bg-zinc-800 border border-zinc-700 rounded px-1 py-1"
              >
                {[2, 4, 8, 12, 24].map((n) => <option key={n} value={n}>×{n}</option>)}
              </select>
            </span>
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
              {!fixtureMode && run.state === "done" && run.run_id !== "-" && (
                <a
                  href={`/api/runs/${run.run_id}/export`}
                  download
                  title="Analizi taşınabilir paket (.zip) olarak indir: akış + özet + video + kanıt kareleri"
                  className="rounded px-2 py-1 border border-zinc-700 hover:border-zinc-500"
                >
                  ⇩ paket
                </a>
              )}
              {run.detail && (
                <span className="text-zinc-500 max-w-56 truncate" title={run.detail}>
                  {run.detail}
                </span>
              )}
            </>
          )}
          </>)}
        </div>
      </header>

      {!fixtureMode && showExperiment && interpretCfg && (
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

      {!liveView && (
        <FeedStrip feeds={state.feeds} active={state.active}
                   onSelect={(f) => dispatch({ kind: "select_feed", feed: f })} />
      )}

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

      {liveView && (
        <LiveGrid
          incidents={Object.fromEntries(
            Object.entries(state.feeds).map(([f, s]) => [f, s.incidents]))}
          onSelectFeed={(f) => dispatch({ kind: "select_feed", feed: f })}
          onOpenTraining={setTrainingEventId}
        />
      )}

      {!liveView && (
      <div className="flex-1 flex gap-2 min-h-0">
      <div className="flex-1 grid grid-cols-6 grid-rows-2 gap-2 min-h-0">
        <div className="col-span-2 row-span-1 min-h-0">
          <VideoPanel
            highlight={feed.highlight}
            seekTo={feed.seekTo}
            video={feed.video}
            feed={Object.keys(state.feeds).filter((k) => k !== "").length >= 2
              ? state.active : null}
          />
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
      {!fixtureMode && (
        <TriagePanel
          title="Olay İnceleme Merkezi"
          scopeFeed={state.active}
          onSelectFeed={(selectedFeed) =>
            dispatch({ kind: "select_feed", feed: selectedFeed })}
          onSeek={(selectedFeed, timestamp, reviewVideo) =>
            dispatch({
              kind: "seek",
              feed: selectedFeed,
              timestamp,
              video: reviewVideo,
            })}
          onOpenTraining={setTrainingEventId}
        />
      )}
      </div>
      )}
      {trainingEventId && (
        <TrainingReviewPanel
          eventId={trainingEventId}
          onClose={() => setTrainingEventId("")}
        />
      )}
      {showLearningOrchestrator && (
        <LearningOrchestratorPanel
          onClose={() => setShowLearningOrchestrator(false)}
          onOpenEvent={(eventId) => {
            setShowLearningOrchestrator(false);
            setTrainingEventId(eventId);
          }}
        />
      )}
    </div>
  );
}

export type { Payload };
