import { memo, useEffect, useState } from "react";
import type { ActuatorRequest, ActuatorResult } from "../types/events";
import ClampText from "./ClampText";
import { categoryLabel, severityClass, severityLabel } from "../lib/labels";

const FALLBACK_LABELS: Record<string, string> = {
  emniyet_bildirimi_hazirla: "Emniyet bildirimi",
  acil_saglik_bildirimi_hazirla: "Acil sağlık bildirimi",
  guvenlik_uyarisi_hazirla: "Güvenlik uyarısı",
  alan_guvenligi_iste: "Alan güvenliği talebi",
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

interface StartableSuggestion {
  action: string;
  label: string;
  status: string;
}

interface StartableItem {
  key: string;
  feed: string;
  live: boolean;
  title: string;
  incident_id: string;
  operator_category: string;
  suggested_actions?: StartableSuggestion[];
}

export function ManualActionStrip({ scopeLive, feedNames = {} }: {
  scopeLive: boolean;
  feedNames?: Record<string, string>;
}) {
  const [confirmed, setConfirmed] = useState<StartableItem[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const response = await fetch("/api/triage");
        if (!response.ok) throw new Error();
        const snap = await response.json();
        if (alive) {
          setConfirmed((snap.confirmed ?? []).filter(
            (item: StartableItem) => item.live === scopeLive,
          ));
        }
      } catch {
      }
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => { alive = false; clearInterval(id); };
  }, [scopeLive]);

  const requestAction = async (item: StartableItem, action: string) => {
    setError("");
    let response: Response;
    try {
      response = await fetch("/api/actions/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          incident_id: item.incident_id,
          feed: item.feed,
        }),
      });
    } catch {
      setError("Aksiyon taslağı sunucuya iletilemedi.");
      return;
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.detail || "Aksiyon taslağı istenemedi.");
    }
  };

  const startable = confirmed.filter(
    (item) => (item.suggested_actions ?? []).some((s) => s.status === "available"),
  );
  if (startable.length === 0 && !error) return null;
  return (
    <div className="shrink-0 space-y-1 border-b border-zinc-800 p-1.5 text-xs">
      <div className="microlabel">Doğrulanan olay · yerel taslak başlat</div>
      {error && (
        <div className="rounded-sm border border-red-900 bg-red-950/40 px-2 py-1 text-red-200">
          {error}
        </div>
      )}
      {startable.map((item) => (
        <div key={item.key} className="flex flex-wrap items-center gap-1">
          <span className="min-w-0 flex-1 truncate text-zinc-300" title={item.title}>
            {categoryLabel(item.operator_category)}
            {" · "}{feedNames[item.feed] || item.feed || "ana akış"}
          </span>
          {(item.suggested_actions ?? [])
            .filter((suggestion) => suggestion.status === "available")
            .map((suggestion) => (
              <button
                key={suggestion.action}
                onClick={() => requestAction(item, suggestion.action)}
                className="btn btn-sm btn-outline-warn"
                title="Yalnız operatör onayına gidecek yerel taslak isteği oluşturur"
              >
                + {suggestion.label}
              </button>
            ))}
        </div>
      ))}
    </div>
  );
}

export function pendingActionCount(
  requests: ActuatorRequest[], results: ActuatorResult[],
): number {
  const resolved = new Set(results.map((result) => result.request_id));
  return requests.filter((request) => !resolved.has(request.request_id)).length;
}

function ActionLog({
  requests, results, onRespond, bare = false, manualScope, feedNames,
}: {
  requests: ActuatorRequest[];
  results: ActuatorResult[];
  onRespond: (request_id: string, approved: boolean) => void;
  bare?: boolean;
  manualScope?: "live" | "analysis";
  feedNames?: Record<string, string>;
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
                {state && (
                  <span className={`ml-auto text-[10px] font-bold ${state.cls}`}>{state.text}</span>
                )}
              </div>

              <div className="flex flex-wrap gap-1">
                <span className="chip chip-notr">
                  {request.feed || "ana kamera"}
                </span>
                <span className="chip chip-notr">
                  {categoryLabel(request.anomaly_type)}
                </span>
                {request.risk && (
                  <span className={`chip ${severityClass(request.risk)}`}>
                    {severityLabel(request.risk)}
                  </span>
                )}
              </div>

              <ClampText text={request.incident_title} lines={1} className="text-xs text-zinc-300" />
              <p className="text-xs text-zinc-400">{request.reason}</p>
              {(request.evidence_timestamps ?? []).length > 0 && (
                <p className="font-mono text-[10px] text-zinc-500">
                  Video kanıtı: {(request.evidence_timestamps ?? []).map(clock).join(", ")}
                </p>
              )}

              {!result && (
                <div className="flex gap-1.5">
                  <button
                    onClick={() => onRespond(request.request_id, true)}
                    className="btn btn-sm btn-primary"
                  >
                    {submitLabel}
                  </button>
                  <button
                    onClick={() => onRespond(request.request_id, false)}
                    className="btn btn-sm btn-outline"
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

  const strip = manualScope
    ? <ManualActionStrip scopeLive={manualScope === "live"} feedNames={feedNames} />
    : null;

  if (bare) return <>{strip}{body}</>;

  return (
    <div className="panel h-full flex-1">
      <div className="panel-title">
        <span>Aksiyon Günlüğü</span>
        {pendingCount > 0 && (
          <span className="chip border border-amber-900 bg-amber-950/40 font-mono normal-case tracking-normal text-amber-300">
            {pendingCount} beklemede
          </span>
        )}
      </div>
      {strip}
      {body}
    </div>
  );
}

export default memo(ActionLog);
