import { memo, useMemo, useState } from "react";
import type { TraceEntry } from "../state";
import { NODE_TR, humanizeEnums, stripPerf, toolLabel } from "../lib/labels";
import { useStickyScroll } from "../lib/useStickyScroll";

type StepRow = { kind: "step"; seq: number; node: string; status: string; detail: string };
type Row =
  | StepRow
  | { kind: "tool"; seq: number; entry: TraceEntry }
  | { kind: "quiet"; seq: number; rows: { seq: number; detail: string }[] };

const QUIET = /atlandı|^0 olay$/;

function build(entries: TraceEntry[]): Row[] {
  const merged: Row[] = [];
  for (const e of entries) {
    if (e.kind === "tool") { merged.push({ kind: "tool", seq: e.seq, entry: e }); continue; }
    const s = e.step;
    if (!s) continue;
    if (s.node === "ledger" && s.status !== "error" &&
        (!s.detail || /\d+\s*olay defterde/.test(s.detail))) continue;

    if (s.status === "start") {
      merged.push({ kind: "step", seq: e.seq, node: s.node, status: "start", detail: s.detail });
      continue;
    }
    let idx = -1;
    for (let i = merged.length - 1; i >= 0; i--) {
      const r = merged[i];
      if (r.kind === "step" && r.node === s.node && r.status === "start") { idx = i; break; }
    }
    if (idx !== -1) {
      const prev = merged[idx] as StepRow;
      merged[idx] = { kind: "step", seq: prev.seq, node: s.node, status: s.status,
                      detail: s.detail || prev.detail };
    } else {
      merged.push({ kind: "step", seq: e.seq, node: s.node, status: s.status, detail: s.detail });
    }
  }

  const rows: Row[] = [];
  for (const r of merged) {
    const quiet = r.kind === "step" && r.status === "end" && r.node === "interpret" &&
                  QUIET.test(r.detail.trim());
    if (!quiet) { rows.push(r); continue; }
    const last = rows[rows.length - 1];
    const step = r as StepRow;
    if (last?.kind === "quiet") last.rows.push({ seq: step.seq, detail: step.detail });
    else rows.push({ kind: "quiet", seq: step.seq,
                     rows: [{ seq: step.seq, detail: step.detail }] });
  }
  return rows;
}

const QuietRow = memo(function QuietRow(
  { rows }: { rows: { seq: number; detail: string }[] },
) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen((o) => !o)}
              className="w-full rounded-sm px-1 text-left text-zinc-600 transition-colors hover:bg-zinc-800/60">
        ✔ <span className="text-sky-800">yorum</span> — {rows.length} pencere, olay yok{" "}
        <span>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="ml-1 border-l border-zinc-800 pl-3">
          {rows.map((r) => <div key={r.seq} className="text-zinc-600">— {r.detail}</div>)}
        </div>
      )}
    </div>
  );
});

function AgentTrace({ entries }: { entries: TraceEntry[] }) {
  const [verbose, setVerbose] = useState(false);
  const { ref, onScroll } = useStickyScroll<HTMLDivElement>(entries.length);
  const rows = useMemo(() => build(entries), [entries]);

  return (
    <div className="panel h-full">
      <div className="panel-title">
        <span>Ajan İzleme</span>
        <span className="flex-1" />
        <button
          onClick={() => setVerbose((v) => !v)}
          title="Ham akış: hiçbir satır gizlenmez (hata ayıklama)"
          className={`btn btn-sm normal-case tracking-normal ${
            verbose ? "btn-outline-warn" : "btn-outline"
          }`}
        >
          {verbose ? "detay ●" : "detay"}
        </button>
      </div>
      {verbose && (
        <div ref={ref} onScroll={onScroll}
             className="panel-body space-y-0.5 p-2 font-mono text-[11px] leading-5">
          {entries.map((e) => (
            <div key={e.seq}>
              <span className="mr-1 text-zinc-700">{String(e.seq).padStart(4, "0")}</span>
              {e.kind === "step" && e.step && (
                <>
                  <span className={e.step.status === "error" ? "text-red-400"
                                 : e.step.status === "start" ? "text-zinc-500"
                                 : "text-emerald-400"}>
                    {e.step.status === "start" ? "▶" : e.step.status === "end" ? "✔" : "✖"}
                  </span>{" "}
                  <span className="text-sky-400">{e.step.node}</span>
                  {e.step.detail && <span className="text-zinc-400"> — {e.step.detail}</span>}
                </>
              )}
              {e.kind === "tool" && e.tool && (
                <>
                  <span className="text-amber-400">⚙ {e.tool.tool}</span>
                  <span className="text-zinc-500">({JSON.stringify(e.tool.args)})</span>
                </>
              )}
            </div>
          ))}
        </div>
      )}
      {!verbose && (
      <div ref={ref} onScroll={onScroll}
           className="panel-body space-y-0.5 p-2 font-mono text-[11px] leading-5">
        {entries.length === 0 && (
          <p className="p-2 font-sans text-xs text-zinc-600">Ajan adımı yok.</p>
        )}
        {rows.map((r) =>
          r.kind === "quiet" ? (
            <QuietRow key={`q${r.seq}`} rows={r.rows} />
          ) : r.kind === "tool" && r.entry.tool ? (
            <div key={r.seq} title={JSON.stringify(r.entry.tool.args)}>
              <span className="text-amber-400">⚙ {toolLabel(r.entry.tool.tool)}</span>
              {r.entry.tool.rationale && (
                <span className="text-zinc-400"> — {r.entry.tool.rationale}</span>
              )}
              {r.entry.tool.result && (
                <span className="text-zinc-300"> → {r.entry.tool.result}</span>
              )}
            </div>
          ) : r.kind === "step" ? (
            <div key={r.seq}>
              <span className={r.status === "error" ? "text-red-400"
                             : r.status === "start" ? "text-zinc-500" : "text-emerald-400"}>
                {r.status === "start" ? "▶" : r.status === "end" ? "✔" : "✖"}
              </span>{" "}
              <span className="text-sky-400">{NODE_TR[r.node] ?? r.node}</span>
              {r.detail && (
                <span className="text-zinc-400"> — {humanizeEnums(stripPerf(r.detail))}</span>
              )}
            </div>
          ) : null
        )}
      </div>
      )}
    </div>
  );
}

export default memo(AgentTrace);
