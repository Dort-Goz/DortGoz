import type { FeedState } from "../state";
import { RISK_TR } from "../lib/labels";

const ORDER = ["dusuk", "orta", "yuksek", "kritik"] as const;

function worstRisk(f: FeedState): string | null {
  return f.incidents.reduce<string | null>(
    (w, i) => (w === null || ORDER.indexOf(i.risk) > ORDER.indexOf(w as any) ? i.risk : w),
    null);
}

export default function FeedStrip({ feeds, active, onSelect }: {
  feeds: Record<string, FeedState>;
  active: string;
  onSelect: (feed: string) => void;
}) {
  const names = Object.keys(feeds).filter((k) => k !== "");
  if (names.length < 2) return null;

  const busyFeeds = names.filter((n) => feeds[n].runStatus?.state === "processing");
  const total = names.reduce((s, n) => s + (feeds[n].runStatus?.speed ?? 0), 0);
  const enough = busyFeeds.length === 0 || total >= busyFeeds.length;

  return (
    <div className="shrink-0 flex flex-wrap gap-1.5 overflow-y-auto max-h-44">
      <div className={`rounded-lg border px-3 py-1.5 min-w-32 shrink-0 ${
        enough ? "border-emerald-900/60 bg-emerald-950/30" : "border-red-900/60 bg-red-950/30"
      }`}>
        <div className={`text-sm font-mono font-bold ${
          enough ? "text-emerald-300" : "text-red-300"
        }`}>
          Σ ×{total >= 10 ? total.toFixed(0) : total.toFixed(1)}
        </div>
        <div className="text-[10px] text-zinc-500">
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
            className={`rounded-lg border px-2.5 py-1.5 text-left w-48 bg-zinc-900/70
                        hover:bg-zinc-800 transition-colors ${
              name === active ? "border-zinc-400" : "border-zinc-800"
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-zinc-200">{name}</span>
              {risk && (
                <span className={`text-[10px] uppercase font-bold risk-${risk}`}>
                  {RISK_TR[risk as keyof typeof RISK_TR] ?? risk}
                </span>
              )}
              {review > 0 && (
                <span className="text-[10px] text-amber-400">⚑{review}</span>
              )}
              {speed > 0 && (
                <span className={`ml-auto text-[10px] font-mono font-bold ${
                  speed >= 1 ? "text-emerald-400" : "text-red-400"
                }`}>
                  ×{speed >= 10 ? speed.toFixed(0) : speed.toFixed(1)}
                </span>
              )}
              <span className={`text-[10px] text-zinc-500 ${speed > 0 ? "" : "ml-auto"}`}>
                {busy ? `%${pct}` : f.runStatus?.state ?? "—"}
              </span>
            </div>
            <div className="text-[10px] text-zinc-500 truncate max-w-44">
              {f.video ?? "—"} · {f.incidents.length} olay
            </div>
            <div className="mt-1 h-1 rounded bg-zinc-800 overflow-hidden">
              <div className={`h-full transition-all ${busy ? "bg-emerald-500" : "bg-zinc-600"}`}
                   style={{ width: `${pct}%` }} />
            </div>
          </button>
        );
      })}
    </div>
  );
}
