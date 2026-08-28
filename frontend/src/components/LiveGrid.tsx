import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ActivityStrip, ActuatorRequest, ActuatorResult, IncidentUpdate,
} from "../types/events";
import ActionLog, { pendingActionCount } from "./ActionLog";
import ActivityBar from "./ActivityBar";
import LiveArchive from "./LiveArchive";
import OperatorReportDialog from "./OperatorReport";
import TriagePanel from "./TriagePanel";
import { startPreviewStream } from "../lib/livePreview";
import { PHASE_TR, RISK_TR, TYPE_TR, clock, humanizeEnums } from "../lib/labels";

interface LiveFeed {
  name: string;
  url: string;
  desc: string;
  state: string;
  lag_s: number | null;
  dropped_s: number;
  segments_done: number;
  last_error: string;
  snapshot: string;
}

function lagBadge(f: LiveFeed): { text: string; cls: string; hint: string } {
  if (f.state === "hata") {
    return {
      text: "KOPUK",
      cls: "bg-red-800 text-red-100",
      hint: "Akış koptu — görüntü gelmiyor.",
    };
  }
  const tail = "Görüntü canlıdır; bu rozet çözümlemenin ne kadar geride olduğunu söyler.";
  if (f.lag_s == null) {
    return {
      text: "analiz bekliyor",
      cls: "bg-zinc-700 text-zinc-200",
      hint: `Henüz çözümlenmiş segment yok. ${tail}`,
    };
  }
  const s = Math.round(f.lag_s);
  const text = s < 90 ? `analiz −${s}s` : `analiz −${Math.round(s / 60)}dk`;
  if (s <= 45) return { text, cls: "bg-emerald-800 text-emerald-100", hint: tail };
  if (s <= 120) return { text, cls: "bg-amber-800 text-amber-100", hint: tail };
  return { text, cls: "bg-red-800 text-red-100", hint: tail };
}

