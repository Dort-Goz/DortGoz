import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  approveEventsInBatch,
  exportCandidateOnnx,
  getIncidentMedia,
  getLearningPipeline,
  planTrainingJob,
  promoteModel,
  runTrainingJob,
} from "../lib/api";
import { CANONICAL_TYPE_TR } from "../lib/labels";
import { presentationForUse } from "../lib/learningPresentation";
import {
  STAGE_ORDER,
  STAGE_TR,
  firstActionableStage,
  formatElapsed,
  jobStatusClass,
  jobStatusLabel,
  measurementSteps,
  ratio,
  readinessSummary,
} from "../lib/pipelinePresentation";
import type {
  DfineArchitecture,
  IncidentMedia,
  LearningPipelineView,
  PipelineEventItem,
  PipelineModelItem,
  PipelineStage,
  TrainingJob,
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

/** Steps the API cannot own: they need an external, immutable test set. */
function measurementCommand(modelVersionId: string): string {
  return [
    "python scripts/dfine_feedback_training.py prepare-evaluation",
    `  --event-store runs/dortgoz.sqlite3 --model-version-id ${modelVersionId}`,
    "  --test-dataset-manifest <test-manifest.json> --coco-annotations <test.json>",
  ].join("\n");
}

export default function ModelMaintenancePanel({
  onOpenEvent,
}: {
  onOpenEvent: (eventId: string) => void;
}) {
  const [view, setView] = useState<LearningPipelineView | null>(null);
  const [media, setMedia] = useState<Record<string, IncidentMedia | null>>({});
  const [stage, setStage] = useState<PipelineStage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [engineer, setEngineer] = useState("");
  const [note, setNote] = useState("");
  const [promotionReason, setPromotionReason] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [architecture, setArchitecture] = useState<DfineArchitecture>("dfine_n");
  const [epochs, setEpochs] = useState(10);
  const [batchSize, setBatchSize] = useState(2);
  const [gpuMinutes, setGpuMinutes] = useState(60);

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
  }, [load]);

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
  const signed = engineer.trim();

  return (
    <div className="flex h-full min-h-0 flex-col gap-1.5 text-xs">
      <div className="panel shrink-0">
        <div className="panel-title">
          <span>◇ Dedektör Bakım Hattı</span>
          <span className="microlabel normal-case tracking-normal">
            bakım mühendisi ekranı
          </span>
          <span className="flex-1" />
          <span
            title="Canlı çalışan dedektör sürümü"
            className={`chip border normal-case tracking-normal ${
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

        <div className="space-y-2 p-2">
          <div className="flex flex-wrap items-end gap-2">
            <label
              className="space-y-0.5"
              title="Onay, eğitim ve terfi kayıtlarına bu ad yazılır"
            >
              <span className="microlabel block">mühendis</span>
              <input
                value={engineer}
                onChange={(e) => setEngineer(e.target.value)}
                placeholder="ad soyad"
                className="field w-44"
              />
            </label>
            <button
              type="button"
              disabled={busy !== ""}
              onClick={() => void act("yenile", async () => {})}
              className="btn btn-outline"
            >
              {busy === "yenile" ? "Yenileniyor…" : "Yenile"}
            </button>
          </div>

          <nav aria-label="Bakım aşamaları" className="grid grid-cols-3 gap-2 lg:grid-cols-6">
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
                  className={`border px-3 py-2 text-left transition-colors ${
                    isActive
                      ? "border-sky-700 bg-zinc-800"
                      : blocked > 0
                        ? "border-amber-900/70 bg-amber-950/20 hover:border-amber-700"
                        : "border-zinc-800 bg-zinc-900 hover:border-zinc-600"
                  }`}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className={`microlabel ${isActive ? "text-zinc-300" : ""}`}>
                      {STAGE_TR[name]}
                    </span>
                    <span className="font-mono text-lg font-semibold leading-6 text-zinc-100">
                      {count}
                    </span>
                  </div>
                  <div className="h-3.5 text-[10px] leading-3.5 text-amber-300">
                    {blocked > 0 ? `${blocked} engelli` : ""}
                  </div>
                </button>
              );
            })}
          </nav>
        </div>
      </div>

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
                    title={signed ? undefined : "Önce üst satıra mühendis adını yazın"}
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

          {active === "queue" && (
            <>
              <section className="bg-zinc-900 p-3">
                <h3 className="font-semibold text-zinc-100">Eğitim paketi oluştur</h3>
                <p className="mt-1 text-zinc-400">
                  İzinli ve insan doğrulamalı kareler COCO'ya aktarılır, iş kuyruğa alınır.
                  Eğitim kendiliğinden başlamaz.
                </p>
                <div className="mt-3 flex flex-wrap items-end gap-2">
                  <label className="space-y-0.5">
                    <span className="microlabel block">mimari</span>
                    <select
                      value={architecture}
                      onChange={(e) => setArchitecture(e.target.value as DfineArchitecture)}
                      className="field w-28"
                    >
                      <option value="dfine_n">dfine_n</option>
                      <option value="dfine_s">dfine_s</option>
                    </select>
                  </label>
                  <NumberField label="epok" value={epochs} onChange={setEpochs} />
                  <NumberField label="yığın" value={batchSize} onChange={setBatchSize} />
                  <NumberField label="gpu dk" value={gpuMinutes} onChange={setGpuMinutes} />
                  <button
                    type="button"
                    disabled={!view.readiness.can_plan || !signed || busy !== ""}
                    title={
                      view.readiness.can_plan
                        ? (signed ? undefined : "Önce üst satıra mühendis adını yazın")
                        : view.readiness.blockers.join("; ")
                    }
                    onClick={() => void act("plan", () => planTrainingJob({
                      architecture,
                      requested_by: signed,
                      epochs,
                      batch_size: batchSize,
                      max_gpu_minutes: gpuMinutes,
                    }))}
                    className="btn btn-accent"
                  >
                    {busy === "plan" ? "Oluşturuluyor…" : "Paket oluştur"}
                  </button>
                </div>
                {!view.readiness.can_plan && (
                  <ul className="mt-2 space-y-0.5 text-amber-200">
                    {view.readiness.blockers.map((blocker) => (
                      <li key={blocker}>• {blocker}</li>
                    ))}
                  </ul>
                )}
              </section>

              {view.queue.filter((group) => group.count > 0).length === 0 ? (
                <Empty>Paketlenmeye hazır izinli örnek yok.</Empty>
              ) : (
                view.queue.filter((group) => group.count > 0).map((group) => (
                  <section key={group.use} className="bg-zinc-900 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <h3 className="font-semibold text-zinc-100">
                        {presentationForUse(group.use).technicalComponent}
                      </h3>
                      <span className="text-[10px] text-zinc-600">{group.count} olay</span>
                    </div>
                    <p className="mt-0.5 text-zinc-400">Hedef havuz: {group.downstream}</p>
                    <p className="mt-0.5 text-[10px] text-zinc-500">
                      Güvenlik kapısı: {group.safety_gate}
                    </p>
                    <div className="mt-2 space-y-1">
                      {group.items.map((item) => (
                        <button
                          key={`${group.use}-${item.event_id}`}
                          type="button"
                          onClick={() => onOpenEvent(item.event_id)}
                          className="flex w-full items-center gap-3 bg-zinc-950 px-3 py-2 text-left hover:bg-zinc-800"
                        >
                          <span className="font-mono text-zinc-500">
                            {shortId(item.event_id)}
                          </span>
                          <span className="ml-auto font-mono text-zinc-400">
                            skor {item.learning_score}
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>
                ))
              )}
            </>
          )}

          {active === "training" && (
            view.jobs.length === 0 ? (
              <Empty>Kayıtlı eğitim işi yok.</Empty>
            ) : (
              view.jobs.map((job: TrainingJob) => (
                <article key={job.job_id} className="bg-zinc-900 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-zinc-300">{shortId(job.job_id)}</span>
                    <span className={`chip border ${jobStatusClass(job.status)}`}>
                      {jobStatusLabel(job.status)}
                    </span>
                    <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
                      {job.architecture}
                    </span>
                    <span className="ml-auto font-mono text-zinc-500">
                      {formatElapsed(job.elapsed_seconds)}
                    </span>
                    {job.status === "queued" && (
                      <button
                        type="button"
                        disabled={!view.readiness.can_run || busy !== ""}
                        title={
                          view.readiness.can_run
                            ? undefined
                            : readinessSummary(view.readiness)
                        }
                        onClick={() => void act(job.job_id, () => runTrainingJob(job.job_id))}
                        className="btn btn-accent"
                      >
                        {busy === job.job_id ? "Başlatılıyor…" : "Eğitimi başlat"}
                      </button>
                    )}
                  </div>
                  <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-zinc-400 sm:grid-cols-4">
                    <Detail label="kare" value={`${job.verified_frame_count}`} />
                    <Detail
                      label="eğitim/doğrulama"
                      value={`${job.train_frame_count}/${job.validation_frame_count}`}
                    />
                    <Detail label="kaynak video" value={`${job.source_video_count}`} />
                    <Detail label="kutu" value={`${job.box_count}`} />
                    <Detail label="epok" value={`${job.epochs}`} />
                    <Detail label="yığın" value={`${job.batch_size}`} />
                    <Detail label="isteyen" value={job.requested_by} />
                    <Detail label="sınıf" value={job.category_names.join(", ")} />
                  </dl>
                  {job.error_message && (
                    <p className="mt-2 bg-red-950/40 px-2 py-1 text-red-200">
                      {job.error_code}: {job.error_message}
                    </p>
                  )}
                </article>
              ))
            )
          )}

          {active === "measurement" && (() => {
            const measuring = view.candidates.filter((item) => !item.measured);
            if (measuring.length === 0) {
              return <Empty>Ölçüm bekleyen aday model yok.</Empty>;
            }
            return measuring.map((item: PipelineModelItem) => (
              <article key={item.version.model_version_id} className="bg-zinc-900 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-zinc-300">
                    {shortId(item.version.model_version_id)}
                  </span>
                  <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
                    {item.version.architecture}
                  </span>
                  {!item.onnx_exported && (
                    <button
                      type="button"
                      disabled={busy !== ""}
                      onClick={() => void act(
                        item.version.model_version_id,
                        () => exportCandidateOnnx(item.version.model_version_id),
                      )}
                      className="btn btn-accent ml-auto"
                    >
                      {busy === item.version.model_version_id
                        ? "Aktarılıyor…"
                        : "ONNX'e aktar"}
                    </button>
                  )}
                </div>
                <ol className="mt-3 flex flex-wrap gap-3">
                  {measurementSteps(item).map((step) => (
                    <li
                      key={step.label}
                      className={step.done ? "text-emerald-300" : "text-zinc-500"}
                    >
                      {step.done ? "✓" : "○"} {step.label}
                    </li>
                  ))}
                </ol>
                {item.onnx_exported && (
                  <>
                    <p className="mt-3 text-zinc-400">
                      Kalan adımlar değişmez bir test kümesi ister; konsoldan çalıştırın:
                    </p>
                    <pre className="mt-1 overflow-x-auto bg-zinc-950 p-2 font-mono text-[10px] text-zinc-400">
                      {measurementCommand(item.version.model_version_id)}
                    </pre>
                  </>
                )}
              </article>
            ));
          })()}

          {active === "promotion" && (
            <>
              {champion && (
                <section className="border border-emerald-900 bg-emerald-950/20 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold text-emerald-100">Yürürlükteki model</h3>
                    <span className="font-mono text-zinc-300">
                      {shortId(champion.version.model_version_id)}
                    </span>
                    <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
                      {champion.version.architecture}
                    </span>
                    <span className="ml-auto text-[10px] text-zinc-500">
                      terfi eden: {champion.version.approved_by ?? "—"}
                    </span>
                  </div>
                  <p className="mt-1 text-zinc-400">Aday bu değerleri geçmek zorundadır.</p>
                  <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-zinc-400 sm:grid-cols-4">
                    <Detail
                      label="mAP50-95"
                      value={ratio(champion.version.evaluation?.map_50_95)}
                    />
                    <Detail
                      label="kritik recall"
                      value={ratio(champion.version.evaluation?.critical_recall)}
                    />
                    <Detail
                      label="yanlış alarm/saat"
                      value={`${champion.version.evaluation?.false_alarms_per_hour ?? "—"}`}
                    />
                    <Detail
                      label="p95 gecikme"
                      value={`${champion.version.evaluation?.p95_latency_ms ?? "—"} ms`}
                    />
                  </dl>
                </section>
              )}

              {view.candidates.filter((item) => item.measured).length === 0 ? (
                <Empty>Ölçümü tamamlanmış aday yok.</Empty>
              ) : (
                <>
                  <label className="block space-y-0.5 bg-zinc-900 p-2">
                    <span className="microlabel block">terfi gerekçesi</span>
                    <input
                      value={promotionReason}
                      onChange={(e) => setPromotionReason(e.target.value)}
                      placeholder="ölçüm kapısı geçildi"
                      className="field w-full"
                    />
                  </label>
                  {view.candidates.filter((item) => item.measured).map((item) => (
                    <article key={item.version.model_version_id} className="bg-zinc-900 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-zinc-300">
                          {shortId(item.version.model_version_id)}
                        </span>
                        <span
                          className={`chip border ${
                            item.gate_passed
                              ? "border-emerald-900 bg-emerald-950/30 text-emerald-200"
                              : "border-amber-900 bg-amber-950/30 text-amber-200"
                          }`}
                        >
                          {item.gate_passed ? "Kapıdan geçti" : "Kapı engelli"}
                        </span>
                        <button
                          type="button"
                          disabled={!item.gate_passed || !signed || busy !== ""}
                          title={
                            item.gate_passed
                              ? (signed ? undefined : "Önce üst satıra mühendis adını yazın")
                              : item.gate_failures.join("; ")
                          }
                          onClick={() => void act(
                            item.version.model_version_id,
                            () => promoteModel(item.version.model_version_id, {
                              approved_by: signed,
                              reason: promotionReason.trim() || "ölçüm kapısı geçildi",
                            }),
                          )}
                          className="btn btn-accent ml-auto"
                        >
                          {busy === item.version.model_version_id
                            ? "Terfi ediliyor…"
                            : "Terfi ettir"}
                        </button>
                      </div>
                      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-zinc-400 sm:grid-cols-4">
                        <Detail
                          label="mAP50-95"
                          value={ratio(item.version.evaluation?.map_50_95)}
                        />
                        <Detail
                          label="kritik recall"
                          value={ratio(item.version.evaluation?.critical_recall)}
                        />
                        <Detail
                          label="yanlış alarm/saat"
                          value={`${item.version.evaluation?.false_alarms_per_hour ?? "—"}`}
                        />
                        <Detail
                          label="p95 gecikme"
                          value={`${item.version.evaluation?.p95_latency_ms ?? "—"} ms`}
                        />
                      </dl>
                      {item.gate_failures.length > 0 && (
                        <ul className="mt-2 space-y-0.5 text-amber-200">
                          {item.gate_failures.map((failure) => (
                            <li key={failure}>• {failure}</li>
                          ))}
                        </ul>
                      )}
                    </article>
                  ))}
                </>
              )}
            </>
          )}
        </div>
      </div>

      <footer className="shrink-0 text-[10px] leading-relaxed text-zinc-500">
        {readinessSummary(view.readiness)} · eğitim politikası{" "}
        {view.readiness.training_policy_version ?? "—"} · terfi politikası{" "}
        {view.readiness.promotion_policy_version ?? "—"} · otomatik eğitim ve otomatik
        terfi KAPALIDIR; her adım insan onayıyla ilerler.
      </footer>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="bg-zinc-900/60 px-4 py-8 text-center text-zinc-400">{children}</p>
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

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="microlabel">{label}</dt>
      <dd className="font-mono text-zinc-300">{value}</dd>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="space-y-0.5">
      <span className="microlabel block">{label}</span>
      <input
        type="number"
        min={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value) || 1)}
        className="field w-20"
      />
    </label>
  );
}
