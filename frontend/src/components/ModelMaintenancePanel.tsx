import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  approveEventsInBatch,
  getIncidentMedia,
  getLearningPipeline,
} from "../lib/api";
import { CANONICAL_TYPE_TR } from "../lib/labels";
import { presentationForUse } from "../lib/learningPresentation";
import {
  STAGE_ORDER,
  STAGE_TR,
  firstActionableStage,
  ratio,
  readinessSummary,
} from "../lib/pipelinePresentation";
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

const BAND_TR = {
  low: "Düşük",
  medium: "Orta",
  high: "Yüksek",
  priority: "Öncelikli",
} as const;

/** Aşamanın bakım mühendisi için ne anlama geldiği; sekme başlığında durur. */
const STAGE_NOTE: Record<PipelineStage, string> = {
  review: "eğitim verisi bu kararlardan üretilir",
  approval: "adı kayda geçen ayrı geliştirme izni",
  queue: "izinli kareler COCO'ya aktarılmayı bekliyor",
  training: "münhasır iş; aynı anda tek eğitim çalışır",
  measurement: "değişmez dış test kümesi ister",
  promotion: "kapı, adayı yürürlükteki modelle karşılaştırır",
};

const POLL_MS = 5000;
/** Kanıt görüntüsü yalnız ekrandaki ilk satırlar için çekilir. */
const THUMBNAIL_LIMIT = 12;

