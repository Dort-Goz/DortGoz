import { useState } from "react";
import { reviewEvent } from "../lib/api";
import type { VerifiedEvent } from "../types/domain";

export default function EventDetail({ events, onSeek, onReviewed }: { events: VerifiedEvent[]; onSeek: (time: number) => void; onReviewed: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState("Operatör evidence'i inceledi.");
  const event = events.find((item) => item.event_id === selected) ?? events[0];
  const review = async (decision: "confirm" | "reject") => { if (!event) return; await reviewEvent(event.event_id, decision, note); onReviewed(); };
  return <div className="panel h-full">
    <div className="panel-title">Canonical event ayrıntısı <span className="normal-case font-normal text-zinc-600">{events.length}</span></div>
    <div className="flex-1 overflow-y-auto p-2 space-y-2">
      {events.length === 0 && <p className="text-xs text-zinc-600">REST analizi tamamlandığında evidence burada görünür.</p>}
      {events.map((item) => <button key={item.event_id} onClick={() => { setSelected(item.event_id); if (item.peak_time != null) onSeek(item.peak_time); }} className={`w-full text-left rounded border p-2 ${item.event_id === event?.event_id ? "border-amber-600" : "border-zinc-800"}`}>
        <span className="text-xs font-medium">{item.event_type}</span><span className="ml-2 text-[10px] text-zinc-500">{item.status}</span>
        <span className="float-right text-[10px] text-amber-300">{item.risk?.level ?? "risk yok"}</span>
      </button>)}
      {event && <div className="text-xs space-y-2 border-t border-zinc-800 pt-2">
        <p>{event.during ?? event.before ?? "Anlatı kaydı yok."}</p>
        {event.evidence.map((item) => <button key={item.evidence_id} onClick={() => onSeek(item.timestamp)} className="block w-full text-left rounded bg-zinc-800/70 p-2"><span className="font-mono text-zinc-500">{item.timestamp.toFixed(1)} sn</span> · {item.claim}</button>)}
        {event.risk && <p className="text-amber-200">{event.risk.reasons.join(" ")} <span className="text-zinc-500">{event.risk.rule_ids.join(", ")}</span></p>}
        {event.actions.map((action) => <p key={`${action.document_id}-${action.section}`} className="text-zinc-400">Öneri: {action.action} <span className="text-zinc-600">[{action.document_id} §{action.section} / {action.version}]</span></p>)}
        {event.decision_trace.length > 0 && <details><summary className="cursor-pointer text-zinc-500">Agent trace ({event.decision_trace.length})</summary><div className="mt-1 space-y-1 text-[10px] text-zinc-500">{event.decision_trace.map((step) => <p key={step.step}>#{step.step} {step.action} · {step.policy_rule_id} · {step.tool_name ?? "policy"} {step.success === false ? `· hata: ${step.error_code ?? "bilinmiyor"}` : ""}</p>)}</div></details>}
        {event.status === "human_review" && <div className="flex gap-1"><input value={note} onChange={(e) => setNote(e.target.value)} className="min-w-0 flex-1 rounded bg-zinc-800 p-1" /><button onClick={() => review("confirm")} className="rounded bg-emerald-700 px-2">Onayla</button><button onClick={() => review("reject")} className="rounded bg-red-700 px-2">Reddet</button></div>}
      </div>}
    </div>
  </div>;
}
