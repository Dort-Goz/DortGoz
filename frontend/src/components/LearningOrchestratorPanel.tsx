import { useCallback, useEffect, useMemo, useState } from "react";
import { getIncidentMedia, getLearningOrchestratorOverview } from "../lib/api";
import { CANONICAL_TYPE_TR } from "../lib/labels";
import {
  approvalWaitingCount,
  candidateActionTitle,
  candidateStatus,
  presentationForUse,
  systemBehaviorLabel,
  visibleDevelopmentSuggestions,
} from "../lib/learningPresentation";
import type { IncidentMedia, LearningOrchestratorOverview } from "../types/domain";
import type { CanonicalEventType } from "../types/events";

const BAND_TR = {
  low: "Düşük",
  medium: "Orta",
  high: "Yüksek",
  priority: "Öncelikli",
} as const;

function shortIdentifier(value: string): string {
  return value.length > 18 ? value.slice(0, 8) : value;
}

function shortClock(seconds: number): string {
  const wholeSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(wholeSeconds / 60);
  const remainingSeconds = wholeSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

export default function LearningOrchestratorPanel({
  onClose,
  onOpenEvent,
}: {
  onClose: () => void;
  onOpenEvent: (eventId: string) => void;
}) {
  const [overview, setOverview] = useState<LearningOrchestratorOverview | null>(null);
  const [candidateMedia, setCandidateMedia] = useState<Record<string, IncidentMedia | null>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextOverview = await getLearningOrchestratorOverview();
      const mediaEntries = await Promise.all(
        nextOverview.priority_candidates.map(async (candidate) => [
          candidate.event_id,
          await getIncidentMedia(candidate.event_id).catch(() => null),
        ] as const),
      );
      setOverview(nextOverview);
      setCandidateMedia(Object.fromEntries(mediaEntries));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Öğrenme Merkezi durumu alınamadı.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const suggestions = useMemo(
    () => visibleDevelopmentSuggestions(overview?.route_summaries ?? []),
    [overview],
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-2 sm:p-4">
      <section className="flex max-h-[96vh] w-full max-w-6xl flex-col overflow-hidden rounded-md border border-zinc-800 bg-zinc-950 shadow-2xl">
        <header className="flex shrink-0 items-center gap-3 border-b border-zinc-800 px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-zinc-100">Öğrenme Merkezi</h2>
            <p className="mt-0.5 text-[10px] leading-relaxed text-zinc-500 sm:text-xs">
              İncelenen olaylardan elde edilen geliştirme önerileri ve insan onaylı iyileştirme süreci
            </p>
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
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

        <div className="min-h-0 flex-1 overflow-y-auto p-3 text-xs sm:p-5">
          {error && (
            <div className="mb-4 rounded-md border border-red-900 bg-red-950/40 p-3 text-red-200">
              {error}
            </div>
          )}
          {loading && !overview && <p className="text-zinc-500">Sistem durumu okunuyor…</p>}

          {overview && (
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                {[
                  ["Toplam olay", overview.total_events],
                  ["İnceleme bekleyen", overview.pending_review_events],
                  [
                    "Onay bekleyen",
                    approvalWaitingCount(
                      overview.pending_approval_events,
                      overview.stale_approval_events,
                    ),
                  ],
                  ["Hazır işlemler", overview.ready_routes],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-md bg-zinc-900 px-3 py-3">
                    <div className="microlabel">{label}</div>
                    <div className="mt-1 font-mono text-xl font-semibold text-zinc-100">{value}</div>
                  </div>
                ))}
              </div>

              <section aria-labelledby="pending-events-title">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <h3 id="pending-events-title" className="text-sm font-semibold text-zinc-100">
                    İşlem bekleyen olaylar
                  </h3>
                  {overview.priority_candidates.length > 0 && (
                    <span className="text-[10px] text-zinc-600">
                      {overview.priority_candidates.length} kayıt
                    </span>
                  )}
                </div>

                {overview.priority_candidates.length === 0 ? (
                  <div className="rounded-md bg-zinc-900/60 px-4 py-8 text-center">
                    <p className="font-medium text-zinc-200">Şu anda işlem bekleyen olay yok.</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {overview.priority_candidates.map((candidate) => {
                      const eventLabel = CANONICAL_TYPE_TR[
                        candidate.event_type as CanonicalEventType
                      ] ?? candidate.event_type;
                      const media = candidateMedia[candidate.event_id];
                      return (
                        <article
                          key={candidate.event_id}
                          className="overflow-hidden rounded-md border border-zinc-800 bg-zinc-900 transition-colors hover:border-zinc-700"
                        >
                          <div className="grid grid-cols-[7rem_minmax(0,1fr)] sm:grid-cols-[11rem_minmax(0,1fr)_8rem] sm:items-stretch">
                            <div className="row-span-2 min-h-32 overflow-hidden bg-zinc-950 sm:row-span-1 sm:min-h-28">
                              {media ? (
                                <img
                                  src={media.thumbnail_url}
                                  alt={`${eventLabel} kanıt görüntüsü`}
                                  loading="lazy"
                                  className="h-full w-full object-cover"
                                />
                              ) : (
                                <div className="flex h-full min-h-24 items-center justify-center bg-gradient-to-br from-zinc-900 to-zinc-950 text-[10px] text-zinc-600">
                                  Görsel yok
                                </div>
                              )}
                            </div>
                            <div className="min-w-0 p-3">
                              <h4 className="text-sm font-medium text-zinc-100">
                                {candidateActionTitle(candidate, eventLabel)}
                              </h4>
                              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                                <span className="chip border border-amber-900 bg-amber-950/30 text-amber-200">
                                  {candidateStatus(candidate)}
                                </span>
                                <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
                                  Önem: {BAND_TR[candidate.learning_band]}
                                </span>
                                {media && (
                                  <span className="chip border border-zinc-700 bg-zinc-950 font-mono text-zinc-400">
                                    Kanıt {shortClock(media.clip_start)}–{shortClock(media.clip_end)}
                                  </span>
                                )}
                                <span className="chip border border-zinc-700 bg-zinc-950 font-mono text-zinc-500">
                                  Kayıt {shortIdentifier(candidate.video_id)}
                                </span>
                              </div>
                            </div>
                            <div className="col-start-2 flex items-center p-3 pt-0 sm:col-auto sm:pt-3 sm:pl-0">
                              <button
                                type="button"
                                onClick={() => onOpenEvent(candidate.event_id)}
                                className="btn btn-accent w-full"
                              >
                                İncele
                              </button>
                            </div>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                )}
              </section>

              <section aria-labelledby="development-suggestions-title">
                <h3 id="development-suggestions-title" className="text-sm font-semibold text-zinc-100">
                  Geliştirme önerileri
                </h3>
                {suggestions.length === 0 ? (
                  <div className="mt-2 rounded-md bg-zinc-900/60 px-4 py-7 text-center">
                    <p className="font-medium text-zinc-200">
                      Şu anda işlem gerektiren bir geliştirme önerisi yok.
                    </p>
                    <p className="mt-1 text-zinc-500">
                      İncelenen olaylarda anlamlı bir örüntü tespit edildiğinde burada gösterilecek.
                    </p>
                  </div>
                ) : (
                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    {suggestions.map((suggestion) => {
                      const presentation = presentationForUse(suggestion.use);
                      return (
                        <article key={suggestion.use} className="rounded-md bg-zinc-900 px-3 py-3">
                          <div className="flex items-center justify-between gap-3">
                            <span className="microlabel">{presentation.category}</span>
                            <span className="text-[10px] text-zinc-500">
                              {suggestion.recommended_count} olay
                            </span>
                          </div>
                          <h4 className="mt-1.5 font-medium text-zinc-100">{presentation.title}</h4>
                          <p className="mt-1 leading-relaxed text-zinc-400">
                            {presentation.description}
                          </p>
                          <p className="mt-2 text-sky-200">
                            <span className="font-medium">Önerilen işlem:</span>{" "}
                            {presentation.action}
                          </p>
                          <p className="mt-2 text-[10px] text-zinc-500">
                            {suggestion.ready_count > 0 && `${suggestion.ready_count} hazır`}
                            {suggestion.ready_count > 0 && suggestion.awaiting_gate_count > 0 && " · "}
                            {suggestion.awaiting_gate_count > 0
                              && `${suggestion.awaiting_gate_count} insan onayı bekliyor`}
                          </p>
                        </article>
                      );
                    })}
                  </div>
                )}
              </section>

              {(overview.drift.state === "watch" || overview.drift.state === "drift") && (
                <section className="flex items-center gap-3 rounded-md border border-amber-900/60 bg-amber-950/20 px-3 py-2.5">
                  <span className="h-2 w-2 shrink-0 rounded-full bg-amber-400" />
                  <div>
                    <h3 className="font-medium text-amber-100">Sistem durumu</h3>
                    <p className="mt-0.5 text-zinc-400">
                      {systemBehaviorLabel(overview.drift.state)}. İnsan incelemeleri sürüyor.
                    </p>
                  </div>
                </section>
              )}

              <p className="border-t border-zinc-800 pt-3 text-[10px] leading-relaxed text-zinc-500">
                Otomatik eğitim ve canlı sisteme otomatik geçiş kapalıdır. Tüm geliştirmeler insan onayıyla ilerler.
              </p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
