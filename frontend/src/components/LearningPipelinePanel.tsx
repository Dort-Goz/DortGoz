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

/** Steps the API cannot own: they need an external, immutable test set. */
function measurementCommand(modelVersionId: string): string {
  return [
    "python scripts/dfine_feedback_training.py prepare-evaluation",
    `  --event-store runs/dortgoz.sqlite3 --model-version-id ${modelVersionId}`,
    "  --test-dataset-manifest <test-manifest.json> --coco-annotations <test.json>",
  ].join("\n");
}

export default function LearningPipelinePanel({
  onOpenEvent,
}: {
  onOpenEvent: (eventId: string) => void;
}) {
  const [view, setView] = useState<LearningPipelineView | null>(null);
  const [media, setMedia] = useState<Record<string, IncidentMedia | null>>({});
  const [stage, setStage] = useState<PipelineStage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [operator, setOperator] = useState("");
  const [architecture, setArchitecture] = useState<DfineArchitecture>("dfine_n");
  const [epochs, setEpochs] = useState(10);
  const [batchSize, setBatchSize] = useState(2);
  const [gpuMinutes, setGpuMinutes] = useState(60);

  const load = useCallback(async () => {
    try {
      setView(await getLearningPipeline());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Hat durumu alınamadı.");
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
        {error || "Hat durumu okunuyor…"}
      </div>
    );
  }

  const dfineQueue = view.queue.find((group) => group.use === "d_fine_training");

  return (
    <div className="flex h-full min-h-0 flex-col text-xs">
      <nav
        aria-label="Öğrenme hattı"
        className="flex shrink-0 items-stretch gap-px overflow-x-auto border-b border-zinc-800 bg-zinc-900"
      >
        {STAGE_ORDER.map((name, index) => {
          const summary = view.stages.find((item) => item.stage === name);
          const isActive = active === name;
          return (
            <button
              key={name}
              type="button"
              onClick={() => setStage(name)}
              title={summary?.detail}
              className={`flex min-w-28 flex-1 flex-col gap-0.5 px-3 py-2 text-left transition-colors ${
                isActive ? "bg-zinc-800" : "bg-zinc-950 hover:bg-zinc-900"
              }`}
            >
              <span className="flex items-center gap-1.5">
                <span
                  className={`text-[10px] uppercase tracking-wide ${
                    isActive ? "text-zinc-200" : "text-zinc-500"
                  }`}
                >
                  {index + 1}. {STAGE_TR[name]}
                </span>
                {(summary?.blocked_count ?? 0) > 0 && (
                  <span className="inline-flex min-w-4 items-center justify-center bg-amber-800 px-1 font-mono text-[10px] leading-4 text-amber-100">
                    {summary?.blocked_count}
                  </span>
                )}
              </span>
              <span
                className={`font-mono text-lg font-semibold ${
                  isActive ? "text-zinc-100" : "text-zinc-400"
                }`}
              >
                {summary?.count ?? 0}
              </span>
            </button>
          );
        })}
      </nav>

      {error && (
        <div className="shrink-0 border-b border-red-900 bg-red-950/40 px-3 py-2 text-red-200">
          {error}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {active === "review" && (
          <StageBody
            title="İnsan kararı bekleyen olaylar"
            empty="İnceleme bekleyen olay yok."
            items={view.review_items}
          >
            {(item) => (
              <EventRow
                key={item.event_id}
                item={item}
                media={media[item.event_id]}
                onOpen={onOpenEvent}
              />
            )}
          </StageBody>
        )}

        {active === "approval" && (
          <>
            <div className="mb-3 flex flex-wrap items-end gap-2 bg-zinc-900 p-3">
              <label className="flex flex-col gap-1">
                <span className="microlabel">onaylayan</span>
                <input
                  value={operator}
                  onChange={(e) => setOperator(e.target.value)}
                  placeholder="ad soyad"
                  className="field w-40"
                />
              </label>
              <label className="flex min-w-48 flex-1 flex-col gap-1">
                <span className="microlabel">gerekçe</span>
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
                  selected.length === 0
                  || !operator.trim()
                  || !note.trim()
                  || busy !== ""
                }
                onClick={() => void act("batch", async () => {
                  const uses = new Set(
                    view.approval_items
                      .filter((item) => selected.includes(item.event_id))
                      .flatMap((item) => item.recommended_uses),
                  );
                  const result = await approveEventsInBatch({
                    event_ids: selected,
                    approved_uses: [...uses],
                    reviewer: operator.trim(),
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
            <StageBody
              title="Geliştirme izni bekleyen olaylar"
              empty="Onay bekleyen olay yok."
              items={view.approval_items}
            >
              {(item) => (
                <EventRow
                  key={item.event_id}
                  item={item}
                  media={media[item.event_id]}
                  onOpen={onOpenEvent}
                  checked={selected.includes(item.event_id)}
                  onToggle={() => toggle(item.event_id)}
                />
              )}
            </StageBody>
          </>
        )}

        {active === "queue" && (
          <div className="space-y-4">
            <section className="bg-zinc-900 p-3">
              <h3 className="font-semibold text-zinc-100">Eğitim paketi oluştur</h3>
              <p className="mt-1 text-zinc-400">
                İzinli ve doğrulanmış kareler COCO'ya aktarılır, iş kuyruğa alınır.
                Eğitim kendiliğinden başlamaz.
              </p>
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1">
                  <span className="microlabel">mimari</span>
                  <select
                    value={architecture}
                    onChange={(e) =>
                      setArchitecture(e.target.value as DfineArchitecture)}
                    className="field w-28"
                  >
                    <option value="dfine_n">dfine_n</option>
                    <option value="dfine_s">dfine_s</option>
                  </select>
                </label>
                <NumberField label="epok" value={epochs} onChange={setEpochs} />
                <NumberField label="yığın" value={batchSize} onChange={setBatchSize} />
                <NumberField
                  label="gpu dk"
                  value={gpuMinutes}
                  onChange={setGpuMinutes}
                />
                <label className="flex flex-col gap-1">
                  <span className="microlabel">isteyen</span>
                  <input
                    value={operator}
                    onChange={(e) => setOperator(e.target.value)}
                    placeholder="ad soyad"
                    className="field w-36"
                  />
                </label>
                <button
                  type="button"
                  disabled={
                    !view.readiness.can_plan || !operator.trim() || busy !== ""
                  }
                  title={
                    view.readiness.can_plan
                      ? undefined
                      : view.readiness.blockers.join("; ")
                  }
                  onClick={() => void act("plan", () => planTrainingJob({
                    architecture,
                    requested_by: operator.trim(),
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
              {dfineQueue && (
                <p className="mt-2 text-zinc-500">
                  D-FINE kuyruğunda {dfineQueue.count} olay hazır · güvenlik kapısı:{" "}
                  {dfineQueue.safety_gate}
                </p>
              )}
            </section>

            {view.queue.filter((group) => group.count > 0).map((group) => (
              <section key={group.use}>
                <div className="mb-2">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="font-semibold text-zinc-100">
                      {presentationForUse(group.use).title}
                    </h3>
                    <span className="text-[10px] text-zinc-600">
                      {group.count} olay
                    </span>
                  </div>
                  <p className="mt-0.5 text-zinc-400">
                    {presentationForUse(group.use).description}
                  </p>
                  <p className="mt-1 text-sky-200">
                    <span className="font-medium">Önerilen işlem:</span>{" "}
                    {presentationForUse(group.use).action}
                  </p>
                  <p className="mt-1 text-[10px] text-zinc-500">
                    {presentationForUse(group.use).technicalComponent} ·{" "}
                    {group.downstream} · güvenlik kapısı: {group.safety_gate}
                  </p>
                </div>
                <div className="space-y-1">
                  {group.items.map((item) => (
                    <button
                      key={`${group.use}-${item.event_id}`}
                      type="button"
                      onClick={() => onOpenEvent(item.event_id)}
                      className="flex w-full items-center gap-3 bg-zinc-900 px-3 py-2 text-left hover:bg-zinc-800"
                    >
                      <span className="font-mono text-zinc-500">
                        {shortId(item.event_id)}
                      </span>
                      <span className="text-zinc-300">{item.downstream}</span>
                      <span className="ml-auto font-mono text-zinc-400">
                        skor {item.learning_score}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}

        {active === "training" && (
          <StageBody
            title="D-FINE eğitim işleri"
            empty="Kayıtlı eğitim işi yok."
            items={view.jobs}
          >
            {(job: TrainingJob) => (
              <article key={job.job_id} className="bg-zinc-900 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-zinc-300">
                    {shortId(job.job_id)}
                  </span>
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
            )}
          </StageBody>
        )}

        {active === "measurement" && (
          <StageBody
            title="Ölçüm bekleyen adaylar"
            empty="Ölçüm bekleyen aday model yok."
            items={view.candidates.filter((item) => !item.measured)}
          >
            {(item: PipelineModelItem) => (
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
            )}
          </StageBody>
        )}

        {active === "promotion" && (
          <>
            {view.champion && (
              <section className="mb-3 border border-emerald-900 bg-emerald-950/20 p-3">
                <h3 className="font-semibold text-emerald-100">Yürürlükteki model</h3>
                <div className="mt-1 flex flex-wrap gap-3 text-zinc-300">
                  <span className="font-mono">
                    {shortId(view.champion.version.model_version_id)}
                  </span>
                  <span>{view.champion.version.architecture}</span>
                  <span>
                    mAP50-95 {ratio(view.champion.version.evaluation?.map_50_95)}
                  </span>
                  <span>
                    kritik recall{" "}
                    {ratio(view.champion.version.evaluation?.critical_recall)}
                  </span>
                </div>
              </section>
            )}
            <StageBody
              title="Terfi kapısındaki adaylar"
              empty="Ölçümü tamamlanmış aday yok."
              items={view.candidates.filter((item) => item.measured)}
            >
              {(item: PipelineModelItem) => (
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
                      disabled={!item.gate_passed || !operator.trim() || busy !== ""}
                      title={
                        item.gate_passed
                          ? undefined
                          : item.gate_failures.join("; ")
                      }
                      onClick={() => void act(
                        item.version.model_version_id,
                        () => promoteModel(item.version.model_version_id, {
                          approved_by: operator.trim(),
                          reason: note.trim() || "terfi kapısı geçildi",
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
              )}
            </StageBody>
          </>
        )}
      </div>

      <footer className="shrink-0 border-t border-zinc-800 bg-zinc-900 px-3 py-2 text-[10px] leading-relaxed text-zinc-500">
        {readinessSummary(view.readiness)} · eğitim politikası{" "}
        {view.readiness.training_policy_version ?? "—"} · terfi politikası{" "}
        {view.readiness.promotion_policy_version ?? "—"} · otomatik eğitim ve otomatik
        terfi KAPALIDIR; her adım insan onayıyla ilerler.
      </footer>
    </div>
  );
}

function StageBody<T>({
  title,
  empty,
  items,
  children,
}: {
  title: string;
  empty: string;
  items: T[];
  children: (item: T) => ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="font-semibold text-zinc-100">{title}</h3>
        {items.length > 0 && (
          <span className="text-[10px] text-zinc-600">{items.length} kayıt</span>
        )}
      </div>
      {items.length === 0 ? (
        <p className="bg-zinc-900/60 px-4 py-8 text-center text-zinc-400">{empty}</p>
      ) : (
        <div className="space-y-2">{items.map(children)}</div>
      )}
    </section>
  );
}

function EventRow({
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
  return (
    <article className="flex items-center gap-3 bg-zinc-900 px-3 py-2">
      {onToggle && (
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          aria-label={`${item.event_id} seç`}
          className="h-4 w-4 shrink-0 accent-sky-600"
        />
      )}
      {media && (
        <img
          src={media.thumbnail_url}
          alt={`${eventLabel(item.event_type)} kanıt görüntüsü`}
          loading="lazy"
          className="h-14 w-24 shrink-0 bg-zinc-950 object-cover"
        />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-zinc-100">{eventLabel(item.event_type)}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
            {BAND_TR[item.learning_band]} · {item.learning_score}
          </span>
          {media && (
            <span className="chip border border-zinc-700 bg-zinc-950 font-mono text-zinc-400">
              Kanıt {shortClock(media.clip_start)}–{shortClock(media.clip_end)}
            </span>
          )}
          <span className="chip border border-zinc-700 bg-zinc-950 font-mono text-zinc-500">
            {shortId(item.video_id)}
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
      <button
        type="button"
        onClick={() => onOpen(item.event_id)}
        className="btn btn-outline shrink-0"
      >
        İncele
      </button>
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
    <label className="flex flex-col gap-1">
      <span className="microlabel">{label}</span>
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