function LiveGrid({
  user, incidents, activity, actionRequests, actionResults, onRespond, onSelectFeed,
}: {
  /** Konsolun tek kimliği; üst çubuktan gelir ve nöbet kararlarını imzalar. */
  user: string;
  incidents: Record<string, IncidentUpdate[]>;
  activity: Record<string, ActivityStrip[]>;
  actionRequests: ActuatorRequest[];
  actionResults: ActuatorResult[];
  onRespond: (requestId: string, approved: boolean) => void;
  onSelectFeed: (feed: string) => void;
}) {
  const [feeds, setFeeds] = useState<LiveFeed[]>([]);
  const [active, setActive] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState<string | null>(null);
  const [view, setView] = useState<"duvar" | "kayitlar" | "aksiyonlar">("duvar");
  const [reportFeed, setReportFeed] = useState<string | null>(null);
  const [preview, setPreview] = useState<Record<string, string>>({});
  const previewRef = useRef<Record<string, string>>({});
  const [rate, setRate] = useState<number>(() =>
    Number(localStorage.getItem("dortgoz.canliKareOrani") || 1));
  const changeRate = (v: number) => {
    setRate(v);
    localStorage.setItem("dortgoz.canliKareOrani", String(v));
  };

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch("/api/live/status");
        const body = await r.json();
        if (!alive) return;
        setActive(body.active);
        setFeeds(body.feeds);
      } catch {}
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const start = useCallback(async () => {
    setError("");
    const r = await fetch("/api/live/start", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (!r.ok) setError((await r.json()).detail ?? r.statusText);
  }, []);

  const stop = useCallback(async () => {
    await fetch("/api/live/stop", { method: "POST" });
    setZoom(null);
  }, []);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let pending: Record<string, string> = {};
    let queued = false;
    startPreviewStream((feed, url) => {
      const previous = previewRef.current[feed] ?? pending[feed];
      if (previous) URL.revokeObjectURL(previous);
      pending = { ...pending, [feed]: url };
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        previewRef.current = { ...previewRef.current, ...pending };
        pending = {};
        setPreview(previewRef.current);
      });
    }, controller.signal);
    return () => {
      controller.abort();
      for (const url of Object.values(previewRef.current)) URL.revokeObjectURL(url);
      previewRef.current = {};
      setPreview({});
    };
  }, [active]);

  const liveSrc = useCallback(
    (f: LiveFeed) => preview[f.name]
      ?? (f.snapshot ? `${f.snapshot}?v=${Math.floor(f.segments_done / rate)}` : ""),
    [preview, rate],
  );

  const zoomed = zoom ? feeds.find((f) => f.name === zoom) : null;
  const labels = useMemo(
    () => Object.fromEntries(feeds.filter((f) => f.desc).map((f) => [f.name, f.desc])),
    [feeds],
  );
  const allLabels = useMemo(
    () => Object.fromEntries(feeds.map((f) => [f.name, f.desc || f.name])),
    [feeds],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="toolbar">
        <button
          onClick={active ? stop : start}
          className={`btn w-44 ${active ? "btn-danger" : "btn-primary"}`}
        >
          {active ? "Canlıyı durdur" : "Canlı akışları başlat"}
        </button>

        <div className="toolbar-group">
          <span className="microlabel block">görünüm</span>
          <nav
            aria-label="Canlı görünümü"
            className="flex h-7 items-center gap-0.5 rounded-sm border border-zinc-800 bg-zinc-950 p-0.5"
          >
            {([
              ["duvar", "▦ Akış duvarı"],
              ["kayitlar", "⛁ Olay kayıtları"],
              ["aksiyonlar", "⚙ Aksiyon günlüğü"],
            ] as const).map(
              ([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setView(value)}
                  className={`h-full px-2 transition-colors ${
                    view === value
                      ? "bg-zinc-800 font-medium text-zinc-100"
                      : "text-zinc-500 hover:text-zinc-200"
                  }`}
                >
                  {label}
                </button>
              ),
            )}
          </nav>
        </div>

        <label className="toolbar-group">
          <span className="microlabel block">kare tazeleme</span>
          <select
            value={rate}
            onChange={(e) => changeRate(Number(e.target.value))}
            title="Izgara karesi kaç segmentte bir indirilsin — yavaş bağlantıda yükseltin (büyütülmüş görünüm her zaman en tazedir)"
            className="field"
          >
            <option value={1}>her segment</option>
            <option value={2}>2 segmentte 1</option>
            <option value={4}>4 segmentte 1</option>
            <option value={8}>8 segmentte 1</option>
          </select>
        </label>

        {feeds.length > 0 && (
          <button
            onClick={() => setReportFeed(zoom ?? feeds[0].name)}
            className="btn btn-outline-warn"
            title="Duvarın bildirmediği bir olay gördüyseniz elle kayda geçirin"
          >
            ⚑ Kaçan olayı bildir
          </button>
        )}

        <span className="flex-1" />
        {active && (
          <span className="truncate font-mono text-zinc-400">
            {feeds.filter((f) => f.state !== "hata").length}/{feeds.length} akış ·{" "}
            {feeds.filter((f) => (f.lag_s ?? 1e9) <= 45).length} canlıya yetişik ·{" "}
            {feeds.reduce((a, f) => a + f.segments_done, 0)} segment işlendi
          </span>
        )}
        {error && <span className="truncate text-red-400">{error}</span>}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1.5 p-1.5">

      <div className="flex min-h-0 flex-1 gap-1.5">
        {view === "kayitlar" ? <LiveArchive feedNames={labels} /> : view === "aksiyonlar" ? (
          <ActionLog
            requests={actionRequests}
            results={actionResults}
            onRespond={onRespond}
          />
        ) : (
      <>
      <div className={`panel ${zoomed ? "w-[14vw] min-w-48 max-w-80 shrink-0" : "flex-1"}`}>
      <div className="panel-title">
        <span>{zoomed ? "Diğer akışlar" : "Akış Duvarı"}</span>
        <span className="flex-1" />
        {feeds.length > 0 && (
          <span className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-300">
            {feeds.length}
          </span>
        )}
      </div>
      <div
        className="panel-body grid content-start gap-1 p-1.5"
        style={{
          gridTemplateColumns: `repeat(${
            zoomed ? 1 : Math.max(1, Math.ceil(Math.sqrt(feeds.length)))
          }, minmax(0, 1fr))`,
        }}
      >
        {feeds.map((f) => {
          const badge = lagBadge(f);
          const inc = incidents[f.name]?.length ?? 0;
          return (
            <div key={f.name} className="flex flex-col gap-px">
            <button
              onClick={() => { setZoom(f.name); onSelectFeed(f.name); }}
              className={`relative aspect-video overflow-hidden rounded-sm border bg-black text-left transition-colors ${
                inc > 0 ? "border-amber-700" : "border-zinc-800"
              } hover:border-zinc-500`}
              title={`${f.name} · ${f.state}`}
            >
              {liveSrc(f) ? (
                <img
                  src={liveSrc(f)}
                  alt={f.name}
                  decoding="async"
                  className="h-full w-full object-cover opacity-90"
                />
              ) : (
                <div className="flex h-full min-h-20 w-full items-center justify-center text-xs text-zinc-600">
                  {f.state === "hata" ? "bağlantı yok" : "bağlanıyor…"}
                </div>
              )}
              <div className="absolute left-0 right-0 top-0 flex justify-between gap-1 px-1 pt-1 text-[10px]">
                <span className="max-w-[60%] truncate rounded-sm bg-black/70 px-1 leading-4"
                      title={f.desc || f.name}>
                  {f.desc || f.name}
                </span>
                <span title={badge.hint}
                      className={`rounded-sm px-1 font-mono leading-4 ${badge.cls}`}>
                  {badge.text}
                </span>
              </div>
              {(f.state === "isleniyor" || inc > 0 || f.dropped_s > 0) && (
                <div className="absolute bottom-0 left-0 right-0 flex gap-1 px-1 pb-1 text-[10px]">
                  {f.state === "isleniyor" && (
                    <span className="rounded-sm bg-sky-950/90 px-1 leading-4 text-sky-200">⚙ işleniyor</span>
                  )}
                  {inc > 0 && (
                    <span className="rounded-sm bg-amber-900/90 px-1 leading-4 text-amber-100">⚠ {inc} olay</span>
                  )}
                  {f.dropped_s > 0 && (
                    <span className="rounded-sm bg-red-950/90 px-1 font-mono leading-4 text-red-200">
                      ⏭ {Math.round(f.dropped_s)}s
                    </span>
                  )}
                </div>
              )}
            </button>
            <ActivityBar strips={activity[f.name] ?? []} />
            </div>
          );
        })}
        {!active && feeds.length === 0 && (
          <div className="col-span-full flex flex-col items-center gap-2 py-20 text-zinc-500">
            <span className="text-3xl text-zinc-800">▦</span>
            <span className="text-xs">Canlı kip kapalı — config/live_feeds.json'daki akışlarla başlatın.</span>
          </div>
        )}
      </div>
      </div>
      {zoomed && (
        <FocusedFeed
          feed={zoomed}
          src={liveSrc(zoomed)}
          strips={activity[zoomed.name] ?? []}
          incidents={incidents[zoomed.name] ?? []}
          onReport={() => setReportFeed(zoomed.name)}
          onClose={() => setZoom(null)}
        />
      )}
      </>
      )}
      <TriagePanel
        user={user}
        onSelectFeed={onSelectFeed}
        scopeLive
        feedNames={labels}
        logPending={pendingActionCount(actionRequests, actionResults)}
        logPanel={(
          <ActionLog
            bare
            requests={actionRequests}
            results={actionResults}
            onRespond={onRespond}
          />
        )}
      />
      </div>
      </div>
      {reportFeed !== null && (
        <OperatorReportDialog
          live
          feeds={allLabels}
          initialFeed={reportFeed}
          user={user}
          onClose={() => setReportFeed(null)}
        />
      )}
    </div>
  );
}

