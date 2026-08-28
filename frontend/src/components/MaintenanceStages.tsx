import { useState, type ReactNode } from "react";
import {
  exportCandidateOnnx,
  planTrainingJob,
  promoteModel,
  runTrainingJob,
} from "../lib/api";
import { CANONICAL_TYPE_TR } from "../lib/labels";
import { presentationForUse } from "../lib/learningPresentation";
import {
  clampToLimit,
  formatElapsed,
  gpuBudgetUsage,
  groupTrainingJobs,
  jobStatusClass,
  jobStatusLabel,
  measurementSteps,
  modelForJob,
  orderedQueueGroups,
  promotionMetricRows,
  ratio,
  readinessSummary,
  trainingLimits,
  type TrainingFieldLimit,
} from "../lib/pipelinePresentation";
import type {
  DfineArchitecture,
  LearningPipelineView,
  PipelineModelItem,
  PipelineStage,
  TrainingJob,
} from "../types/domain";
import type { CanonicalEventType } from "../types/events";

/** Her aşama aynı kabuğu paylaşır: imza, meşgul kilidi ve tazeleme aynı yerden. */
export interface StageProps {
  view: LearningPipelineView;
  signed: string;
  busy: string;
  act: (label: string, run: () => Promise<unknown>) => void;
  onOpenEvent: (eventId: string) => void;
  onOpenStage: (stage: PipelineStage) => void;
}

/** Havuz listesinde ekrana sığan satır sayısı; kalanı sayaçla yazılır. */
const QUEUE_ITEM_LIMIT = 8;

const SIGN_HINT = "Önce üst çubuktaki kullanıcı adını doldurun";

function eventLabel(type: string): string {
  return CANONICAL_TYPE_TR[type as CanonicalEventType] ?? type;
}

function shortId(value: string): string {
  return value.length > 20 ? `${value.slice(0, 12)}…` : value;
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="bg-zinc-900/60 px-4 py-8 text-center text-zinc-400">{children}</p>
  );
}

export function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="microlabel">{label}</dt>
      <dd className="font-mono text-zinc-300">{value}</dd>
    </div>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-1.5">
      <div className="flex items-center gap-2">
        <h3 className="microlabel">{title}</h3>
        {count && <span className="font-mono text-[10px] text-zinc-600">{count}</span>}
      </div>
      {children}
    </section>
  );
}

function LimitedField({
  label,
  value,
  limit,
  onChange,
}: {
  label: string;
  value: number;
  limit: TrainingFieldLimit;
  onChange: (value: number) => void;
}) {
  return (
    <label className="space-y-0.5">
      <span className="microlabel block">
        {label} <span className="font-normal normal-case text-zinc-600">{limit.hint}</span>
      </span>
      <input
        type="number"
        min={1}
        max={limit.max}
        value={value}
        onChange={(event) => onChange(clampToLimit(Number(event.target.value), limit))}
        className="field w-24"
      />
    </label>
  );
}

/**
 * Kuyruk: izinli örneklerin hangi havuza aktığını gösterir ve eğitim paketini
 * kurar. Havuzların tamamı listelenir; boş havuz da nedeniyle görünür kalır.
 */
