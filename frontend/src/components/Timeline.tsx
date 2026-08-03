import type { IncidentUpdate, WindowReport } from "../types/events";

const PHASE_TR: Record<string, string> = {
  basladi: "başladı", gelisiyor: "gelişiyor", sonuclandi: "sonuçlandı",
};

function fmt(t: number) {
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export default function Timeline({
  incidents, reports, highlightId,
}: { incidents: IncidentUpdate[]; reports: WindowReport[]; highlightId?: string }) {
  return (
    <div className="panel h-full">
      <div className="panel-title flex items-center gap-2">
        Olay Zaman Çizelgesi
        {incidents.length > 0 && (
          <span className="ml-auto font-normal normal-case text-zinc-500">
            {incidents.length} olay
          </span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {incidents.length === 0 && reports.length === 0 && (
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
            {inc.thumbnail && (
              <img
                src={inc.thumbnail}
                alt=""
                className="mt-1.5 rounded w-full object-cover max-h-24"
              />
            )}
            <p className="text-xs text-zinc-400 mt-1">{inc.detail}</p>
            <span className="text-[10px] text-zinc-500">{PHASE_TR[inc.phase] ?? inc.phase}</span>
          </div>
        ))}

        {/* Pencere raporları: kesintisiz anlatı. Olaya dönüşmeyen `dusuk`
            gözlemler burada kalır — deftere girmezler (yanlış alarm üretmesin). */}
        {reports.length > 0 && (
          <div className="pt-2 border-t border-zinc-800">
            <div className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1 px-1">
              Pencere raporları
            </div>
            {reports.map((r) => (
              <div key={r.window_start} className="px-1 py-1.5">
                <span className="font-mono text-[10px] text-zinc-600">
                  {fmt(r.window_start)}–{fmt(r.window_end)}
                </span>
                <p className="text-xs text-zinc-400">{r.summary}</p>
                {r.uncertainties.length > 0 && (
                  <p className="text-[10px] text-amber-500/70 mt-0.5">
                    ? {r.uncertainties[0]}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
