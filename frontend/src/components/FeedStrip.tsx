import { memo, useMemo } from "react";
import type { FeedState } from "../state";
import { RISK_TR } from "../lib/labels";

const ORDER = ["dusuk", "orta", "yuksek", "kritik"] as const;

function worstRisk(f: FeedState): string | null {
  return f.incidents.reduce<string | null>(
    (w, i) => (w === null || ORDER.indexOf(i.risk) > ORDER.indexOf(w as any) ? i.risk : w),
    null);
}

function FeedStrip({ feeds, active, onSelect }: {
  feeds: Record<string, FeedState>;
  active: string;
  onSelect: (feed: string) => void;
}) {
  const names = Object.keys(feeds).filter((k) => k !== "");
  const summary = useMemo(() => {
    const busyFeeds = names.filter((n) => feeds[n].runStatus?.state === "processing");
    const total = names.reduce((s, n) => s + (feeds[n].runStatus?.speed ?? 0), 0);
    return { busyFeeds, total, enough: busyFeeds.length === 0 || total >= busyFeeds.length };
  }, [names, feeds]);
  if (names.length < 2) return null;
  const { busyFeeds, total, enough } = summary;

  return (
    <div className="panel w-52 shrink-0">
      <div className="panel-title">
        <span>Akışlar</span>
        <span className="flex-1" />
        <span className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-300">
          {names.length}
        </span>
      </div>
      <div className="panel-body flex flex-col gap-1 p-1.5">
        <div className={`shrink-0 rounded-sm border px-2 py-1.5 ${
          enough ? "border-emerald-900 bg-emerald-950/30" : "border-red-900 bg-red-950/30"
        }`}>
          <div className={`font-mono text-sm font-bold ${
            enough ? "text-emerald-300" : "text-red-300"
          }`}>
            Σ ×{total >= 10 ? total.toFixed(0) : total.toFixed(1)}
          </div>
          <div
            className="truncate text-[10px] text-zinc-500"
            title={busyFeeds.length > 0
              ? `Toplam işleme hızı — ${busyFeeds.length} akışın hepsi gerçek zamanlı kalması için Σ ≥ ${busyFeeds.length} olmalı`
              : undefined}
          >
            {busyFeeds.length > 0
              ? `${busyFeeds.length} akış işleniyor · gerçek zaman için Σ ≥ ${busyFeeds.length}`
              : `${names.length} akış tamamlandı`}
          </div>
        </div>
        {names.map((name) => {
          const f = feeds[name];
          const risk = worstRisk(f);
          const review = f.incidents.filter((i) => i.needs_review).length;
          const busy = f.runStatus?.state === "processing";
          const pct = Math.round((f.runStatus?.progress ?? 0) * 100);
          const speed = f.runStatus?.speed ?? 0;
          return (
            <button
              key={name}
              onClick={() => onSelect(name)}
              className={`shrink-0 rounded-sm border bg-zinc-900 px-2 py-1.5 text-left transition-colors hover:bg-zinc-800 ${
                name === active ? "border-sky-600 ring-1 ring-sky-800" : "border-zinc-800"
              }`}
            >
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-xs font-bold text-zinc-200">{name}</span>
                {risk && (
                  <span className={`text-[10px] font-bold uppercase risk-${risk}`}>
                    {RISK_TR[risk as keyof typeof RISK_TR] ?? risk}
                  </span>
                )}
                {review > 0 && (
                  <span className="text-[10px] text-amber-400">⚑{review}</span>
                )}
                {speed > 0 && (
                  <span className={`ml-auto font-mono text-[10px] font-bold ${
                    speed >= 1 ? "text-emerald-400" : "text-red-400"
                  }`}>
                    ×{speed >= 10 ? speed.toFixed(0) : speed.toFixed(1)}
                  </span>
                )}
                <span className={`font-mono text-[10px] text-zinc-500 ${speed > 0 ? "" : "ml-auto"}`}>
                  {busy ? `%${pct}` : f.runStatus?.state ?? "—"}
                </span>
              </div>
              <div className="truncate text-[10px] text-zinc-500"
                   title={f.video ?? undefined}>
                {f.video ?? "—"} · {f.incidents.length} olay
              </div>
              <div className="mt-1 h-0.5 overflow-hidden rounded-full bg-zinc-800">
                <div className={`h-full ${busy ? "bg-emerald-500" : "bg-zinc-600"}`}
                     style={{ width: `${pct}%` }} />
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default memo(FeedStrip);
