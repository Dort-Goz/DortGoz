import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { ActuatorRequest, ActuatorResult, Event, IncidentUpdate, Payload } from "./types/events";
import { DortgozSocket, type ConnectionState } from "./lib/ws";
import VideoPanel from "./components/VideoPanel";
import Timeline from "./components/Timeline";
import AgentTrace from "./components/AgentTrace";
import ChatPanel from "./components/ChatPanel";
import ActionLog from "./components/ActionLog";
import ImportPackage from "./components/ImportPackage";
import FeedStrip from "./components/FeedStrip";
import LiveGrid from "./components/LiveGrid";
import ModelMaintenancePanel from "./components/ModelMaintenancePanel";
import OperatorReportDialog from "./components/OperatorReport";
import UploadPanel from "./components/UploadPanel";
import TrainingReviewPanel from "./components/TrainingReviewPanel";
import ReviewConsole from "./components/ReviewConsole";
import {
  buildChatMessage,
  eventBelongsToDialogue,
  loadDialogueId,
  renewDialogueId,
} from "./lib/agentSession";
import { includeUploadedVideo, startCanonicalRun } from "./lib/canonicalRun";
import {
  actionBelongsToMode,
  analysisRunIds,
  consoleReducer,
  emptyFeed,
  feedNames,
  initialState,
} from "./state";

