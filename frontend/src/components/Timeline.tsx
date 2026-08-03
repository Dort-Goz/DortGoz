import type { IncidentUpdate, WindowReport } from "../types/events";
import { PHASE_TR, TYPE_TR, clock } from "../lib/labels";

export default function Timeline({
  incidents, reports, highlightId, onSelect,
}: {
  incidents: IncidentUpdate[];
  reports: WindowReport[];
  highlightId?: string;
  onSelect: (inc: IncidentUpdate) => void;
}) {
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
          <button
            key={inc.incident_id}
            onClick={() => onSelect(inc)}
            title="Videoyu bu ana sar"
            className={`w-full text-left rounded border-l-2 bg-zinc-900 p-2 risk-${inc.risk}
                        hover:bg-zinc-800 transition-colors ${
              inc.incident_id === highlightId ? "ring-1 ring-zinc-500" : ""
            }`}
          >
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-xs text-zinc-500">{clock(inc.t)}</span>
              {/* Nihai sınıf kararı — operatörün ilk bakışta görmesi gereken bilgi */}
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300
                               uppercase tracking-wide font-semibold">
                {TYPE_TR[inc.anomaly_type] ?? inc.anomaly_type}
              </span>
              <span className={`ml-auto text-[10px] uppercase font-bold risk-${inc.risk}`}>
                {inc.risk}
              </span>
            </div>
            <div className="text-sm font-medium text-zinc-200 mt-1">{inc.title}</div>
            {inc.thumbnail && (
              <img src={inc.thumbnail} alt="" className="mt-1.5 rounded w-full object-cover max-h-24" />
            )}
            <p className="text-xs text-zinc-400 mt-1">{inc.detail}</p>
            <span className="text-[10px] text-zinc-500">{PHASE_TR[inc.phase] ?? inc.phase}</span>
          </button>
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
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-[10px] text-zinc-600">
                    {clock(r.window_start)}–{clock(r.window_end)}
                  </span>
                  <span className={`text-[10px] ${
                    r.anomaly_type === "normal" ? "text-zinc-600" : "text-amber-400/80"
                  }`}>
                    {TYPE_TR[r.anomaly_type] ?? r.anomaly_type}
                  </span>
                </div>
                <p className="text-xs text-zinc-400">{r.summary}</p>
                {r.uncertainties.length > 0 && (
                  <p className="text-[10px] text-amber-500/70 mt-0.5">? {r.uncertainties[0]}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
