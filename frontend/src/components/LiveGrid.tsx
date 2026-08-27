import { memo, useCallback, useEffect, useMemo, useState } from "react";
import type { IncidentUpdate } from "../types/events";
import LiveArchive from "./LiveArchive";
import TriagePanel from "./TriagePanel";

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

function lagBadge(f: LiveFeed): { text: string; cls: string } {
  if (f.state === "hata") return { text: "KOPUK", cls: "bg-red-800 text-red-100" };
  if (f.lag_s == null) return { text: "başlıyor…", cls: "bg-zinc-700 text-zinc-200" };
  const s = Math.round(f.lag_s);
  if (s <= 45) return { text: `CANLI −${s}s`, cls: "bg-emerald-800 text-emerald-100" };
  if (s <= 120) return { text: `−${s}s geride`, cls: "bg-amber-800 text-amber-100" };
  return { text: `−${Math.round(s / 60)}dk geride`, cls: "bg-red-800 text-red-100" };
}

function LiveGrid({ incidents, onSelectFeed, onOpenTraining }: {
  incidents: Record<string, IncidentUpdate[]>;
  onSelectFeed: (feed: string) => void;
  onOpenTraining: (eventId: string) => void;
}) {
  const [feeds, setFeeds] = useState<LiveFeed[]>([]);
  const [active, setActive] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState<string | null>(null);
  const [view, setView] = useState<"duvar" | "kayitlar">("duvar");
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

  const zoomed = zoom ? feeds.find((f) => f.name === zoom) ?? null : null;

  useEffect(() => {
    if (feeds.length === 0) {
      if (zoom !== null) setZoom(null);
      return;
    }
    if (zoom === null || !feeds.some((f) => f.name === zoom)) {
      setZoom(feeds[0].name);
      onSelectFeed(feeds[0].name);
    }
  }, [feeds, zoom, onSelectFeed]);

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
                className={`h-full rounded-[3px] px-2 transition-colors ${
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

      <div className="flex min-h-0 flex-1 gap-1.5">
      {view === "kayitlar" ? <LiveArchive feedNames={labels} /> : (
      <>
      <div className="panel w-52 shrink-0">
      <div className="panel-title">
        <span>Akışlar</span>
        <span className="flex-1" />
        {feeds.length > 0 && (
          <span className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-300">
            {feeds.length}
          </span>
        )}
      </div>
      <div className="panel-body flex flex-col gap-1 p-1.5">
        {feeds.map((f) => {
          const badge = lagBadge(f);
          const inc = incidents[f.name]?.length ?? 0;
          return (
            <button
              key={f.name}
              onClick={() => { setZoom(f.name); onSelectFeed(f.name); }}
              className={`relative aspect-video shrink-0 overflow-hidden rounded-sm border bg-black text-left transition-colors ${
                zoom === f.name
                  ? "border-sky-600 ring-1 ring-sky-800"
                  : inc > 0 ? "border-amber-700" : "border-zinc-800"
              } hover:border-zinc-500`}
              title={`${f.name} · ${f.state}`}
            >
              {f.snapshot ? (
                <img
                  src={`${f.snapshot}?v=${Math.floor(f.segments_done / rate)}`}
                  alt={f.name}
                  loading="lazy"
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
                <span className={`rounded-sm px-1 font-mono leading-4 ${badge.cls}`}>{badge.text}</span>
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
          );
        })}
        {!active && feeds.length === 0 && (
          <div className="px-1 py-6 text-center text-[11px] text-zinc-500">
            Canlı kip kapalı.
          </div>
        )}
      </div>
      </div>

      <div className="panel flex-1">
        <div className="panel-title">
          <span className="truncate">{zoomed ? (zoomed.desc || zoomed.name) : "Kamera"}</span>
          <span className="flex-1" />
          {zoomed && (
            <>
              <span className={`chip normal-case tracking-normal ${lagBadge(zoomed).cls}`}>
                {lagBadge(zoomed).text}
              </span>
              <span className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-400">
                {zoomed.segments_done} segment · {zoomed.state}
                {zoomed.dropped_s > 0 && ` · ⏭ ${Math.round(zoomed.dropped_s)}s`}
              </span>
            </>
          )}
        </div>
        <div className="panel-body flex min-h-0 flex-col p-1.5">
          {!zoomed ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 text-zinc-500">
              <span className="text-3xl text-zinc-800">▦</span>
              <span className="text-xs">
                {active
                  ? "Soldaki listeden bir kamera seçin."
                  : "Canlı kip kapalı — config/live_feeds.json'daki akışlarla başlatın."}
              </span>
            </div>
          ) : (
            <>
              <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-sm bg-black">
                {zoomed.snapshot ? (
                  <img
                    src={`${zoomed.snapshot}?v=${zoomed.segments_done}`}
                    alt={zoomed.name}
                    className="max-h-full max-w-full object-contain"
                  />
                ) : (
                  <span className="text-xs text-zinc-600">
                    {zoomed.state === "hata" ? "bağlantı yok" : "bağlanıyor…"}
                  </span>
                )}
              </div>
              {zoomed.last_error && (
                <div className="mt-1 shrink-0 truncate text-[11px] text-red-400"
                     title={zoomed.last_error}>
                  {zoomed.last_error}
                </div>
              )}
              {(incidents[zoomed.name] ?? []).length > 0 && (
                <div className="mt-1 shrink-0 space-y-0.5">
                  {(incidents[zoomed.name] ?? []).slice(-3).map((i) => (
                    <div key={i.incident_id} className="truncate text-[11px] text-amber-300">
                      ⚠ {i.title} · risk {i.risk}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
      </>
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