export function QueueStage({ view, signed, busy, act, onOpenEvent }: StageProps) {
  const limits = trainingLimits(view.training_policy);
  const allowed = view.training_policy?.allowed_architectures ?? ["dfine_n", "dfine_s"];
  const [architecture, setArchitecture] = useState<DfineArchitecture>(allowed[0]);
  const [epochs, setEpochs] = useState(() => clampToLimit(10, limits.epochs));
  const [batchSize, setBatchSize] = useState(() => clampToLimit(2, limits.batchSize));
  const [gpuMinutes, setGpuMinutes] = useState(() =>
    clampToLimit(60, limits.gpuMinutes),
  );

  const dfine = view.queue.find((group) => group.use === "d_fine_training");
  const policy = view.training_policy;

  return (
    <>
      <section className="bg-zinc-900 p-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className="space-y-0.5">
            <span className="microlabel block">mimari</span>
            <select
              value={architecture}
              onChange={(event) =>
                setArchitecture(event.target.value as DfineArchitecture)}
              className="field w-28"
            >
              {allowed.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <LimitedField
            label="epok"
            value={epochs}
            limit={limits.epochs}
            onChange={setEpochs}
          />
          <LimitedField
            label="yığın"
            value={batchSize}
            limit={limits.batchSize}
            onChange={setBatchSize}
          />
          <LimitedField
            label="gpu dk"
            value={gpuMinutes}
            limit={limits.gpuMinutes}
            onChange={setGpuMinutes}
          />
          <span className="flex-1" />
          <button
            type="button"
            disabled={!view.readiness.can_plan || !signed || busy !== ""}
            title={
              view.readiness.can_plan
                ? (signed ? undefined : SIGN_HINT)
                : view.readiness.blockers.join("; ")
            }
            onClick={() => act("plan", () => planTrainingJob({
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
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-zinc-500">
          <span className="font-mono">{policy?.policy_version ?? "politika okunamadı"}</span>
          {policy && (
            <>
              <span>en az {policy.minimum_verified_frames} doğrulanmış kare</span>
              <span>en az {policy.minimum_source_videos} kaynak video</span>
              <span>günlük {policy.maximum_gpu_minutes_per_day} dk GPU</span>
            </>
          )}
          <span className="text-zinc-600">
            havuzda {dfine?.count ?? 0} izinli olay
          </span>
        </div>
        {view.readiness.blockers.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-amber-200">
            {view.readiness.blockers.map((blocker) => (
              <li key={blocker}>• {blocker}</li>
            ))}
          </ul>
        )}
      </section>

      <Section title="havuzlar">
        <div className="space-y-1.5">
          {orderedQueueGroups(view.queue).map((group) => {
            const presentation = presentationForUse(group.use);
            const empty = group.count === 0;
            return (
              <article
                key={group.use}
                className={`bg-zinc-900 ${empty ? "px-3 py-1.5 opacity-50" : "p-3"}`}
              >
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <h4 className="font-semibold text-zinc-100">
                    {presentation.technicalComponent}
                  </h4>
                  <span className="text-zinc-500">{group.downstream}</span>
                  <span className="flex-1" />
                  <span
                    className={`chip border font-mono ${
                      empty
                        ? "border-zinc-800 bg-zinc-950 text-zinc-600"
                        : "border-zinc-700 bg-zinc-950 text-zinc-300"
                    }`}
                  >
                    {group.count}
                  </span>
                </div>
                {!empty && (
                  <p className="mt-0.5 text-[10px] text-zinc-600">
                    Güvenlik kapısı: {group.safety_gate}
                  </p>
                )}
                {!empty && (
                  <div className="mt-2 space-y-px">
                    {group.items.slice(0, QUEUE_ITEM_LIMIT).map((item) => (
                      <button
                        key={`${group.use}-${item.event_id}`}
                        type="button"
                        onClick={() => onOpenEvent(item.event_id)}
                        className="flex w-full items-center gap-3 bg-zinc-950 px-3 py-1.5 text-left hover:bg-zinc-800"
                      >
                        <span className="text-zinc-200">
                          {eventLabel(item.event_type)}
                        </span>
                        <span className="font-mono text-[10px] text-zinc-600">
                          {shortId(item.event_id)}
                        </span>
                        <span className="ml-auto font-mono text-zinc-400">
                          skor {item.learning_score}
                        </span>
                      </button>
                    ))}
                    {group.items.length > QUEUE_ITEM_LIMIT && (
                      <p className="px-1 pt-1 text-[10px] text-zinc-600">
                        + {group.items.length - QUEUE_ITEM_LIMIT} olay daha
                      </p>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </Section>
    </>
  );
}

function JobFacts({ job }: { job: TrainingJob }) {
  return (
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
  );
}

function JobHeader({
  job,
  children,
}: {
  job: TrainingJob;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-mono text-zinc-300">{shortId(job.job_id)}</span>
      <span className={`chip border ${jobStatusClass(job.status)}`}>
        {jobStatusLabel(job.status)}
      </span>
      <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
        {job.architecture}
      </span>
      <span className="font-mono text-zinc-500">
        {formatElapsed(job.elapsed_seconds)}
      </span>
      <span className="flex-1" />
      {children}
    </div>
  );
}

/**
 * Eğitim: münhasır iştir. Makinede duran iş üstte, insan bekleyen hata onun
 * altında, kapanmış geçmiş en altta durur.
 */
export function TrainingStage({
  view,
  busy,
  act,
  onOpenStage,
}: StageProps) {
  const { active, attention, history } = groupTrainingJobs(view.jobs);
  const models: (PipelineModelItem | null)[] = [...view.candidates, view.champion];

  const producedLink = (job: TrainingJob) => {
    const model = modelForJob(job.job_id, models);
    if (model === null) return null;
    return (
      <button
        type="button"
        onClick={() => onOpenStage(model.measured ? "promotion" : "measurement")}
        className="btn btn-outline-accent"
      >
        {shortId(model.version.model_version_id)} →
      </button>
    );
  };

  if (view.jobs.length === 0) {
    return <Empty>Kayıtlı eğitim işi yok.</Empty>;
  }

  return (
    <>
      {active.length > 0 && (
        <Section title="makinede">
          <div className="space-y-1.5">
            {active.map((job) => {
              const budget = gpuBudgetUsage(job);
              return (
                <article key={job.job_id} className="bg-zinc-900 p-3">
                  <JobHeader job={job}>
                    {job.status === "queued" && (
                      <button
                        type="button"
                        disabled={!view.readiness.can_run || busy !== ""}
                        title={
                          view.readiness.can_run
                            ? undefined
                            : readinessSummary(view.readiness)
                        }
                        onClick={() => act(job.job_id, () => runTrainingJob(job.job_id))}
                        className="btn btn-accent"
                      >
                        {busy === job.job_id ? "Başlatılıyor…" : "Eğitimi başlat"}
                      </button>
                    )}
                  </JobHeader>
                  {job.status === "running" && (
                    <div className="mt-2 space-y-0.5">
                      <div className="flex items-baseline justify-between text-[10px] text-zinc-500">
                        <span className="microlabel">gpu bütçesi</span>
                        <span className="font-mono">{budget.label}</span>
                      </div>
                      <div className="h-1 w-full bg-zinc-950">
                        <div
                          className="h-full bg-sky-700"
                          style={{ width: `${budget.percent}%` }}
                        />
                      </div>
                    </div>
                  )}
                  <JobFacts job={job} />
                </article>
              );
            })}
          </div>
        </Section>
      )}

      {attention.length > 0 && (
        <Section title="insan bekliyor" count={`${attention.length}`}>
          <div className="space-y-1.5">
            {attention.map((job) => (
              <article
                key={job.job_id}
                className="border border-amber-900/60 bg-zinc-900 p-3"
              >
                <JobHeader job={job}>
                  <span className="font-mono text-[10px] text-zinc-600">
                    {job.verified_frame_count} kare · epok {job.epochs} · yığın{" "}
                    {job.batch_size}
                  </span>
                </JobHeader>
                {job.error_message && (
                  <p className="mt-2 bg-amber-950/30 px-2 py-1 text-amber-200">
                    <span className="font-mono">{job.error_code}</span> · {job.error_message}
                  </p>
                )}
              </article>
            ))}
          </div>
        </Section>
      )}

      {history.length > 0 && (
        <Section title="geçmiş" count={`${history.length}`}>
          <div className="space-y-px">
            {history.map((job) => (
              <div
                key={job.job_id}
                className="flex flex-wrap items-center gap-2 bg-zinc-900 px-3 py-1.5"
              >
                <span className="font-mono text-zinc-400">{shortId(job.job_id)}</span>
                <span className={`chip border ${jobStatusClass(job.status)}`}>
                  {jobStatusLabel(job.status)}
                </span>
                <span className="text-zinc-600">{job.architecture}</span>
                <span className="font-mono text-[10px] text-zinc-600">
                  {job.verified_frame_count} kare · {job.source_video_count} video
                </span>
                <span className="flex-1" />
                <span className="font-mono text-[10px] text-zinc-600">
                  {formatElapsed(job.elapsed_seconds)}
                </span>
                {producedLink(job)}
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  );
}

/** Steps the API cannot own: they need an external, immutable test set. */
function measurementCommand(modelVersionId: string): string {
  return [
    "python scripts/dfine_feedback_training.py prepare-evaluation",
    `  --event-store runs/dortgoz.sqlite3 --model-version-id ${modelVersionId}`,
    "  --test-dataset-manifest <test-manifest.json> --coco-annotations <test.json>",
  ].join("\n");
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="btn btn-outline"
    >
      {copied ? "Kopyalandı" : "Kopyala"}
    </button>
  );
}

/** Ölçüm: üç sıralı adım. Yalnız ilki API'den, kalanı sabit test kümesi ister. */
export function MeasurementStage({ view, busy, act }: StageProps) {
  const measuring = view.candidates.filter((item) => !item.measured);
  if (measuring.length === 0) {
    return <Empty>Ölçüm bekleyen aday model yok.</Empty>;
  }
  return (
    <>
      {measuring.map((item) => {
        const modelVersionId = item.version.model_version_id;
        const job = view.jobs.find(
          (entry) => entry.job_id === item.version.training_job_id,
        );
        const command = measurementCommand(modelVersionId);
        return (
          <article key={modelVersionId} className="bg-zinc-900 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-zinc-300">{shortId(modelVersionId)}</span>
              <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
                {item.version.architecture}
              </span>
              {job && (
                <span className="font-mono text-[10px] text-zinc-600">
                  {job.verified_frame_count} kare · {job.source_video_count} video
                </span>
              )}
              <span className="flex-1" />
              {!item.onnx_exported && (
                <button
                  type="button"
                  disabled={busy !== ""}
                  onClick={() => act(
                    modelVersionId,
                    () => exportCandidateOnnx(modelVersionId),
                  )}
                  className="btn btn-accent"
                >
                  {busy === modelVersionId ? "Aktarılıyor…" : "ONNX'e aktar"}
                </button>
              )}
            </div>

            <ol className="mt-3 grid gap-px sm:grid-cols-3">
              {measurementSteps(item).map((step, index) => (
                <li
                  key={step.label}
                  className={`flex items-center gap-2 px-2.5 py-1.5 ${
                    step.done
                      ? "bg-emerald-950/30 text-emerald-200"
                      : "bg-zinc-950 text-zinc-500"
                  }`}
                >
                  <span
                    className={`flex h-4 w-4 shrink-0 items-center justify-center font-mono text-[10px] ${
                      step.done
                        ? "bg-emerald-800 text-emerald-100"
                        : "bg-zinc-800 text-zinc-500"
                    }`}
                  >
                    {step.done ? "✓" : index + 1}
                  </span>
                  {step.label}
                </li>
              ))}
            </ol>

            {item.onnx_exported && (
              <div className="mt-3 flex items-start gap-2">
                <pre className="min-w-0 flex-1 overflow-x-auto bg-zinc-950 p-2 font-mono text-[10px] text-zinc-400">
                  {command}
                </pre>
                <CopyButton text={command} />
              </div>
            )}
          </article>
        );
      })}
    </>
  );
}

/**
 * Terfi: kapı, adayı hem politikayla hem yürürlükteki modelle karşılaştırır.
 * Tablo üç sütunu yan yana koyar; düğme yalnız kapı açıksa çalışır.
 */
export function PromotionStage({ view, signed, busy, act }: StageProps) {
  const [reason, setReason] = useState("");
  const champion = view.champion;
  const measured = view.candidates.filter((item) => item.measured);

  return (
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
            <span className="flex-1" />
            <span className="text-[10px] text-zinc-500">
              terfi eden: {champion.version.approved_by ?? "—"}
            </span>
          </div>
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

      {measured.length === 0 ? (
        <Empty>Ölçümü tamamlanmış aday yok.</Empty>
      ) : (
        measured.map((item) => {
          const modelVersionId = item.version.model_version_id;
          const rows = promotionMetricRows(
            item.version.evaluation,
            champion?.version.evaluation ?? null,
            view.promotion_policy,
          );
          return (
            <article key={modelVersionId} className="bg-zinc-900 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-zinc-300">{shortId(modelVersionId)}</span>
                <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-400">
                  {item.version.architecture}
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
                <span className="flex-1" />
                {item.gate_passed && (
                  <input
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="terfi gerekçesi"
                    aria-label="terfi gerekçesi"
                    className="field w-48"
                  />
                )}
                <button
                  type="button"
                  disabled={!item.gate_passed || !signed || busy !== ""}
                  title={
                    item.gate_passed
                      ? (signed ? undefined : SIGN_HINT)
                      : item.gate_failures.join("; ")
                  }
                  onClick={() => act(modelVersionId, () => promoteModel(modelVersionId, {
                    approved_by: signed,
                    reason: reason.trim() || "ölçüm kapısı geçildi",
                  }))}
                  className="btn btn-accent"
                >
                  {busy === modelVersionId ? "Terfi ediliyor…" : "Terfi ettir"}
                </button>
              </div>

              <table className="mt-3 w-full text-left">
                <thead>
                  <tr className="microlabel">
                    <th className="w-1/3 font-semibold">ölçüt</th>
                    <th className="font-semibold">aday</th>
                    <th className="font-semibold">yürürlükte</th>
                    <th className="font-semibold">politika</th>
                  </tr>
                </thead>
                <tbody className="font-mono text-zinc-300">
                  {rows.map((row) => (
                    <tr key={row.label} className="border-t border-zinc-800">
                      <td className="py-1 font-sans text-zinc-400">{row.label}</td>
                      <td
                        className={`py-1 ${
                          row.passed === null
                            ? ""
                            : row.passed
                              ? "text-emerald-300"
                              : "text-amber-300"
                        }`}
                      >
                        {row.passed === null ? "" : row.passed ? "✓ " : "✕ "}
                        {row.candidate}
                      </td>
                      <td className="py-1 text-zinc-500">{row.champion}</td>
                      <td className="py-1 text-zinc-500">{row.limit}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-zinc-800">
                    <td className="py-1 font-sans text-zinc-400">gölge koşusu</td>
                    <td
                      className={`py-1 ${
                        item.shadow_passed ? "text-emerald-300" : "text-amber-300"
                      }`}
                      colSpan={3}
                    >
                      {item.shadow_passed ? "✓ geçti" : "✕ geçmedi"}
                    </td>
                  </tr>
                </tbody>
              </table>

              {item.gate_failures.length > 0 && (
                <details className="mt-2">
                  <summary className="microlabel cursor-pointer text-amber-300">
                    kapı kaydı ({item.gate_failures.length})
                  </summary>
                  <ul className="mt-1 space-y-0.5 font-mono text-[10px] text-amber-200/80">
                    {item.gate_failures.map((failure) => (
                      <li key={failure}>{failure}</li>
                    ))}
                  </ul>
                </details>
              )}
            </article>
          );
        })
      )}
    </>
  );
}
