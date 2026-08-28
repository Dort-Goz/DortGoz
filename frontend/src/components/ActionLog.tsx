import { memo } from "react";
import type { ActuatorRequest, ActuatorResult } from "../types/events";

const FALLBACK_LABELS: Record<string, string> = {
  emniyet_bildirimi_hazirla: "Emniyet bildirimi",
  acil_saglik_bildirimi_hazirla: "Acil sağlık bildirimi",
  guvenlik_uyarisi_hazirla: "Güvenlik uyarısı",
  alan_guvenligi_iste: "Alan güvenliği talebi",
};

const RISK_CLASS: Record<string, string> = {
  dusuk: "border-sky-900 bg-sky-950 text-sky-300",
  orta: "border-amber-900 bg-amber-950 text-amber-300",
  yuksek: "border-orange-900 bg-orange-950 text-orange-300",
  kritik: "border-red-900 bg-red-950 text-red-300",
};

const clock = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

function resultLabel(result: ActuatorResult): { text: string; cls: string } {
  if (result.status === "prepared") return { text: "TASLAK HAZIR", cls: "text-emerald-400" };
  if (result.status === "failed") return { text: "HAZIRLANAMADI", cls: "text-red-400" };
  if (result.status === "rejected") return { text: "VAZGEÇİLDİ", cls: "text-zinc-400" };
  return result.approved
    ? { text: "ONAYLANDI", cls: "text-emerald-400" }
    : { text: "REDDEDİLDİ", cls: "text-zinc-400" };
}

export function pendingActionCount(
  requests: ActuatorRequest[], results: ActuatorResult[],
): number {
  const resolved = new Set(results.map((result) => result.request_id));
  return requests.filter((request) => !resolved.has(request.request_id)).length;
}

function ActionLog({
  requests, results, onRespond, readOnly = false, bare = false,
}: {
  requests: ActuatorRequest[];
  results: ActuatorResult[];
  onRespond: (request_id: string, approved: boolean) => void;
  readOnly?: boolean;
  bare?: boolean;
}) {
  const resolved = new Map(results.map((result) => [result.request_id, result]));
  const pendingCount = pendingActionCount(requests, results);

  const body = (
      <div className="panel-body space-y-1.5 p-2">
        {requests.length === 0 && (
          <p className="p-2 text-xs text-zinc-600">Aksiyon kaydı yok.</p>
        )}
        {requests.map((request) => {
          const result = resolved.get(request.request_id);
          const cardReadOnly = readOnly;
          const label = request.action_label || FALLBACK_LABELS[request.actuator] || "Aksiyon taslağı";
          const state = result ? resultLabel(result) : null;
          const submitLabel = request.actuator.includes("bildirimi")
            ? "Bildirimi hazırla"
            : "Talebi hazırla";
          return (
            <div key={request.request_id}
                 className="space-y-1.5 rounded-md border border-zinc-800 bg-zinc-950 p-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-amber-200">{label}</span>
                {state ? (
                  <span className={`ml-auto text-[10px] font-bold ${state.cls}`}>{state.text}</span>
                ) : (
                  <span className="ml-auto text-[10px] font-semibold text-amber-400">
                    {cardReadOnly ? "TEMSİLİ KART" : "OPERATÖR KARARI BEKLİYOR"}
                  </span>
                )}
              </div>

              <div className="flex flex-wrap gap-1">
                <span className="chip bg-zinc-800 text-zinc-300">
                  {request.feed || "ana kamera"}
                </span>
                <span className="chip bg-zinc-800 text-zinc-300">
                  {request.anomaly_type}
                </span>
                {request.risk && (
                  <span className={`chip border ${RISK_CLASS[request.risk] ?? "bg-zinc-800 text-zinc-300"}`}>
                    {request.risk} risk
                  </span>
                )}
                {request.incident_id && (
                  <span className="chip bg-zinc-800 font-mono text-zinc-400">
                    {request.incident_id}
                  </span>
                )}
              </div>

              {request.incident_title && (
                <p className="text-xs text-zinc-300">{request.incident_title}</p>
              )}
              <p className="text-xs text-zinc-400">{request.reason}</p>
              {(request.evidence_timestamps ?? []).length > 0 && (
                <p className="font-mono text-[10px] text-zinc-500">
                  Video kanıtı: {(request.evidence_timestamps ?? []).map(clock).join(", ")}
                </p>
              )}

              {!result && cardReadOnly && (
                <p className="text-[10px] text-amber-300">
                  Arayüz test akışında işlem düğmeleri kapalıdır.
                </p>
              )}

              {!result && !cardReadOnly && (
                <div className="flex gap-1.5">
                  <button
                    onClick={() => onRespond(request.request_id, true)}
                    className="btn btn-primary h-6"
                  >
                    {submitLabel}
                  </button>
                  <button
                    onClick={() => onRespond(request.request_id, false)}
                    className="btn btn-outline h-6"
                  >
                    Vazgeç
                  </button>
                </div>
              )}

              {result && (
                <div className="space-y-1 rounded-sm border border-zinc-800 bg-zinc-900 p-2">
                  <p className="text-xs text-zinc-300">{result.detail}</p>
                  {result.status === "prepared" && (
                    <p className="text-[10px] font-bold text-amber-300">
                      DEMO MODU · DIŞ KURUMA İLETİLMEDİ
                    </p>
                  )}
                  {result.artifact_url && (
                    <a href={result.artifact_url}
                       className="inline-block text-xs text-sky-400 underline hover:text-sky-300">
                      Hazırlanan taslağı indir
                    </a>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
  );

  if (bare) return body;

  return (
    <div className="panel h-full flex-1">
      <div className="panel-title">
        <span>Aksiyon Günlüğü</span>
        {pendingCount > 0 && !readOnly && (
          <span className="chip border border-amber-900 bg-amber-950/40 font-mono normal-case tracking-normal text-amber-300">
            {pendingCount} beklemede
          </span>
        )}
      </div>
      {body}
    </div>
  );
}

export default memo(ActionLog);
