import type { FeedState } from "../state";
import { RISK_TR } from "../lib/labels";

/** Kamera duvarı şeridi — çoklu-akış (demo) kipinde akış başına bir karo.
 *  Karo: akış adı, ilerleme, olay sayısı + en ciddi risk, inceleme bayrağı.
 *  Tıklanan akış konsolun odağı olur (video/çizelge/iz o akışı gösterir). */

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
  if (names.length < 2) return null;      // tek akışta şerit gereksiz

  return (
    <div className="shrink-0 flex gap-2 overflow-x-auto">
      {names.map((name) => {
        const f = feeds[name];
        const risk = worstRisk(f);
        const review = f.incidents.filter((i) => i.needs_review).length;
        const busy = f.runStatus?.state === "processing";
        const pct = Math.round((f.runStatus?.progress ?? 0) * 100);
        return (
          <button
            key={name}
            onClick={() => onSelect(name)}
            className={`rounded-lg border px-3 py-1.5 text-left min-w-40 bg-zinc-900/70
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
              <span className="ml-auto text-[10px] text-zinc-500">
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
