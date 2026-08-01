import { useEffect, useRef } from "react";
import type { TraceEntry } from "../state";

/** Ajan izleme konsolu — her düğüm geçişi ve araç çağrısı gerekçesiyle akar.
 *  Jüri açıklanabilirlik kriterinin görünür yüzü. */
export default function AgentTrace({ entries }: { entries: TraceEntry[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [entries.length]);

  return (
    <div className="panel h-full">
      <div className="panel-title">Ajan İzleme</div>
      <div className="flex-1 overflow-y-auto p-2 font-mono text-xs space-y-1">
        {entries.map((e) => (
          <div key={e.seq} className="leading-relaxed">
            {e.kind === "step" && e.step && (
              <span>
                <span className={e.step.status === "error" ? "text-red-400" : "text-emerald-400"}>
                  {e.step.status === "start" ? "▶" : e.step.status === "end" ? "✔" : "✖"}
                </span>{" "}
                <span className="text-sky-400">{e.step.node}</span>
                {e.step.detail && <span className="text-zinc-400"> — {e.step.detail}</span>}
              </span>
            )}
            {e.kind === "tool" && e.tool && (
              <span>
                <span className="text-amber-400">⚙ {e.tool.tool}</span>
                <span className="text-zinc-500">({JSON.stringify(e.tool.args)})</span>
                {e.tool.rationale && (
                  <span className="text-zinc-400 italic"> · gerekçe: {e.tool.rationale}</span>
                )}
                {e.tool.result && <span className="text-zinc-300"> → {e.tool.result}</span>}
              </span>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
