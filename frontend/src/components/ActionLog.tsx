import type { ActuatorRequest, ActuatorResult } from "../types/events";

/** Aksiyon günlüğü + insan-onaylı aktüatörler (human-in-the-loop). */
export default function ActionLog({
  requests, results, onRespond,
}: {
  requests: ActuatorRequest[];
  results: ActuatorResult[];
  onRespond: (request_id: string, approved: boolean) => void;
}) {
  const resolved = new Map(results.map((r) => [r.request_id, r]));

  return (
    <div className="panel h-full">
      <div className="panel-title">Aksiyon Günlüğü</div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {requests.length === 0 && (
          <p className="text-xs text-zinc-600 p-2">Bekleyen aksiyon yok.</p>
        )}
        {requests.map((req) => {
          const res = resolved.get(req.request_id);
          return (
            <div key={req.request_id} className="rounded border border-zinc-800 bg-zinc-900 p-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm text-amber-300">{req.actuator}()</span>
                {res ? (
                  <span
                    className={`ml-auto text-xs font-bold ${
                      res.approved ? "text-emerald-400" : "text-red-400"
                    }`}
                  >
                    {res.approved ? "ONAYLANDI" : "REDDEDİLDİ"}
                  </span>
                ) : (
                  <div className="ml-auto flex gap-1">
                    <button
                      onClick={() => onRespond(req.request_id, true)}
                      className="rounded bg-emerald-800 hover:bg-emerald-700 px-2 py-0.5 text-xs"
                    >
                      Onayla
                    </button>
                    <button
                      onClick={() => onRespond(req.request_id, false)}
                      className="rounded bg-red-900 hover:bg-red-800 px-2 py-0.5 text-xs"
                    >
                      Reddet
                    </button>
                  </div>
                )}
              </div>
              <p className="text-xs text-zinc-400 mt-1">{req.reason}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
