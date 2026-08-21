import type { ActuatorRequest, ActuatorResult } from "../types/events";

const FALLBACK_LABELS: Record<string, string> = {
  emniyet_bildirimi_hazirla: "Emniyet bildirimi",
  acil_saglik_bildirimi_hazirla: "Acil sağlık bildirimi",
  guvenlik_uyarisi_hazirla: "Güvenlik uyarısı",
  alan_guvenligi_iste: "Alan güvenliği talebi",
};

const RISK_CLASS: Record<string, string> = {
  dusuk: "bg-sky-950 text-sky-300",
  orta: "bg-amber-950 text-amber-300",
  yuksek: "bg-orange-950 text-orange-300",
  kritik: "bg-red-950 text-red-300",
};

const clock = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

function resultLabel(result: ActuatorResult): { text: string; cls: string } {
  if (result.status === "prepared") return { text: "TASLAK HAZIR", cls: "text-emerald-400" };
  if (result.status === "failed") return { text: "HAZIRLANAMADI", cls: "text-red-400" };
  return { text: "VAZGEÇİLDİ", cls: "text-zinc-400" };
}

export default function ActionLog({
  requests, results, onRespond, readOnly = false,
}: {
  requests: ActuatorRequest[];
  results: ActuatorResult[];
  onRespond: (request_id: string, approved: boolean) => void;
  readOnly?: boolean;
}) {
  const resolved = new Map(results.map((result) => [result.request_id, result]));

  return (
    <div className="panel h-full">
      <div className="panel-title flex items-center justify-between">
        <span>Aksiyon Günlüğü</span>
        <span className="text-[10px] font-normal text-zinc-500">
          Dış aksiyonlar yerel taslak olarak hazırlanır
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {requests.length === 0 && (
          <p className="text-xs text-zinc-600 p-2">Aksiyon kaydı yok.</p>
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
                 className="rounded border border-zinc-800 bg-zinc-900 p-2 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-amber-200">{label}</span>
                {state ? (
                  <span className={`ml-auto text-xs font-bold ${state.cls}`}>{state.text}</span>
                ) : (
                  <span className="ml-auto text-[10px] text-amber-400">
                    {cardReadOnly ? "TEMSİLİ KART" : "OPERATÖR KARARI BEKLİYOR"}
                  </span>
                )}
              </div>

              <div className="flex flex-wrap gap-1 text-[10px]">
                <span className="rounded bg-zinc-800 px-1.5 py-0.5">
                  {request.feed || "ana kamera"}
                </span>
                <span className="rounded bg-zinc-800 px-1.5 py-0.5">
                  {request.anomaly_type}
                </span>
                {request.risk && (
                  <span className={`rounded px-1.5 py-0.5 ${RISK_CLASS[request.risk] ?? "bg-zinc-800"}`}>
                    {request.risk} risk
                  </span>
                )}
                {request.incident_id && (
                  <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono">
                    {request.incident_id}
                  </span>
                )}
              </div>

              {request.incident_title && (
                <p className="text-xs text-zinc-300">{request.incident_title}</p>
              )}
              <p className="text-xs text-zinc-400">{request.reason}</p>
              {request.evidence_timestamps.length > 0 && (
                <p className="text-[10px] text-zinc-500">
                  Video kanıtı: {request.evidence_timestamps.map(clock).join(", ")}
                </p>
              )}

              {!result && cardReadOnly && (
                <p className="text-[10px] text-amber-300">
                  Arayüz test akışında işlem düğmeleri kapalıdır.
                </p>
              )}

              {!result && !cardReadOnly && (
                <div className="flex gap-1">
                  <button
                    onClick={() => onRespond(request.request_id, true)}
                    className="rounded bg-emerald-800 hover:bg-emerald-700 px-2 py-1 text-xs"
                  >
                    {submitLabel}
                  </button>
                  <button
                    onClick={() => onRespond(request.request_id, false)}
                    className="rounded bg-zinc-800 hover:bg-zinc-700 px-2 py-1 text-xs"
                  >
                    Vazgeç
                  </button>
                </div>
              )}

              {result && (
                <div className="rounded border border-zinc-800 bg-zinc-950/60 p-2 space-y-1">
                  <p className="text-xs text-zinc-300">{result.detail}</p>
                  {result.status === "prepared" && (
                    <p className="text-[10px] font-bold text-amber-300">
                      DEMO MODU · DIŞ KURUMA İLETİLMEDİ
                    </p>
                  )}
                  {result.artifact_url && (
                    <a href={result.artifact_url}
                       className="inline-block text-xs text-sky-400 hover:text-sky-300 underline">
                      Hazırlanan taslağı indir
                    </a>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