function eventLabel(type: string): string {
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

/** Mühendisin sorusu: bu olay hangi bileşeni besler? */
function feedsLine(item: PipelineEventItem): string {
  if (item.recommended_uses.length === 0) {
    return "İnsan kararı yok; şu anda hiçbir bileşeni beslemiyor.";
  }
  const components = item.recommended_uses
    .map((use) => presentationForUse(use).technicalComponent)
    .join(", ");
  return `Besleyeceği bileşenler: ${components}`;
}

export default function ModelMaintenancePanel({
  user,
  onOpenEvent,
  refreshToken,
}: {
  /** Konsolun tek kimliği; onay, eğitim ve terfi kayıtlarını imzalar. */
  user: string;
  onOpenEvent: (eventId: string) => void;
  refreshToken: number;
}) {
  const [view, setView] = useState<LearningPipelineView | null>(null);
  const [media, setMedia] = useState<Record<string, IncidentMedia | null>>({});
  const [stage, setStage] = useState<PipelineStage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [selected, setSelected] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      setView(await getLearningPipeline());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Bakım hattı durumu alınamadı.");
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

  const toggle = (eventId: string) =>
    setSelected((current) =>
      current.includes(eventId)
        ? current.filter((item) => item !== eventId)
        : [...current, eventId],
    );

  if (!view) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-zinc-500">
        {error || "Bakım hattı durumu okunuyor…"}
      </div>
    );
  }

  /** Aşama sayısı gerçek birikimi verir; liste uçları sunucuda kırpılıdır. */
  const stageCount = (name: PipelineStage) =>
    view.stages.find((item) => item.stage === name)?.count ?? 0;
  const champion = view.champion;
  const signed = user.trim();
  const stageProps: StageProps = {
    view,
    signed,
    busy,
    act: (label, run) => void act(label, run),
    onOpenEvent,
    onOpenStage: setStage,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="toolbar">
        <div className="toolbar-group">
          <span className="microlabel block">aşama</span>
          <nav
            aria-label="Bakım aşamaları"
            className="flex h-7 items-center gap-0.5 rounded-sm border border-zinc-800 bg-zinc-950 p-0.5"
          >
            {STAGE_ORDER.map((name) => {
              const summary = view.stages.find((item) => item.stage === name);
              const count = summary?.count ?? 0;
              const blocked = summary?.blocked_count ?? 0;
              const isActive = active === name;
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => setStage(name)}
                  title={summary?.detail}
                  aria-current={isActive ? "page" : undefined}
                  className={`h-full px-2.5 transition-colors ${
                    isActive
                      ? "bg-zinc-800 font-medium text-zinc-100"
                      : "text-zinc-500 hover:text-zinc-200"
                  }`}
                >
                  {STAGE_TR[name]}
                  {count > 0 && (
                    <span
                      className={`ml-1.5 inline-flex min-w-4 items-center justify-center rounded-sm px-1 font-mono text-[10px] leading-4 ${
                        blocked > 0
                          ? "bg-amber-800 text-amber-100"
                          : isActive
                            ? "bg-zinc-600 text-zinc-100"
                            : "bg-zinc-800 text-zinc-300"
                      }`}
                    >
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
        <span
          title="Canlı çalışan dedektör sürümü"
          className={`chip border ${
            champion
              ? "border-emerald-900 bg-emerald-950/30 text-emerald-200"
              : "border-zinc-700 bg-zinc-950 text-zinc-400"
          }`}
        >
          {champion
            ? `yürürlükte ${champion.version.architecture} ·`
              + ` mAP ${ratio(champion.version.evaluation?.map_50_95)}`
            : "yürürlükte terfi etmiş aday yok"}
        </span>
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
          <span className="microlabel normal-case tracking-normal">
            {STAGE_NOTE[active]}
          </span>
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
              <Empty>İnceleme bekleyen olay yok.</Empty>
            ) : (
              <>
                {view.review_items.map((item) => (
                  <EventCard
                    key={item.event_id}
                    item={item}
                    media={media[item.event_id]}
                    onOpen={onOpenEvent}
                  />
                ))}
                <Truncated shown={view.review_items.length} total={stageCount("review")} />
              </>
            )
          )}

          {active === "approval" && (
            view.approval_items.length === 0 ? (
              <Empty>Onay bekleyen olay yok.</Empty>
            ) : (
              <>
                <div className="flex flex-wrap items-end gap-2 bg-zinc-900 p-2">
                  <label className="min-w-48 flex-1 space-y-0.5">
                    <span className="microlabel block">gerekçe</span>
                    <input
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="toplu onay gerekçesi"
                      className="field w-full"
                    />
                  </label>
                  <button
                    type="button"
                    disabled={
                      selected.length === 0 || !signed || !note.trim() || busy !== ""
                    }
                    title={signed ? undefined : "Önce üst çubuktaki kullanıcı adını doldurun"}
                    onClick={() => void act("batch", async () => {
                      const uses = new Set(
                        view.approval_items
                          .filter((item) => selected.includes(item.event_id))
                          .flatMap((item) => item.recommended_uses),
                      );
                      const result = await approveEventsInBatch({
                        event_ids: selected,
                        approved_uses: [...uses],
                        reviewer: signed,
                        note: note.trim(),
                      });
                      setSelected([]);
                      if (result.failures.length > 0) {
                        setError(
                          `${result.failures.length} olay onaylanamadı: `
                          + result.failures.map((f) => f.reason).join("; "),
                        );
                      }
                    })}
                    className="btn btn-accent"
                  >
                    {busy === "batch"
                      ? "Onaylanıyor…"
                      : `Seçilenleri onayla (${selected.length})`}
                  </button>
                </div>
                {view.approval_items.map((item) => (
                  <EventCard
                    key={item.event_id}
                    item={item}
                    media={media[item.event_id]}
                    onOpen={onOpenEvent}
                    checked={selected.includes(item.event_id)}
                    onToggle={() => toggle(item.event_id)}
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

      <footer className="shrink-0 text-[10px] leading-relaxed text-zinc-500">
        {readinessSummary(view.readiness)} · eğitim politikası{" "}
        {view.readiness.training_policy_version ?? "—"} · terfi politikası{" "}
        {view.readiness.promotion_policy_version ?? "—"} · otomatik eğitim ve otomatik
        terfi KAPALIDIR; her adım insan onayıyla ilerler.
      </footer>
      </div>
    </div>
  );
}

/** Sunucu liste uçlarını kırpar; sayaçla listenin farkını açıkça yazarız. */
function Truncated({ shown, total }: { shown: number; total: number }) {
  if (total <= shown) return null;
  return (
    <p className="px-1 text-[10px] text-zinc-600">
      Sunucu ilk {shown} kaydı gönderdi; bu aşamada toplam {total} olay bekliyor.
    </p>
  );
}

function EventCard({
  item,
  media,
  onOpen,
  checked,
  onToggle,
}: {
  item: PipelineEventItem;
  media?: IncidentMedia | null;
  onOpen: (eventId: string) => void;
  checked?: boolean;
  onToggle?: () => void;
}) {
  const label = eventLabel(item.event_type);
  return (
    <article className="overflow-hidden border border-zinc-800 bg-zinc-900 transition-colors hover:border-zinc-700">
      <div className="grid grid-cols-[7rem_minmax(0,1fr)] sm:grid-cols-[11rem_minmax(0,1fr)_8rem] sm:items-stretch">
        <div className="row-span-2 min-h-32 overflow-hidden bg-zinc-950 sm:row-span-1 sm:min-h-28">
          {media ? (
            <img
              src={media.thumbnail_url}
              alt={`${label} kanıt görüntüsü`}
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
          <div className="flex items-start gap-2">
            {onToggle && (
              <input
                type="checkbox"
                checked={checked}
                onChange={onToggle}
                aria-label={`${label} kaydını toplu onaya seç`}
                className="mt-1 h-4 w-4 shrink-0 accent-sky-600"
              />
            )}
            <h4 className="text-sm font-medium text-zinc-100">{label}</h4>
          </div>
          <p className="mt-1 text-zinc-400">{feedsLine(item)}</p>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
              Öğrenme {BAND_TR[item.learning_band]} · {item.learning_score}
            </span>
            {media && (
              <span className="chip border border-zinc-700 bg-zinc-950 font-mono text-zinc-400">
                Kanıt {shortClock(media.clip_start)}–{shortClock(media.clip_end)}
              </span>
            )}
            <span className="chip border border-zinc-700 bg-zinc-950 font-mono text-zinc-500">
              Kayıt {shortId(item.video_id)}
            </span>
            {item.blockers.map((blocker) => (
              <span
                key={blocker}
                className="chip border border-amber-900 bg-amber-950/30 text-amber-200"
              >
                {blocker}
              </span>
            ))}
          </div>
        </div>
        <div className="col-start-2 flex items-center p-3 pt-0 sm:col-auto sm:pt-3 sm:pl-0">
          <button
            type="button"
            onClick={() => onOpen(item.event_id)}
            className="btn btn-accent w-full"
          >
            İncele
          </button>
        </div>
      </div>
    </article>
  );
}
