import { useCallback, useEffect, useMemo, useState } from "react";
import { getIncidentMedia, getLearningPipeline } from "../lib/api";
import { CANONICAL_TYPE_TR } from "../lib/labels";
import { STAGE_ORDER, firstActionableStage, ratio } from "../lib/pipelinePresentation";
import {
  Empty,
  MeasurementStage,
  PromotionStage,
  QueueStage,
  TrainingStage,
  type StageProps,
} from "./MaintenanceStages";
import type {
  IncidentMedia,
  LearningPipelineView,
  PipelineEventItem,
  PipelineStage,
} from "../types/domain";
import type { CanonicalEventType } from "../types/events";

export const MAINTENANCE_STAGE_ORDER: PipelineStage[] = [...STAGE_ORDER];

const STAGE_TR: Record<PipelineStage, string> = {
  review: "IT incelemesi",
  approval: "Fine-tune kararı",
  queue: "Eğitim kuyruğu",
  training: "Eğitim",
  measurement: "Ölçüm",
  promotion: "Terfi",
};

const DECISION_TR: Record<string, string> = {
  confirm: "Anomali doğru",
  edit: "Anomali düzeltildi",
  reject: "Anomali yok",
};

const POLL_MS = 5000;
const THUMBNAIL_LIMIT = 12;

function eventLabel(type: string | null): string {
  if (!type) return "—";
  return CANONICAL_TYPE_TR[type as CanonicalEventType] ?? type;
}

function shortId(value: string): string {
  return value.length > 20 ? `${value.slice(0, 12)}…` : value;
}

