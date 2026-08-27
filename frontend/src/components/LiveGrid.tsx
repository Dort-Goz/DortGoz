import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ActivityStrip, IncidentUpdate } from "../types/events";
import ActivityBar from "./ActivityBar";
import LiveArchive from "./LiveArchive";
import TriagePanel from "./TriagePanel";
import { startPreviewStream } from "../lib/livePreview";

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

function LiveGrid({ incidents, activity, onSelectFeed, onOpenTraining }: {
  incidents: Record<string, IncidentUpdate[]>;
  activity: Record<string, ActivityStrip[]>;
  onSelectFeed: (feed: string) => void;
  onOpenTraining: (eventId: string) => void;
}) {
  const [feeds, setFeeds] = useState<LiveFeed[]>([]);
  const [active, setActive] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState<string | null>(null);
  const [view, setView] = useState<"duvar" | "kayitlar">("duvar");
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

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1.5">
      <div className="flex h-9 shrink-0 items-center gap-3 text-xs">
        <button
          onClick={active ? stop : start}
          className={`btn w-44 ${active ? "btn-danger" : "btn-primary"}`}
        >
          {active ? "Canlıyı durdur" : "Canlı akışları başlat"}
        </button>
        {active && (
          <span className="font-mono text-zinc-400">
            {feeds.filter((f) => f.state !== "hata").length}/{feeds.length} akış ·{" "}
            {feeds.filter((f) => (f.lag_s ?? 1e9) <= 45).length} canlıya yetişik ·{" "}
            {feeds.reduce((a, f) => a + f.segments_done, 0)} segment işlendi
          </span>
        )}
        {error && <span className="truncate text-red-400">{error}</span>}
        <span className="flex-1" />
        <nav
          aria-label="Canlı görünümü"
          className="flex h-7 shrink-0 items-center gap-0.5 rounded-sm border border-zinc-800 bg-zinc-950 p-0.5"
        >
          {([["duvar", "▦ Akış duvarı"], ["kayitlar", "⛁ Olay kayıtları"]] as const).map(
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
        <label className="flex shrink-0 items-center gap-1.5 text-zinc-500">
          <span className="microlabel">kare tazeleme</span>
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
      </div>

      {zoomed && (
        <div className="flex shrink-0 items-start gap-3 rounded-md border border-zinc-700 bg-zinc-900 p-2">
          <img
            src={liveSrc(zoomed)}
            alt={zoomed.name}
            className="max-h-64 rounded-sm"
          />
          <div className="min-w-0 space-y-1 text-xs">
            <div className="text-sm font-bold text-zinc-100">{zoomed.desc || zoomed.name}</div>
            <div className={`chip ${lagBadge(zoomed).cls}`} title={lagBadge(zoomed).hint}>
              {lagBadge(zoomed).text}
            </div>
            <div className="font-mono text-zinc-400">
              {zoomed.segments_done} segment · {zoomed.state}
              {zoomed.dropped_s > 0 && ` · ${Math.round(zoomed.dropped_s)} sn atlandı`}
            </div>
            {zoomed.last_error && <div className="truncate text-red-400">{zoomed.last_error}</div>}
            {(incidents[zoomed.name] ?? []).slice(-3).map((i) => (
              <div key={i.incident_id} className="truncate text-amber-300">
                ⚠ {i.title} · risk {i.risk}
              </div>
            ))}
            <button onClick={() => setZoom(null)} className="btn btn-ghost h-6 px-1.5">
              kapat ✕
            </button>
          </div>
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-1.5">
      {view === "kayitlar" ? <LiveArchive feedNames={labels} /> : (
      <div className="panel flex-1">
      <div className="panel-title">
        <span>Akış Duvarı</span>
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
          gridTemplateColumns: `repeat(${Math.max(1, Math.ceil(Math.sqrt(feeds.length)))}, minmax(0, 1fr))`,
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
      )}
      <TriagePanel
        onSelectFeed={onSelectFeed}
        onOpenTraining={onOpenTraining}
        scopeLive
        feedNames={labels}
      />
      </div>
    </div>
  );
}

export default memo(LiveGrid);
