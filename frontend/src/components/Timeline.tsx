import { useState } from "react";
import type { IncidentUpdate, WindowReport } from "../types/events";
import { PHASE_TR, RISK_TR, TYPE_TR, clock } from "../lib/labels";

const quiet = (r: WindowReport) => r.anomaly_type === "normal" && r.events.length === 0;

type Row = { kind: "report"; report: WindowReport }
         | { kind: "quiet"; reports: WindowReport[] };

function groupRows(reports: WindowReport[]): Row[] {
  const rows: Row[] = [];
  for (const r of reports) {
    const last = rows[rows.length - 1];
    if (quiet(r)) {
      if (last?.kind === "quiet") last.reports.push(r);
      else rows.push({ kind: "quiet", reports: [r] });
    } else {
      rows.push({ kind: "report", report: r });
    }
  }
  return rows;
}

function ReportRow({ r }: { r: WindowReport }) {
  return (
    <div className="px-1 py-1.5">
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
  );
}

function QuietGroup({ reports }: { reports: WindowReport[] }) {
  const [open, setOpen] = useState(false);
  const first = reports[0], last = reports[reports.length - 1];
  return (
    <div className="px-1 py-1">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-baseline gap-2 text-left hover:bg-zinc-800/40 rounded px-1"
      >
        <span className="font-mono text-[10px] text-zinc-600">
          {clock(first.window_start)}–{clock(last.window_end)}
        </span>
        <span className="text-[10px] text-zinc-600">olağan</span>
        <span className="text-[10px] text-zinc-700">
          {reports.length} pencere{reports.length > 1 ? ", olay yok" : ""}
        </span>
        <span className="ml-auto text-[10px] text-zinc-700">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="pl-2 border-l border-zinc-800 ml-1 mt-1">
          {reports.map((r) => (
            <div key={r.window_start} className="py-0.5">
              <span className="font-mono text-[10px] text-zinc-700 mr-2">
                {clock(r.window_start)}
              </span>
              <span className="text-[11px] text-zinc-500">{r.summary}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

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
            <div className="flex gap-2">
              {inc.thumbnail && (
                <img src={inc.thumbnail} alt=""
                     className="w-14 h-14 shrink-0 rounded object-cover bg-black" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="font-mono text-xs text-zinc-500">{clock(inc.t)}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300
                                   uppercase tracking-wide font-semibold">
                    {TYPE_TR[inc.anomaly_type] ?? inc.anomaly_type}
                  </span>
                  {inc.needs_review && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wide
                                     font-semibold bg-amber-950/60 text-amber-300
                                     border border-amber-700/60">
                      ⚑ inceleme ister
                    </span>
                  )}
                  <span className={`ml-auto text-[10px] uppercase font-bold risk-${inc.risk}`}>
                    {RISK_TR[inc.risk] ?? inc.risk}
                  </span>
                </div>
                <div className="text-sm font-medium text-zinc-200 mt-0.5">{inc.title}</div>
                <span className="text-[10px] text-zinc-500">
                  {PHASE_TR[inc.phase] ?? inc.phase}
                </span>
              </div>
            </div>
            {inc.needs_review && inc.review_reason && (
              <p className="text-[11px] text-amber-400/80 mt-1">⚑ {inc.review_reason}</p>
            )}
            <p className={`text-xs text-zinc-400 mt-1 whitespace-pre-line ${
              inc.incident_id === highlightId ? "" : "line-clamp-3"
            }`}>{inc.detail}</p>
          </button>
        ))}

        {reports.length > 0 && (
          <div className="pt-2 border-t border-zinc-800">
            <div className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1 px-1">
              Pencere raporları
            </div>
            {groupRows(reports).map((row) =>
              row.kind === "report"
                ? <ReportRow key={row.report.window_start} r={row.report} />
                : <QuietGroup key={`q${row.reports[0].window_start}`} reports={row.reports} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