function FocusedFeed({
  feed, src, strips, incidents, onReport, onClose,
}: {
  feed: LiveFeed;
  src: string;
  strips: ActivityStrip[];
  incidents: IncidentUpdate[];
  onReport: () => void;
  onClose: () => void;
}) {
  const badge = lagBadge(feed);
  const latest = new Map<string, IncidentUpdate>();
  for (const i of incidents) latest.set(i.incident_id, i);
  const list = [...latest.values()].reverse();

  return (
    <div className="panel panel-accent min-w-0 flex-1">
      <div className="panel-title">
        <span className="truncate normal-case tracking-normal text-[13px] font-bold text-zinc-100"
              title={feed.name}>
          {feed.desc || feed.name}
        </span>
        <span className={`chip ${badge.cls}`} title={badge.hint}>{badge.text}</span>
        {feed.state === "isleniyor" && (
          <span className="chip bg-sky-950 text-sky-200">⚙ işleniyor</span>
        )}
        <span className="flex-1" />
        <button
          onClick={onReport}
          className="btn btn-outline-warn h-6 px-1.5"
          title="Bu kamerada sistemin kaçırdığı bir olayı bildir"
        >
          ⚑ bildir
        </button>
        <button onClick={onClose} className="btn btn-ghost h-6 px-1.5" title="Duvara dön">
          kapat ✕
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="relative flex min-h-0 flex-1 items-center justify-center bg-black">
          {src ? (
            <img src={src} alt={feed.name} className="h-full w-full object-contain" />
          ) : (
            <span className="text-xs text-zinc-600">
              {feed.state === "hata" ? "bağlantı yok" : "bağlanıyor…"}
            </span>
          )}
          {feed.dropped_s > 0 && (
            <span className="chip absolute bottom-1 right-1 bg-red-950/90 font-mono text-red-200"
                  title="Çözümlemenin canlıya yetişmek için atladığı süre">
              ⏭ {Math.round(feed.dropped_s)}s atlandı
            </span>
          )}
        </div>

        <div className="shrink-0 border-y border-zinc-800 bg-zinc-950 px-1.5 py-1">
          <ActivityBar strips={strips} />
          <div className="mt-1 flex items-center gap-2 font-mono text-[11px] text-zinc-500">
            <span>{feed.segments_done} segment</span>
            <span>·</span>
            <span>{feed.state}</span>
            {feed.last_error && (
              <span className="truncate text-red-400" title={feed.last_error}>
                · {feed.last_error}
              </span>
            )}
          </div>
        </div>

        <div className="flex max-h-[38%] shrink-0 flex-col">
          <div className="flex shrink-0 items-center gap-2 px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
            <span>Bu akıştaki anomaliler</span>
            <span className={`chip border font-mono ${
              list.length > 0 ? "border-amber-900 text-amber-300" : "border-zinc-800 text-zinc-500"
            }`}>
              {list.length}
            </span>
          </div>
          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-1.5 pb-1.5 text-xs">
            {list.length === 0 && (
              <div className="text-zinc-500">Bu akışta kayda geçen anomali yok.</div>
            )}
            {list.map((i) => (
              <div key={i.incident_id}
                   className={`flex gap-2 border-l-2 bg-zinc-950 p-1.5 risk-${i.risk}`}>
                {i.thumbnail && (
                  <img src={i.thumbnail} alt="" className="h-12 w-20 shrink-0 object-cover" />
                )}
                <div className="min-w-0 flex-1 space-y-0.5">
                  <div className="text-zinc-100">{i.title}</div>
                  {i.detail && (
                    <div className="line-clamp-2 text-zinc-400">{humanizeEnums(i.detail)}</div>
                  )}
                  <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-zinc-500">
                    <span className={`chip border risk-${i.risk}`}>risk {RISK_TR[i.risk]}</span>
                    <span>{TYPE_TR[i.anomaly_type] ?? i.anomaly_type}</span>
                    <span className="font-mono">{clock(i.t)}</span>
                    <span>{PHASE_TR[i.phase] ?? i.phase}</span>
                    {i.needs_review && <span className="text-amber-400">inceleme bekliyor</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default memo(LiveGrid);