function shortClock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:`
    + `${String(whole % 60).padStart(2, "0")}`;
}

export default function ModelMaintenancePanel({
  user,
  onReviewEvent,
  onOpenEvent,
  refreshToken,
}: {
  user: string;
  onReviewEvent: (eventId: string) => void;
  onOpenEvent: (eventId: string) => void;
  refreshToken: number;
}) {
  const [view, setView] = useState<LearningPipelineView | null>(null);
  const [media, setMedia] = useState<Record<string, IncidentMedia | null>>({});
  const [stage, setStage] = useState<PipelineStage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      setView(await getLearningPipeline());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Bakım hattı alınamadı.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const jobRunning = useMemo(
    () => (view?.jobs ?? []).some((job) => job.status === "running"),
    [view],
  );

  useEffect(() => {
    if (!jobRunning) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [jobRunning, load]);

  const active: PipelineStage = stage
    ?? (view ? firstActionableStage(view.stages) : "review");

  const visibleEvents = useMemo(() => {
    if (!view) return [];
    if (active === "review") return view.review_items.slice(0, THUMBNAIL_LIMIT);
    if (active === "approval") return view.approval_items.slice(0, THUMBNAIL_LIMIT);
    return [];
  }, [view, active]);

  useEffect(() => {
    const missing = visibleEvents
      .map((item) => item.event_id)
      .filter((eventId) => !(eventId in media));
    if (missing.length === 0) return;
    let cancelled = false;
    void Promise.all(
      missing.map(async (eventId) =>
        [eventId, await getIncidentMedia(eventId).catch(() => null)] as const),
    ).then((entries) => {
      if (!cancelled) {
        setMedia((current) => ({ ...current, ...Object.fromEntries(entries) }));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [visibleEvents, media]);

  const act = async (label: string, run: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    try {
      await run();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "İşlem başarısız.");
    } finally {
      setBusy("");
    }
  };

  if (!view) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-zinc-500">
        {error || "Yükleniyor…"}
      </div>
    );
  }

  const stageCount = (name: PipelineStage) =>
    view.stages.find((item) => item.stage === name)?.count ?? 0;
  const stageProps: StageProps = {
    view,
    signed: user.trim(),
    busy,
    act: (label, run) => void act(label, run),
    onOpenEvent,
    onOpenStage: setStage,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="toolbar">
        <div className="toolbar-group min-w-0">
          <span className="microlabel block">aşama</span>
          <nav
            aria-label="Bakım aşamaları"
            className="flex h-7 min-w-0 items-center gap-0.5 overflow-x-auto rounded-sm border border-zinc-800 bg-zinc-950 p-0.5"
          >
            {MAINTENANCE_STAGE_ORDER.map((name) => {
              const summary = view.stages.find((item) => item.stage === name);
              const count = summary?.count ?? 0;
              const isActive = active === name;
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => setStage(name)}
                  aria-current={isActive ? "page" : undefined}
                  className={`h-full shrink-0 px-2.5 transition-colors ${
                    isActive
                      ? "bg-zinc-800 font-medium text-zinc-100"
                      : "text-zinc-500 hover:text-zinc-200"
                  }`}
                >
                  {STAGE_TR[name]}
                  {count > 0 && (
                    <span className="ml-1.5 inline-flex min-w-4 items-center justify-center rounded-sm bg-zinc-700 px-1 font-mono text-[10px] leading-4 text-zinc-100">
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        <button
          type="button"
          disabled={busy !== ""}
          onClick={() => void act("yenile", async () => {})}
          className="btn btn-outline"
        >
          {busy === "yenile" ? "Yenileniyor…" : "Yenile"}
        </button>

        <span className="flex-1" />
        {view.champion && (
          <span className="chip border border-emerald-900 bg-emerald-950/30 text-emerald-200">
            {view.champion.version.architecture} · mAP {ratio(view.champion.version.evaluation?.map_50_95)}
          </span>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1.5 p-1.5 text-xs">
        {error && (
          <div className="shrink-0 border border-red-900 bg-red-950/40 px-3 py-2 text-red-200">
            {error}
          </div>
        )}

        <div className="panel min-h-0 flex-1">
          <div className="panel-title">
            <span>{STAGE_TR[active]}</span>
            <span className="flex-1" />
            {stageCount(active) > 0 && (
              <span className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-300">
                {stageCount(active)}
              </span>
            )}
          </div>

          <div className="panel-body space-y-2 p-2">
            {active === "review" && (
              view.review_items.length === 0 ? (
                <Empty>Bekleyen kayıt yok.</Empty>
              ) : (
                <>
                  {view.review_items.map((item) => (
                    <EventCard
                      key={item.event_id}
                      item={item}
                      media={media[item.event_id]}
                      onOpen={onReviewEvent}
                      actionLabel="IT incele"
                    />
                  ))}
                  <Truncated shown={view.review_items.length} total={stageCount("review")} />
                </>
              )
            )}

            {active === "approval" && (
              view.approval_items.length === 0 ? (
                <Empty>Bekleyen kayıt yok.</Empty>
              ) : (
                <>
                  {view.approval_items.map((item) => (
                    <EventCard
                      key={item.event_id}
                      item={item}
                      media={media[item.event_id]}
                      onOpen={onOpenEvent}
                      actionLabel="Fine-tune kararı"
                    />
                  ))}
                  <Truncated shown={view.approval_items.length} total={stageCount("approval")} />
                </>
              )
            )}

            {active === "queue" && <QueueStage {...stageProps} />}
            {active === "training" && <TrainingStage {...stageProps} />}
            {active === "measurement" && <MeasurementStage {...stageProps} />}
            {active === "promotion" && <PromotionStage {...stageProps} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Truncated({ shown, total }: { shown: number; total: number }) {
  if (total <= shown) return null;
  return <p className="px-1 text-[10px] text-zinc-600">{shown}/{total}</p>;
}

function EventCard({
  item,
  media,
  onOpen,
  actionLabel,
}: {
  item: PipelineEventItem;
  media?: IncidentMedia | null;
  onOpen: (eventId: string) => void;
  actionLabel: string;
}) {
  const label = eventLabel(item.operator_event_type ?? item.event_type);
  return (
    <article className="overflow-hidden border border-zinc-800 bg-zinc-900 transition-colors hover:border-zinc-700">
      <div className="grid grid-cols-1 sm:grid-cols-[11rem_minmax(0,1fr)_minmax(9rem,11rem)] sm:items-stretch">
        <div className="min-h-32 overflow-hidden bg-zinc-950 sm:min-h-28">
          {media ? (
            <img
              src={media.thumbnail_url}
              alt="Olay görüntüsü"
              loading="lazy"
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full min-h-24 items-center justify-center text-[10px] text-zinc-600">
              Görsel yok
            </div>
          )}
        </div>

        <div className="min-w-0 p-3">
          <h4 className="text-sm font-medium text-zinc-100">{label}</h4>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-300">
              Operatör · {DECISION_TR[item.operator_decision ?? ""] ?? item.operator_decision}
            </span>
            {item.maintenance_decision && (
              <span className="chip border border-sky-900 bg-sky-950/30 text-sky-200">
                IT · {DECISION_TR[item.maintenance_decision] ?? item.maintenance_decision}
              </span>
            )}
            {media && (
              <span className="chip border border-zinc-700 bg-zinc-950 font-mono text-zinc-400">
                {shortClock(media.clip_start)}–{shortClock(media.clip_end)}
              </span>
            )}
            <span className="chip border border-zinc-700 bg-zinc-950 font-mono text-zinc-500">
              {shortId(item.video_id)}
            </span>
            {item.blockers.map((blocker) => (
              <span key={blocker} className="chip border border-amber-900 bg-amber-950/30 text-amber-200">
                {blocker}
              </span>
            ))}
          </div>
        </div>

        <div className="flex min-w-0 items-center p-3 pt-0 sm:p-3 sm:pl-0">
          <button
            type="button"
            onClick={() => onOpen(item.event_id)}
            className="btn btn-accent btn-wrap w-full min-w-0"
          >
            {actionLabel}
          </button>
        </div>
      </div>
    </article>
  );
}
