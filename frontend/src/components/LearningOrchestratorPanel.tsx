import { useCallback, useEffect, useState } from "react";
import { getLearningOrchestratorOverview } from "../lib/api";
import { CANONICAL_TYPE_TR } from "../lib/labels";
import type {
  DevelopmentUse,
  LearningOrchestratorOverview,
} from "../types/domain";
import type { CanonicalEventType } from "../types/events";

const USE_TR: Record<DevelopmentUse, string> = {
  camera_rule: "Süreli kamera kuralı",
  prompt_example: "İstem örneği",
  threshold_calibration: "Eşik kalibrasyonu",
  siglip_training: "SigLIP aday havuzu",
  d_fine_training: "D-FINE kare havuzu",
  evaluation: "Sabit değerlendirme",
};

const DRIFT_TR = {
  insufficient_data: "Ölçüm için veri bekleniyor",
  stable: "Kararlı",
  watch: "İzleme gerekli",
  drift: "Kayma sinyali",
} as const;

const BAND_TR = {
  low: "Düşük",
  medium: "Orta",
  high: "Yüksek",
  priority: "Öncelikli",
} as const;

export default function LearningOrchestratorPanel({
  onClose,
  onOpenEvent,
}: {
  onClose: () => void;
  onOpenEvent: (eventId: string) => void;
}) {
  const [overview, setOverview] = useState<LearningOrchestratorOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setOverview(await getLearningOrchestratorOverview());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Orkestratör durumu alınamadı.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <section className="flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-md border border-zinc-800 bg-zinc-950 shadow-2xl">
        <header className="flex h-12 shrink-0 items-center gap-3 border-b border-zinc-800 px-4">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Öğrenme Merkezi</h2>
            <p className="text-[10px] text-zinc-500">
              İnsan onaylı geliştirme akışları · salt-okunur yönlendirme · gölge kayma gözcüsü
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="btn btn-outline"
            >
              {loading ? "Yenileniyor…" : "Yenile"}
            </button>
            <button type="button" onClick={onClose} className="btn btn-ghost">
              Kapat ×
            </button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 text-xs">
          {error && (
            <div className="mb-3 rounded-md border border-red-900 bg-red-950/40 p-2.5 text-xs text-red-200">
              {error}
            </div>
          )}
          {loading && !overview && <p className="text-zinc-500">Sistem durumu okunuyor…</p>}

          {overview && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                {[
                  ["Toplam olay", overview.total_events],
                  ["İncelenen", overview.reviewed_events],
                  ["İnceleme bekleyen", overview.pending_review_events],
                  ["İzin bekleyen", overview.pending_approval_events],
                  ["Eski izin", overview.stale_approval_events],
                  ["Hazır rota", overview.ready_routes],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-md border border-zinc-800 bg-zinc-900 p-2">
                    <div className="microlabel">{label}</div>
                    <div className="mt-1 font-mono text-lg font-semibold text-zinc-100">{value}</div>
                  </div>
                ))}
              </div>

              <div className="grid gap-3 lg:grid-cols-[18rem_minmax(0,1fr)]">
                <section className="rounded-md border border-sky-900 bg-zinc-900 p-2.5">
                  <div className="flex items-center justify-between">
                    <h3 className="font-medium text-sky-100">Kayma gözcüsü</h3>
                    <span className="chip border border-sky-900 bg-sky-950/40 font-mono text-sky-200">
                      {overview.drift.score}/100
                    </span>
                  </div>
                  <p className="mt-1 text-zinc-400">{DRIFT_TR[overview.drift.state]}</p>
                  <p className="text-[10px] text-zinc-600">
                    {overview.drift.reviewed_events}/{overview.drift.minimum_required} insan incelemesi · kip: gölge
                  </p>
                  <div className="mt-2 space-y-1">
                    {overview.drift.metrics.map((metric) => (
                      <div key={metric.name} className="rounded-md border border-zinc-800 bg-zinc-950 p-1.5">
                        <div className="flex justify-between text-zinc-300">
                          <span>{metric.detail}</span>
                          <span>{metric.points} puan</span>
                        </div>
                        <div className="text-[9px] text-zinc-600">
                          {metric.baseline.toFixed(3)} → {metric.current.toFixed(3)}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>

                <section className="rounded-md border border-zinc-800 bg-zinc-900 p-2.5">
                  <h3 className="mb-2 font-medium text-zinc-200">Öğrenme rotaları</h3>
                  <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                    {overview.route_summaries.map((route) => (
                      <div key={route.use} className="rounded-md border border-zinc-800 bg-zinc-950 p-2">
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-zinc-200">{USE_TR[route.use]}</span>
                          <span className="text-emerald-400">{route.ready_count} hazır</span>
                        </div>
                        <p className="mt-1 text-[10px] text-zinc-500">
                          {route.recommended_count} öneri · {route.awaiting_gate_count} kapı bekliyor
                        </p>
                        <p className="mt-1 text-[9px] leading-relaxed text-zinc-600">
                          {route.downstream} · {route.safety_gate}
                        </p>
                      </div>
                    ))}
                  </div>
                </section>
              </div>

              <section className="rounded-md border border-zinc-800 bg-zinc-900 p-2.5">
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="font-medium text-zinc-200">Öncelikli inceleme kuyruğu</h3>
                  <span className="text-[10px] text-zinc-600">Öğrenme değeri, müdahale önceliğinden ayrıdır.</span>
                </div>
                <div className="space-y-1.5">
                  {overview.priority_candidates.length === 0 && (
                    <p className="text-zinc-500">Henüz olay yok.</p>
                  )}
                  {overview.priority_candidates.map((candidate) => (
                    <button
                      key={candidate.event_id}
                      type="button"
                      onClick={() => onOpenEvent(candidate.event_id)}
                      className="grid w-full grid-cols-[5rem_minmax(0,1fr)_auto] items-center gap-3 rounded-md border border-zinc-800 bg-zinc-950 p-2 text-left transition-colors hover:border-sky-800"
                    >
                      <span className="text-center">
                        <span className="block font-mono text-lg font-semibold text-sky-200">
                          {candidate.learning_score}
                        </span>
                        <span className="text-[9px] text-zinc-600">{BAND_TR[candidate.learning_band]}</span>
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-zinc-200">
                          {CANONICAL_TYPE_TR[candidate.event_type as CanonicalEventType] ?? candidate.event_type}
                        </span>
                        <span className="block truncate font-mono text-[9px] text-zinc-600">
                          {candidate.event_id}
                        </span>
                        <span className="mt-0.5 block text-[10px] text-amber-300/80">
                          {candidate.blockers.join(" · ") || "Seçilen rotalar hazır"}
                        </span>
                      </span>
                      <span className="text-right text-[10px] text-zinc-500">
                        <span className="block">{candidate.ready_uses.length}/{candidate.recommended_uses.length} rota</span>
                        <span className="block">Müdahale {candidate.intervention_score ?? "—"}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </section>

              <div className="rounded-md border border-amber-900 bg-amber-950/30 p-2.5 text-xs text-amber-200">
                Otomatik yürütme, otomatik eğitim ve canlı modele otomatik terfi kapalıdır.
                Hazır rota yalnız ilgili insan onaylı hazırlık kuyruğuna giriş iznidir.
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