function createDialogueId(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `dialogue-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function browserDialogueId(): string {
  try {
    return loadDialogueId(sessionStorage, createDialogueId);
  } catch {
    return createDialogueId();
  }
}

function nextBrowserDialogueId(): string {
  try {
    return renewDialogueId(sessionStorage, createDialogueId);
  } catch {
    return createDialogueId();
  }
}

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

type Workspace = "analysis" | "live" | "review" | "maintenance";

/** İlk üçü nöbetteki operatöründür; bakım ayrı bir kümede durur. */
const OPERATOR_TABS = [
  ["analysis", "Analiz"],
  ["live", "Canlı"],
  ["review", "Olay inceleme"],
] as const satisfies readonly (readonly [Workspace, string])[];

const WORKSPACE_HASH: Record<Workspace, string> = {
  analysis: "#",
  live: "#canli",
  review: "#inceleme",
  maintenance: "#bakim",
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
  const [initialDialogueId] = useState(browserDialogueId);
  const dialogueIdRef = useRef(initialDialogueId);
  const socketRef = useRef<DortgozSocket | null>(null);
  const [videos, setVideos] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [runMode, setRunMode] = useState<"" | "temkinli" | "genis">("");
  const [demoCount, setDemoCount] = useState(4);
  const startPendingRef = useRef(false);
  const [startPending, setStartPending] = useState(false);
  const [liveView, setLiveView] = useState(() => location.hash === "#canli");
  const [reviewView, setReviewView] = useState(() => location.hash === "#inceleme");
  const [maintenanceView, setMaintenanceView] = useState(() => location.hash === "#bakim");
  /** Konsolun tek kimliği: her insan kararı ve onayı bu adla kaydedilir. */
  const [user, setUser] = useState(
    () => localStorage.getItem("dortgoz.reviewer") ?? "operator",
  );
  const [trainingEventId, setTrainingEventId] = useState("");
  const [reportT, setReportT] = useState<number | null>(null);
  const [openedFromMaintenance, setOpenedFromMaintenance] = useState(false);
  const [maintenanceRefreshToken, setMaintenanceRefreshToken] = useState(0);
  const [fixtureMode, setFixtureMode] = useState(false);
  const [livePending, setLivePending] = useState(0);
  const [resolvedKeys, setResolvedKeys] = useState<string[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  useEffect(() => {
    localStorage.setItem("dortgoz.reviewer", user);
  }, [user]);

  const resetDialogue = useCallback(() => {
    dialogueIdRef.current = nextBrowserDialogueId();
    dispatch({ kind: "clear_chat" });
  }, []);

  const closeTrainingReview = useCallback(() => {
    setTrainingEventId("");
    setOpenedFromMaintenance(false);
  }, []);

  const returnToMaintenance = useCallback(() => {
    setTrainingEventId("");
    setOpenedFromMaintenance(false);
    setMaintenanceRefreshToken((current) => current + 1);
    setLiveView(false);
    setReviewView(false);
    setMaintenanceView(true);
    history.replaceState(null, "", "#bakim");
  }, []);

  const openMaintenanceEvent = useCallback((eventId: string) => {
    setOpenedFromMaintenance(true);
    setTrainingEventId(eventId);
  }, []);

  useEffect(() => {
    const socket = new DortgozSocket(
      (e: Event) => {
        if (eventBelongsToDialogue(e, dialogueIdRef.current)) {
          dispatch({ kind: "event", event: e });
        }
      },
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
    if (fixtureMode && videos.length === 0) {
      setSelected((s) => s || "sanal-test-kaydi.mp4");
    }
  }, [fixtureMode, videos]);

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
        setFixtureMode(body.analysis_mode === "ui_fixture_replay");
      })
      .catch(() => setFixtureMode(false));
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch("/api/triage");
        if (!r.ok) return;
        const body = await r.json();
        if (alive) {
          const queued: { live?: boolean }[] = body.pending ?? [];
          setLivePending(queued.filter((item) => item.live).length);
          setResolvedKeys(body.resolved_keys ?? []);
        }
      } catch {}
    };
    poll();
    const id = setInterval(poll, 10000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Veri kümesi açıksa liste binlerce kayıt olur. Adın başındaki sınıf
  // (Burglary054_x264.mp4 → Burglary) başlık olur; yüklü kayıtlar "diğer"e düşer.
  const videoGroups = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const name of videos) {
      const key = /^([A-Za-z]+)/.exec(name)?.[1] ?? "diğer";
      const items = groups.get(key);
      if (items) items.push(name);
      else groups.set(key, [name]);
    }
    return [...groups].sort(([a], [b]) => a.localeCompare(b, "tr"));
  }, [videos]);

  const analysisFeeds = useMemo(
    () => Object.fromEntries(
      feedNames(state, false).map((name) => [name, state.feeds[name]])),
    [state],
  );
  const analysisActive = state.feeds[state.active]?.live ? "" : state.active;
  const feed = state.feeds[analysisActive] ?? emptyFeed;

  const send = useMemo(() => ({
    chat: (text: string) => socketRef.current?.send(buildChatMessage(
      text,
      dialogueIdRef.current,
      analysisActive,
      feed.highlight?.incident_id ?? "",
    )),
    actuator: (request_id: string, approved: boolean) =>
      socketRef.current?.send({
        kind: "actuator_response",
        request_id,
        approved,
        operator: localStorage.getItem("dortgoz.reviewer") ?? "",
      }),
  }), [analysisActive, feed.highlight?.incident_id]);

  const run = feed.runStatus;
  const selectFeed = useCallback(
    (name: string) => dispatch({ kind: "select_feed", feed: name }),
    [],
  );
  const selectLiveFeed = useCallback(
    (name: string) => dispatch({ kind: "select_feed", feed: name, live: true }),
    [],
  );
  const selectVideo = useCallback((video: string) => {
    if (video === selected) return;
    resetDialogue();
    setSelected(video);
  }, [selected, resetDialogue]);
  const selectIncident = useCallback(
    (incident: IncidentUpdate) => dispatch({ kind: "select_incident", incident }),
    [],
  );
  const busy = Object.values(analysisFeeds).some(
    (f) => f.runStatus?.state === "processing")
    || state.feeds[""]?.runStatus?.state === "processing";
  const workspace = liveView
    ? "live"
    : reviewView
      ? "review"
      : maintenanceView
        ? "maintenance"
        : "analysis";
  const liveIncidents = useMemo(
    () => Object.fromEntries(
      feedNames(state, true).map((name) => [name, state.feeds[name].incidents])),
    [state],
  );
  const liveActivity = useMemo(
    () => Object.fromEntries(
      feedNames(state, true).map((name) => [name, state.feeds[name].activity])),
    [state],
  );
  const analysisRuns = useMemo(() => analysisRunIds(state), [state]);
  const resolvedActionIds = useMemo(
    () => new Set(state.actuatorResults.map((result) => result.request_id)),
    [state.actuatorResults],
  );
  // Karar bekleyen istek koşu değişse de operatörde durur; sonuçlanmış kart
  // yalnız kendi koşusu açıkken görünür.
  const analysisActionRequests = useMemo(
    () => state.actuatorRequests.filter((request) => actionBelongsToMode(request, fixtureMode)
      && (analysisRuns.has(request.run_id) || !resolvedActionIds.has(request.request_id))),
    [state.actuatorRequests, fixtureMode, analysisRuns, resolvedActionIds],
  );
  const analysisActionResults = useMemo(
    () => state.actuatorResults.filter((result) => actionBelongsToMode(result, fixtureMode)
      && analysisRuns.has(result.run_id)),
    [state.actuatorResults, fixtureMode, analysisRuns],
  );

  const overrides = useCallback(() => ({ mode: runMode }), [runMode]);

  const startRun = useCallback(() => {
    const started = startCanonicalRun({
      selected,
      busy,
      gate: startPendingRef,
      overrides: overrides(),
      dispatchStarted: (video) => dispatch({ kind: "run_started", video, feed: "" }),
      send: (message) => socketRef.current?.send(message),
    });
    if (started) {
      resetDialogue();
      setStartPending(true);
    }
  }, [selected, busy, overrides, resetDialogue]);

  useEffect(() => {
    if (!run?.state) return;
    startPendingRef.current = false;
    setStartPending(false);
  }, [run]);

  const startDemo = useCallback((count: number) => {
    if (busy || videos.length === 0) return;
    resetDialogue();
    const long = videos.filter((v) => v.toLowerCase().startsWith("kamera"));
    const pool = long.length >= 2 ? long : videos;
    const picks = Array.from({ length: count }, (_, i) => pool[i % pool.length]);
    picks.forEach((video, i) => {
      const feedName = `KAM-${i + 1}`;
      dispatch({ kind: "run_started", video, feed: feedName });
      socketRef.current?.send({ kind: "start_run", video, feed: feedName, ...overrides() });
    });
  }, [busy, videos, overrides, resetDialogue]);

  const stopRun = useCallback(() => socketRef.current?.send({ kind: "stop_run" }), []);

  const clearAnalysis = useCallback(() => {
    resetDialogue();
    dispatch({ kind: "clear_analysis" });
  }, [resetDialogue]);

  const runState = run?.state ?? "idle";
  const rawPct = Math.round((run?.progress ?? 0) * 100);
  const progressPct = runState === "processing" ? Math.min(rawPct, 99) : rawPct;
  const decided = useMemo(
    () => new Set(
      resolvedKeys
        .filter((key) => key.slice(0, key.lastIndexOf(":")) === analysisActive)
        .map((key) => key.slice(key.lastIndexOf(":") + 1)),
    ),
    [resolvedKeys, analysisActive],
  );
  const detailReady = Boolean(
    feed.highlight && run?.run_id && run.run_id !== "-" && run.state === "done",
  );
  // Paket yalnız biten bir koşu için üretilir; kanıtlar videodan yeniden türetilir.
  const exportRunId = run?.run_id && run.run_id !== "-" && run.state === "done"
    ? run.run_id
    : "";

  const workspaceTab = (value: Workspace, label: string) => (
    <button
      key={value}
      type="button"
      onClick={() => {
        setLiveView(value === "live");
        setReviewView(value === "review");
        setMaintenanceView(value === "maintenance");
        history.replaceState(null, "", WORKSPACE_HASH[value]);
      }}
      className={`h-full px-2.5 transition-colors ${
        workspace === value
          ? "bg-zinc-800 font-medium text-zinc-100"
          : "text-zinc-500 hover:text-zinc-200"
      }`}
    >
      {label}
      {value === "live" && livePending > 0 && (
        <span
          title={`${livePending} canlı olay operatör kararı bekliyor`}
          className="ml-1.5 inline-flex min-w-4 items-center justify-center rounded-sm bg-amber-600 px-1 font-mono text-[10px] leading-4 text-amber-950"
        >
          {livePending}
        </span>
      )}
    </button>
  );

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {fixtureMode && (
        <div className="flex h-6 shrink-0 items-center justify-center border-b border-amber-900/60 bg-amber-950/30 px-3 text-[10px] font-semibold uppercase tracking-wider text-amber-300/90">
          ARAYÜZ TEST AKIŞI · “BAŞLAT” KAYITLI BİR ÖRNEK AKIŞI OYNATIR · VİDEO ANALİZ EDİLMEZ
        </div>
      )}

      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-zinc-800 bg-zinc-900 px-3">
        <span className="text-sm font-bold tracking-widest text-zinc-100">DÖRTGÖZ</span>

        {/* operatör çalışma alanları */}
        <nav
          aria-label="Operatör çalışma alanları"
          className="flex h-7 items-center gap-0.5 rounded-sm border border-zinc-800 bg-zinc-950 p-0.5 text-xs"
        >
          {OPERATOR_TABS.map(([value, label]) => workspaceTab(value, label))}
        </nav>

        {/* bakım mühendisi ayrı bir kullanıcı kümesidir; sekmesi de ayrı durur */}
        <span aria-hidden className="h-5 w-px shrink-0 bg-zinc-700" />
        <nav
          aria-label="Bakım çalışma alanı"
          className="flex h-7 items-center rounded-sm border border-zinc-800 bg-zinc-950 p-0.5 text-xs"
        >
          {workspaceTab("maintenance", "Bakım")}
        </nav>

        <div className="flex-1" />
        <label
          className="hidden items-center gap-1.5 sm:flex"
          title="Kararlara bu ad yazan olarak geçer"
        >
          <span className="microlabel">kullanıcı</span>
          <input
            value={user}
            onChange={(e) => setUser(e.target.value)}
            placeholder="ad soyad"
            className="field w-32"
          />
        </label>
        <span
          title={connection === "open"
            ? "Olaylar canlı akıyor"
            : "Bağlantı yok — ekrandaki veri bayat olabilir"}
          className={`chip border font-semibold uppercase tracking-wide ${CONNECTION_CLS[connection]}`}
        >
          {connection === "open" ? "●" : "○"} {CONNECTION_TR[connection]}
        </span>

        <Clock />
      </header>

      {workspace === "analysis" && (
        <div className="toolbar">
          <div className="toolbar-group">
            <span className="microlabel block">kaynak</span>
            <span className="flex items-center gap-1">
              <select
                aria-label="Kaynak video"
                value={selected}
                onChange={(e) => selectVideo(e.target.value)}
                disabled={busy || videos.length === 0}
                className="field w-52"
              >
                {videos.length === 0 && (
                  <option value={selected}>
                    {fixtureMode ? "sanal kayıt (media/ boş)" : "media/ boş"}
                  </option>
                )}
                {videoGroups.map(([label, items]) => (
                  <optgroup key={label} label={`${label} (${items.length})`}>
                    {items.map((v) => <option key={v} value={v}>{v}</option>)}
                  </optgroup>
                ))}
              </select>
              <UploadPanel onUploaded={(video) => {
                setVideos((current) => includeUploadedVideo(current, video.stored_filename));
                selectVideo(video.stored_filename);
              }} />
            </span>
          </div>
          <label className="toolbar-group">
            <span className="microlabel block">çalışma kipi</span>
            <select
              value={runMode}
              onChange={(e) => setRunMode(e.target.value as "" | "temkinli" | "genis")}
              disabled={busy}
              title="temkinli: az yanlış alarm · geniş: en yüksek yakalama"
              className="field"
            >
              <option value="">dengeli</option>
              <option value="temkinli">temkinli</option>
              <option value="genis">geniş</option>
            </select>
          </label>
          <button
            onClick={busy ? stopRun : startRun}
            disabled={(!selected && !busy) || (startPending && !busy)}
            className={`btn w-28 ${busy ? "btn-danger" : "btn-primary"}`}
          >
            {busy ? "Durdur" : startPending ? "Başlatılıyor…" : "Başlat"}
          </button>
          {!fixtureMode && videos.length > 1 && (
            <div className="toolbar-group">
              <span className="microlabel block">çoklu kamera</span>
              <span className="flex items-center gap-1">
              <button
                onClick={() => startDemo(demoCount)}
                disabled={busy}
                title="N akış eşzamanlı çözümlenir"
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
            </div>
          )}

          <button
            onClick={clearAnalysis}
            disabled={busy}
            title={busy
              ? "Önce çalışan analizi durdurun"
              : "Ekranı temizler; sunucudaki kayıtlar silinmez"}
            className="btn btn-outline"
          >
            ✕ temizle
          </button>

          <div className="min-w-0 flex-1" />

          <button
            onClick={() => {
              if (feed.highlight && run?.run_id) {
                setTrainingEventId(`${run.run_id}:${feed.highlight.incident_id}`);
              }
            }}
            disabled={!detailReady}
            title={detailReady
              ? "Seçili olayı insan incelemesine aç"
              : "Önce analizi bitirin ve bir olay seçin"}
            className="btn btn-outline-accent"
          >
            ◎ ayrıntılı incele
          </button>
          <button
            onClick={() => { if (exportRunId) location.href = `/api/runs/${exportRunId}/export`; }}
            disabled={!exportRunId}
            title={exportRunId
              ? "Koşuyu .zip paket olarak dışarı çıkar"
              : "Önce bir analiz tamamlanmalı"}
            className="btn btn-outline"
          >
            ↓ dışarı çıkar
          </button>
          <ImportPackage />
        </div>
      )}


      {workspace === "live" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <LiveGrid
            user={user}
            incidents={liveIncidents}
            activity={liveActivity}
            actionRequests={state.liveActuatorRequests}
            actionResults={state.liveActuatorResults}
            onRespond={send.actuator}
            onSelectFeed={selectLiveFeed}
          />
        </div>
      )}

      {workspace === "analysis" && (
      <div className="flex min-h-0 flex-1 gap-1.5 p-1.5">
      <FeedStrip feeds={analysisFeeds} active={analysisActive} onSelect={selectFeed} />
      <div className="grid min-h-0 flex-1 grid-cols-12 grid-rows-[minmax(0,3fr)_minmax(0,2fr)] gap-1.5">
        <div className="col-span-6 min-h-0">
          <VideoPanel
            highlight={feed.highlight}
            seekTo={feed.seekTo}
            seekNonce={feed.seekNonce}
            video={feed.video}
            feed={Object.keys(analysisFeeds).length >= 2 ? analysisActive : null}
            progress={run ? progressPct : null}
            progressError={runState === "error"}
            onReport={run?.run_id && run.run_id !== "-" ? setReportT : undefined}
          />
        </div>
        <div className="col-span-3 row-span-2 min-h-0">
          <Timeline
            incidents={feed.incidents}
            reports={feed.reports}
            highlightId={feed.highlight?.incident_id}
            reportsPulse={feed.reportsPulse}
            decided={decided}
            onSelect={selectIncident}
          />
        </div>
        <div className="col-span-3 row-span-2 min-h-0">
          <ChatPanel
            messages={state.chat}
            onSend={send.chat}
            incident={feed.highlight}
          />
        </div>
        <div className="col-span-3 min-h-0">
          <AgentTrace entries={feed.trace} />
        </div>
        <div className="col-span-3 min-h-0">
          <ActionLog
            requests={analysisActionRequests}
            results={analysisActionResults}
            onRespond={send.actuator}
          />
        </div>
      </div>
      </div>
      )}

      {workspace === "review" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <ReviewConsole onOpenTraining={setTrainingEventId} />
        </div>
      )}
      {workspace === "maintenance" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <ModelMaintenancePanel
            user={user}
            onOpenEvent={openMaintenanceEvent}
            refreshToken={maintenanceRefreshToken}
          />
        </div>
      )}

      {reportT !== null && (
        <OperatorReportDialog
          live={false}
          feeds={{}}
          initialFeed={analysisActive}
          runId={run?.run_id}
          video={feed.video ?? ""}
          initialT={reportT}
          user={user}
          onClose={() => setReportT(null)}
        />
      )}

      {trainingEventId && (
        <TrainingReviewPanel
          user={user}
          eventId={trainingEventId}
          onClose={closeTrainingReview}
          onBack={openedFromMaintenance ? returnToMaintenance : undefined}
        />
      )}
    </div>
  );
}

export type { Payload };
