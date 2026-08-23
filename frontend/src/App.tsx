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
  connecting: "bağlanıyor",
  open: "bağlı",
  reconnecting: "koptu — yeniden deneniyor",
  closed: "bağlantı kapalı",
};

const CONNECTION_CLS: Record<ConnectionState, string> = {
  connecting: "border-amber-900 bg-amber-950/40 text-amber-300",
  open: "border-emerald-900 bg-emerald-950/30 text-emerald-300",
  reconnecting: "border-red-700 bg-red-900/70 text-red-100 animate-pulse",
  closed: "border-red-900 bg-red-950/60 text-red-300",
};

const RUN_STATE_TR: Record<string, string> = {
  idle: "beklemede",
  processing: "işleniyor",
  done: "tamamlandı",
  error: "hata",
};

const RUN_STATE_CLS: Record<string, string> = {
  idle: "border-zinc-700 text-zinc-400",
  processing: "border-emerald-800 bg-emerald-950/30 text-emerald-300",
  done: "border-sky-900 bg-sky-950/30 text-sky-300",
  error: "border-red-800 bg-red-950/40 text-red-300",
};

const RISK_ORDER = ["dusuk", "orta", "yuksek", "kritik"] as const;

const VERDICT_TONE: Record<string, string> = {
  none: "text-emerald-300",
  dusuk: "text-sky-300",
  orta: "text-amber-300",
  yuksek: "text-red-300",
  kritik: "text-red-300",
};

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="shrink-0 font-mono text-xs text-zinc-500">
      {now.toLocaleTimeString("tr-TR")}
    </span>
  );
}

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
  const [liveView, setLiveView] = useState(() => location.hash === "#canli");
  const [reviewView, setReviewView] = useState(() => location.hash === "#inceleme");
  const [trainingEventId, setTrainingEventId] = useState("");
  const [showLearningOrchestrator, setShowLearningOrchestrator] = useState(false);
  const [fixtureMode, setFixtureMode] = useState(false);
  const [triagePending, setTriagePending] = useState(0);
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
        if (fixture) {
          setLiveView(false);
          setReviewView(false);
        }
      })
      .catch(() => setFixtureMode(false));
  }, []);

  useEffect(() => {
    if (fixtureMode) return;
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch("/api/triage");
        if (!r.ok) return;
        const body = await r.json();
        if (alive) setTriagePending((body.pending ?? []).length);
      } catch {}
    };
    poll();
    const id = setInterval(poll, 10000);
    return () => { alive = false; clearInterval(id); };
  }, [fixtureMode]);

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
  const workspace = liveView ? "live" : reviewView ? "review" : "analysis";

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

  const runState = run?.state ?? "idle";
  const progressPct = Math.round((run?.progress ?? 0) * 100);
  const reviewCount = feed.incidents.filter((i) => i.needs_review).length;
  const worstRisk = feed.incidents.reduce<string | null>(
    (w, i) =>
      w === null || RISK_ORDER.indexOf(i.risk) > RISK_ORDER.indexOf(w as typeof RISK_ORDER[number])
        ? i.risk
        : w,
    null,
  );
  const verdictReady = runState === "done" && Boolean(run?.detail);
  const verdictTone = VERDICT_TONE[worstRisk ?? "none"] ?? "text-red-300";
  const experimentDirty = Boolean(interpretCfg && (
    model !== interpretCfg.default_model ||
    systemPrompt !== interpretCfg.system_prompt ||
    taskPrompt !== interpretCfg.task_prompt
  ));
  const detailReady = Boolean(
    feed.highlight && run?.run_id && run.run_id !== "-" && run.state === "done",
  );

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {fixtureMode && (
        <div className="flex h-8 shrink-0 items-center justify-center border-b border-amber-900/60 bg-amber-950/40 px-3 text-[11px] font-bold tracking-wide text-amber-200">
          ARAYÜZ TEST AKIŞI · VİDEOYU SEÇİP “BAŞLAT”A BASIN · VİDEO ANALİZ EDİLMEZ
        </div>
      )}

      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-zinc-800 bg-zinc-900 px-3">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-bold tracking-widest text-zinc-100">DÖRTGÖZ</span>
          <span className="microlabel hidden sm:inline">operatör konsolu</span>
        </div>

        {!fixtureMode && (
          <nav
            aria-label="Çalışma alanları"
            className="flex h-7 items-center gap-0.5 rounded-sm border border-zinc-800 bg-zinc-950 p-0.5 text-xs"
          >
            {([
              ["analysis", "Analiz"],
              ["live", "Canlı"],
              ["review", "Olay inceleme"],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setLiveView(value === "live");
                  setReviewView(value === "review");
                  history.replaceState(null, "",
                    value === "live" ? "#canli" : value === "review" ? "#inceleme" : "#");
                }}
                className={`h-full rounded-[3px] px-2.5 transition-colors ${
                  workspace === value
                    ? "bg-zinc-800 font-medium text-zinc-100"
                    : "text-zinc-500 hover:text-zinc-200"
                }`}
              >
                {label}
                {value === "review" && triagePending > 0 && (
                  <span className="ml-1.5 inline-flex min-w-4 items-center justify-center rounded-sm bg-amber-800 px-1 font-mono text-[10px] leading-4 text-amber-100">
                    {triagePending}
                  </span>
                )}
              </button>
            ))}
          </nav>
        )}

        <div className="flex-1" />

        {!fixtureMode && (
          <button
            type="button"
            onClick={() => setShowLearningOrchestrator(true)}
            title="İnsan kapılı öğrenme rotalarını, öncelik kuyruğunu ve gölge kayma gözcüsünü aç"
            className="btn btn-ghost"
          >
            ◈ öğrenme merkezi
          </button>
        )}

        {/* Rozet HER kipte görünür: kopuk bağlantıda ekrandaki her şey bayattır. */}
        <span
          title={connection === "open"
            ? "Sunucu bağlantısı açık — olaylar canlı akıyor"
            : "Sunucu bağlantısı yok — ekrandaki veri BAYAT olabilir"}
          className={`chip border font-semibold uppercase tracking-wide ${CONNECTION_CLS[connection]}`}
        >
          {connection === "open" ? "●" : "○"} {CONNECTION_TR[connection]}
        </span>

        <Clock />
      </header>

      {workspace === "analysis" && (
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-zinc-800 bg-zinc-900 px-3">
          {!fixtureMode && (
            <>
              <span className="microlabel">kaynak</span>
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                disabled={busy || videos.length === 0}
                className="field w-52"
              >
                {videos.length === 0 && <option value="">media/ boş</option>}
                {videos.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </>
          )}
          <select
            value={runMode}
            onChange={(e) => setRunMode(e.target.value as "" | "temkinli" | "genis")}
            disabled={busy}
            title="Çalışma kipi — dengeli: varsayılan; temkinli: alarm ikinci okumayla doğrulanır (az yanlış alarm); geniş: çift okuma + son tarama (en yüksek yakalama)"
            className="field"
          >
            <option value="">dengeli</option>
            <option value="temkinli">temkinli</option>
            <option value="genis">geniş</option>
          </select>
          <button
            onClick={busy ? stopRun : startRun}
            disabled={(!selected && !busy) || (startPending && !busy)}
            className={`btn w-28 ${busy ? "btn-danger" : "btn-primary"}`}
          >
            {busy ? "Durdur" : startPending ? "Başlatılıyor…" : "Başlat"}
          </button>
          {!fixtureMode && videos.length > 1 && (
            <span className="flex items-center gap-1">
              <button
                onClick={() => startDemo(demoCount)}
                disabled={busy}
                title="Çoklu kamera demosu: N akış eşzamanlı çözümlenir (24 = şartname senaryosu)"
                className="btn btn-outline-accent"
              >
                ⊞ demo
              </button>
              <select
                value={demoCount}
                onChange={(e) => setDemoCount(Number(e.target.value))}
                disabled={busy}
                className="field w-16"
              >
                {[2, 4, 8, 12, 24].map((n) => <option key={n} value={n}>×{n}</option>)}
              </select>
            </span>
          )}

          <div className="min-w-0 flex-1">
            {importNote && (
              <button
                onClick={() => setImportNote("")}
                title={`${importNote} — kapatmak için tıklayın`}
                className="block max-w-full truncate text-xs text-zinc-400 hover:text-zinc-200"
              >
                {importNote}
              </button>
            )}
          </div>

          {!fixtureMode && (
            <button
              onClick={() => {
                if (feed.highlight && run?.run_id) {
                  setTrainingEventId(`${run.run_id}:${feed.highlight.incident_id}`);
                }
              }}
              disabled={!detailReady}
              title={detailReady
                ? "Seçili olayı insan incelemesine ve kontrollü eğitim verisi hazırlığına aç"
                : "Önce analiz tamamlanmalı ve zaman çizelgesinden bir olay seçilmelidir"}
              className="btn btn-outline-accent"
            >
              ◎ ayrıntılı incele
            </button>
          )}
          {interpretCfg && !fixtureMode && (
            <button
              onClick={() => setShowExperiment((s) => !s)}
              title="Model ve istem deneyleri"
              className={`btn ${showExperiment ? "btn-outline-warn" : "btn-outline"}`}
            >
              ⚗ deney
              {experimentDirty && <span className="text-amber-400">●</span>}
            </button>
          )}
          <UploadPanel onUploaded={(video) => {
            setVideos((current) => includeUploadedVideo(current, video.stored_filename));
            setSelected(video.stored_filename);
          }} />
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
            className="btn btn-outline"
          >
            ⇪ paket al
          </button>
        </div>
      )}

      {workspace === "analysis" && (
        <div className="flex h-8 shrink-0 items-center gap-2 border-b border-zinc-800 bg-zinc-900/60 px-3 text-xs">
          <span
            className={`chip w-24 justify-center border font-semibold uppercase tracking-wide ${
              RUN_STATE_CLS[runState] ?? RUN_STATE_CLS.idle
            }`}
          >
            {RUN_STATE_TR[runState] ?? runState}
          </span>
          <div className="h-1 w-36 shrink-0 overflow-hidden rounded-full bg-zinc-800">
            <div
              className={`h-full ${runState === "error" ? "bg-red-500" : "bg-emerald-500"}`}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <span className="w-10 shrink-0 font-mono text-[11px] text-zinc-500">
            %{progressPct}
          </span>
          {(run?.speed ?? 0) > 0 && runState === "processing" && (
            <span
              className={`chip border font-mono font-semibold ${
                (run?.speed ?? 0) >= 1
                  ? "border-emerald-900 text-emerald-300"
                  : "border-red-900 text-red-300"
              }`}
              title="İşleme hızı — ×1 üstü gerçek zamandan hızlı"
            >
              ×{(run?.speed ?? 0) >= 10 ? (run?.speed ?? 0).toFixed(0) : (run?.speed ?? 0).toFixed(1)}
            </span>
          )}

          <div className="min-w-0 flex-1 truncate" title={run?.detail || undefined}>
            {verdictReady ? (
              <>
                <span className="microlabel mr-2">karar</span>
                <span className={`font-medium ${verdictTone}`}>{run?.detail}</span>
              </>
            ) : (
              <span className="text-zinc-500">{run?.detail ?? ""}</span>
            )}
          </div>

          {!fixtureMode && runState === "done" && run && run.run_id !== "-" && (
            <a
              href={`/api/runs/${run.run_id}/export`}
              download
              title="Analizi taşınabilir paket (.zip) olarak indir: akış + özet + video + kanıt kareleri"
              className="btn btn-outline h-6"
            >
              ⇩ paket
            </a>
          )}
          {reviewCount > 0 && (
            <span className="chip border border-amber-900 bg-amber-950/40 text-amber-300">
              ⚑ {reviewCount} inceleme
            </span>
          )}
          <span className="chip border border-zinc-800 text-zinc-400">
            {feed.incidents.length} olay
          </span>
        </div>
      )}

      {!fixtureMode && workspace === "analysis" && showExperiment && interpretCfg && (
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

      {workspace === "analysis" && (
        <FeedStrip feeds={state.feeds} active={state.active}
                   onSelect={(f) => dispatch({ kind: "select_feed", feed: f })} />
      )}

      {workspace === "live" && (
        <div className="flex min-h-0 flex-1 flex-col p-1.5">
          <LiveGrid
            incidents={Object.fromEntries(
              Object.entries(state.feeds).map(([f, s]) => [f, s.incidents]))}
            onSelectFeed={(f) => dispatch({ kind: "select_feed", feed: f })}
            onOpenTraining={setTrainingEventId}
          />
        </div>
      )}

      {workspace === "analysis" && (
      <div className="grid min-h-0 flex-1 grid-cols-6 grid-rows-[minmax(0,3fr)_minmax(0,2fr)] gap-1.5 p-1.5">
        <div className="col-span-4 min-h-0">
          <VideoPanel
            highlight={feed.highlight}
            seekTo={feed.seekTo}
            seekNonce={feed.seekNonce}
            video={feed.video}
            feed={Object.keys(state.feeds).filter((k) => k !== "").length >= 2
              ? state.active : null}
          />
        </div>
        <div className="col-span-2 min-h-0">
          <Timeline
            incidents={feed.incidents}
            reports={feed.reports}
            highlightId={feed.highlight?.incident_id}
            reportsPulse={feed.reportsPulse}
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
      )}

      {workspace === "review" && (
        <div className="min-h-0 flex-1 p-1.5">
        <TriagePanel
          title="Olay İnceleme Merkezi"
          layout="workspace"
          onSelectFeed={(selectedFeed) =>
            dispatch({ kind: "select_feed", feed: selectedFeed })}
          onSeek={(selectedFeed, timestamp, reviewVideo) => {
            dispatch({
              kind: "seek",
              feed: selectedFeed,
              timestamp,
              video: reviewVideo,
            });
            setLiveView(false);
            setReviewView(false);
            history.replaceState(null, "", "#");
          }}
          onOpenTraining={setTrainingEventId}
        />
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
