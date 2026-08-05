import { useEffect, useRef, useState } from "react";
import type { TraceEntry } from "../state";

/** Ajan izleme konsolu — düğüm geçişleri ve araç çağrıları gerekçesiyle akar.
 *  Jüri açıklanabilirlik kriterinin görünür yüzü.
 *
 *  ⚠ Ham akış OKUNAMIYORDU: her pencere 4 satır üretiyordu (interpret ▶/✔ +
 *  ledger ▶/✔) ve saatlik kayıtta bunların yüzlercesi "0 olay" diyordu; gerçek
 *  bulgular arada kayboluyordu. Bu yüzden:
 *   1) ▶başlangıç/✔bitiş çiftleri TEK satıra indirgenir (sonuç neyse o yazılır),
 *   2) eski anlamsız `ledger` sayaç satırları atılır (backend artık
 *      NE DEĞİŞTİĞİNİ yazıyor: açıldı/genişledi/kapandı + tolerans),
 *   3) ardışık olaysız pencereler katlanır — tıklayınca açılır.
 *  Hata satırları ve olay üreten satırlar HER ZAMAN görünür kalır.
 *  ⚠ Bu kip bilgi ATAR; akışı hata ayıklamak için **detay** düğmesi ham
 *  akışı (sıra no + ▶/✔ ayrı, hiçbir satır gizli değil) gösterir.
 */

type StepRow = { kind: "step"; seq: number; node: string; status: string; detail: string };
type Row =
  | StepRow
  | { kind: "tool"; seq: number; entry: TraceEntry }
  | { kind: "quiet"; seq: number; rows: { seq: number; detail: string }[] };

const QUIET = /atlandı|^0 olay$/;              // olaysız pencere imzaları

function build(entries: TraceEntry[]): Row[] {
  const merged: Row[] = [];
  for (const e of entries) {
    if (e.kind === "tool") { merged.push({ kind: "tool", seq: e.seq, entry: e }); continue; }
    const s = e.step;
    if (!s) continue;
    // Defterin "N olay defterde" satırları yalnız SAYAÇ — asıl haber
    // incident_update olayları ve zaman çizelgesi kartlarıdır. Bunlar hem
    // gürültü yapıyor hem de ardışık sessiz pencerelerin katlanmasını bölüyordu.
    if (s.node === "ledger" && s.status !== "error" &&
        (!s.detail || /\d+\s*olay defterde/.test(s.detail))) continue;

    if (s.status === "start") {
      merged.push({ kind: "step", seq: e.seq, node: s.node, status: "start", detail: s.detail });
      continue;
    }
    // ✔/✖ geldiğinde aynı düğümün bekleyen ▶ satırının yerine geçer
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

function QuietRow({ rows }: { rows: { seq: number; detail: string }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen((o) => !o)}
              className="w-full text-left hover:bg-zinc-800/40 rounded px-1 text-zinc-600">
        ✔ <span className="text-sky-800">interpret</span> — {rows.length} pencere, olay yok{" "}
        <span>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="pl-3 border-l border-zinc-800 ml-1">
          {rows.map((r) => <div key={r.seq} className="text-zinc-600">— {r.detail}</div>)}
        </div>
      )}
    </div>
  );
}

export default function AgentTrace({ entries }: { entries: TraceEntry[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  // "detay" = hata ayıklama kipi: hiçbir satır gizlenmez/katlanmaz, ▶/✔ ayrı
  // durur. Varsayılan kipin okunabilirliği için bilgi ATILIYOR — akışı gerçekten
  // izlemek gerektiğinde (hangi pencere, neden, defter ne karar verdi) bu açılır.
  const [verbose, setVerbose] = useState(false);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [entries.length]);
  const rows = build(entries);

  return (
    <div className="panel h-full">
      <div className="panel-title flex items-center gap-2">
        Ajan İzleme
        <button
          onClick={() => setVerbose((v) => !v)}
          title="Ham akış: hiçbir satır gizlenmez (hata ayıklama)"
          className={`ml-auto normal-case font-normal rounded px-1.5 py-0.5 border text-[10px] ${
            verbose ? "border-amber-700 text-amber-300 bg-amber-950/40"
                    : "border-zinc-700 text-zinc-500 hover:border-zinc-500"
          }`}
        >
          {verbose ? "detay ●" : "detay"}
        </button>
      </div>
      {verbose && (
        <div className="flex-1 overflow-y-auto p-2 font-mono text-[11px] space-y-0.5">
          {entries.map((e) => (
            <div key={e.seq} className="leading-relaxed">
              <span className="text-zinc-700 mr-1">{String(e.seq).padStart(4, "0")}</span>
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
          <div ref={endRef} />
        </div>
      )}
      {!verbose && (
      <div className="flex-1 overflow-y-auto p-2 font-mono text-xs space-y-0.5">
        {rows.map((r) =>
          r.kind === "quiet" ? (
            <QuietRow key={`q${r.seq}`} rows={r.rows} />
          ) : r.kind === "tool" && r.entry.tool ? (
            <div key={r.seq} className="leading-relaxed">
              <span className="text-amber-400">⚙ {r.entry.tool.tool}</span>
              <span className="text-zinc-500">({JSON.stringify(r.entry.tool.args)})</span>
              {r.entry.tool.rationale && (
                <span className="text-zinc-400 italic"> · gerekçe: {r.entry.tool.rationale}</span>
              )}
              {r.entry.tool.result && (
                <span className="text-zinc-300"> → {r.entry.tool.result}</span>
              )}
            </div>
          ) : r.kind === "step" ? (
            <div key={r.seq} className="leading-relaxed">
              <span className={r.status === "error" ? "text-red-400"
                             : r.status === "start" ? "text-zinc-500" : "text-emerald-400"}>
                {r.status === "start" ? "▶" : r.status === "end" ? "✔" : "✖"}
              </span>{" "}
              <span className="text-sky-400">{r.node}</span>
              {r.detail && <span className="text-zinc-400"> — {r.detail}</span>}
            </div>
          ) : null
        )}
        <div ref={endRef} />
      </div>
      )}
    </div>
  );
}
