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
        <p className="mt-0.5 text-[10px] text-amber-500/70">? {r.uncertainties[0]}</p>
      )}
    </div>
  );
}

function QuietGroup({ reports }: { reports: WindowReport[] }) {
  const [open, setOpen] = useState(false);
  const first = reports[0], last = reports[reports.length - 1];
  return (
    <div className="px-1 py-0.5">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-baseline gap-2 rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-zinc-800/60"
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
        <div className="ml-1 mt-1 border-l border-zinc-800 pl-2">
          {reports.map((r) => (
            <div key={r.window_start} className="py-0.5">
              <span className="mr-2 font-mono text-[10px] text-zinc-700">
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
      <div className="panel-title">
        <span>Olay Zaman Çizelgesi</span>
        <span className="flex-1" />
        {incidents.length > 0 && (
          <span className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-300">
            {incidents.length}
          </span>
        )}
      </div>
      <div className="panel-body space-y-1.5 p-2">
        {incidents.length === 0 && reports.length === 0 && (
          <p className="p-2 text-xs text-zinc-600">Henüz olay yok — akış bekleniyor.</p>
        )}

        {incidents.map((inc) => (
          <button
            key={inc.incident_id}
            onClick={() => onSelect(inc)}
            title="Videoyu bu ana sar"
            className={`w-full rounded-sm border-l-2 bg-zinc-950 p-2 text-left transition-colors risk-${inc.risk}
                        hover:bg-zinc-800/60 ${
              inc.incident_id === highlightId ? "ring-1 ring-zinc-500" : ""
            }`}
          >
            <div className="flex gap-2">
              {inc.thumbnail && (
                <img src={inc.thumbnail} alt=""
                     className="h-12 w-12 shrink-0 rounded-sm bg-black object-cover" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span className="font-mono text-xs text-zinc-500">{clock(inc.t)}</span>
                  <span className="chip bg-zinc-800 font-semibold uppercase tracking-wide text-zinc-300">
                    {TYPE_TR[inc.anomaly_type] ?? inc.anomaly_type}
                  </span>
                  {inc.needs_review && (
                    <span className="chip border border-amber-900 bg-amber-950/60 font-semibold uppercase tracking-wide text-amber-300">
                      ⚑ inceleme
                    </span>
                  )}
                  <span className={`ml-auto text-[10px] font-bold uppercase risk-${inc.risk}`}>
                    {RISK_TR[inc.risk] ?? inc.risk}
                  </span>
                </div>
                <div className="mt-0.5 text-sm font-medium text-zinc-200">{inc.title}</div>
                <span className="text-[10px] text-zinc-500">
                  {PHASE_TR[inc.phase] ?? inc.phase}
                </span>
              </div>
            </div>
            {inc.needs_review && inc.review_reason && (
              <p className="mt-1 text-[11px] text-amber-400/80">⚑ {inc.review_reason}</p>
            )}
            <p className={`mt-1 whitespace-pre-line text-xs text-zinc-400 ${
              inc.incident_id === highlightId ? "" : "line-clamp-3"
            }`}>{inc.detail}</p>
          </button>
        ))}

        {reports.length > 0 && (
          <div className="border-t border-zinc-800 pt-2">
            <div className="microlabel sticky top-0 mb-1 bg-zinc-900 px-1 py-0.5">
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
