import { useState } from "react";
import { queryAnalysis } from "../lib/api";
import type { QueryResponse } from "../types/domain";

export default function QueryPanel({ analysisId }: { analysisId: string }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    if (!question.trim()) return;
    try { setError(null); setResult(await queryAnalysis(analysisId, question)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Sorgu başarısız."); }
  };
  return <div className="panel h-full">
    <div className="panel-title">Olay sorgusu</div>
    <div className="flex-1 overflow-y-auto p-2 space-y-2">
      <div className="flex gap-1"><input value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void submit()} placeholder="Örn. confirmed" className="min-w-0 flex-1 rounded bg-zinc-800 p-2 text-xs" /><button onClick={() => void submit()} className="rounded bg-zinc-700 px-2 text-xs">Sor</button></div>
      {error && <p className="text-xs text-red-300">{error}</p>}
      {result && <><p className="text-xs">{result.answer_tr}</p><p className="text-[10px] text-zinc-500">Event: {result.event_refs.join(", ") || "—"}</p>{result.procedure_sources.map((source) => <p key={`${source.document_id}-${source.section}`} className="text-[10px] text-amber-200">Kaynak: {source.document_id} §{source.section} / {source.version}</p>)}</>}
    </div>
  </div>;
}
