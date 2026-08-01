import type { IncidentUpdate } from "../types/events";

const PHASE_TR: Record<string, string> = {
  basladi: "başladı", gelisiyor: "gelişiyor", sonuclandi: "sonuçlandı",
};

function fmt(t: number) {
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export default function Timeline({
  incidents, highlightId,
}: { incidents: IncidentUpdate[]; highlightId?: string }) {
  return (
    <div className="panel h-full">
      <div className="panel-title">Olay Zaman Çizelgesi</div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {incidents.length === 0 && (
          <p className="text-xs text-zinc-600 p-2">Henüz olay yok — akış bekleniyor.</p>
        )}
        {incidents.map((inc) => (
          <div
            key={inc.incident_id}
            className={`rounded border-l-2 bg-zinc-900 p-2 risk-${inc.risk} ${
              inc.incident_id === highlightId ? "ring-1 ring-zinc-500" : ""
            }`}
          >
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-xs text-zinc-500">{fmt(inc.t)}</span>
              <span className="text-sm font-medium text-zinc-200">{inc.title}</span>
              <span className={`ml-auto text-[10px] uppercase font-bold risk-${inc.risk}`}>
                {inc.risk}
              </span>
            </div>
            <p className="text-xs text-zinc-400 mt-1">{inc.detail}</p>
            <span className="text-[10px] text-zinc-500">{PHASE_TR[inc.phase] ?? inc.phase}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
