import type {
  DevelopmentUse,
  DfineTrainingPolicy,
  ModelEvaluation,
  PipelineModelItem,
  PipelineQueueGroup,
  PipelineReadiness,
  PipelineStage,
  PipelineStageSummary,
  PromotionPolicy,
  TrainingJob,
  TrainingJobStatus,
} from "../types/domain";

export const STAGE_ORDER: PipelineStage[] = [
  "review",
  "approval",
  "queue",
  "training",
  "measurement",
  "promotion",
];

export const STAGE_TR: Record<PipelineStage, string> = {
  review: "İnceleme",
  approval: "Onay",
  queue: "Kuyruk",
  training: "Eğitim",
  measurement: "Ölçüm",
  promotion: "Terfi",
};

const JOB_STATUS_TR: Record<TrainingJobStatus, string> = {
  queued: "kuyrukta",
  running: "çalışıyor",
  succeeded: "tamamlandı",
  failed: "başarısız",
  cancelled: "iptal edildi",
  budget_stopped: "bütçe sınırında durdu",
  interrupted: "yarıda kesildi",
};

const JOB_STATUS_CLS: Record<TrainingJobStatus, string> = {
  queued: "border-zinc-700 bg-zinc-950 text-zinc-300",
  running: "border-sky-800 bg-sky-950/40 text-sky-200",
  succeeded: "border-emerald-900 bg-emerald-950/30 text-emerald-200",
  failed: "border-red-900 bg-red-950/40 text-red-200",
  cancelled: "border-zinc-700 bg-zinc-950 text-zinc-400",
  budget_stopped: "border-amber-900 bg-amber-950/30 text-amber-200",
  interrupted: "border-amber-900 bg-amber-950/30 text-amber-200",
};

export const jobStatusLabel = (status: TrainingJobStatus): string =>
  JOB_STATUS_TR[status];

export const jobStatusClass = (status: TrainingJobStatus): string =>
  JOB_STATUS_CLS[status];

/** The stage the engineer should land on: the first one that has work waiting. */
export function firstActionableStage(
  stages: PipelineStageSummary[],
): PipelineStage {
  const blocked = stages.find((stage) => stage.blocked_count > 0);
  if (blocked) return blocked.stage;
  const pending = stages.find((stage) => stage.count > 0);
  return pending?.stage ?? "queue";
}

/** Ölçüm is three ordered sub-steps; a candidate can sit part way through. */
export function measurementSteps(
  item: PipelineModelItem,
): { label: string; done: boolean }[] {
  return [
    { label: "ONNX aktarımı", done: item.onnx_exported },
    { label: "Dedektör ölçümü", done: item.measured },
    { label: "Gölge koşusu", done: item.shadow_passed },
  ];
}

export function readinessSummary(readiness: PipelineReadiness): string {
  if (readiness.active_workload) {
    return `Münhasır iş çalışıyor: ${readiness.active_workload}`;
  }
  if (readiness.can_run) return "Eğitim başlatılabilir";
  if (readiness.blockers.length > 0) return readiness.blockers[0];
  return "Eğitim şu anda başlatılamaz";
}

export function formatElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  if (hours > 0) return `${hours}s ${String(minutes).padStart(2, "0")}dk`;
  return `${minutes}dk ${String(whole % 60).padStart(2, "0")}sn`;
}

/** Percentage-style metric for the promotion table; null stays a dash. */
export const ratio = (value: number | null | undefined): string =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;

/**
 * D-FINE havuzu eğitim paketini besler; değerlendirme havuzu ölçüm kümesini
 * besler. İkisi başa gelir, kalanı dolu olan öne çıkar.
 */
const QUEUE_PRIORITY: DevelopmentUse[] = ["d_fine_training", "evaluation"];

export function orderedQueueGroups(
  groups: PipelineQueueGroup[],
): PipelineQueueGroup[] {
  const rank = (group: PipelineQueueGroup) => {
    const pinned = QUEUE_PRIORITY.indexOf(group.use);
    if (pinned >= 0) return pinned;
    return group.count > 0 ? 2 : 3;
  };
  return [...groups].sort(
    (left, right) => rank(left) - rank(right) || right.count - left.count,
  );
}

export interface TrainingFieldLimit {
  max: number;
  hint: string;
}

/** Politika üst sınırları form alanlarına iner; kullanıcı sınırı aşamaz. */
export function trainingLimits(policy: DfineTrainingPolicy | null): {
  epochs: TrainingFieldLimit;
  batchSize: TrainingFieldLimit;
  gpuMinutes: TrainingFieldLimit;
} {
  return {
    epochs: {
      max: policy?.maximum_epochs ?? 500,
      hint: policy ? `en çok ${policy.maximum_epochs}` : "politika okunamadı",
    },
    batchSize: {
      max: policy?.maximum_batch_size ?? 128,
      hint: policy ? `en çok ${policy.maximum_batch_size}` : "politika okunamadı",
    },
    gpuMinutes: {
      max: policy?.maximum_gpu_minutes_per_job ?? 1440,
      hint: policy
        ? `en çok ${policy.maximum_gpu_minutes_per_job} dk`
        : "politika okunamadı",
    },
  };
}

export const clampToLimit = (value: number, limit: TrainingFieldLimit): number =>
  Math.min(Math.max(1, Math.floor(value) || 1), limit.max);

const ACTIVE_JOB_STATES: TrainingJobStatus[] = ["queued", "running"];
const ATTENTION_JOB_STATES: TrainingJobStatus[] = [
  "failed",
  "interrupted",
  "budget_stopped",
];

/**
 * Eğitim sekmesi üç kümeye ayrılır: makinede duran iş, insan bekleyen iş ve
 * kapanmış geçmiş. Aynı sıralama aşama sayacındaki count/blocked ile örtüşür.
 */
export function groupTrainingJobs(jobs: TrainingJob[]): {
  active: TrainingJob[];
  attention: TrainingJob[];
  history: TrainingJob[];
} {
  return {
    active: jobs.filter((job) => ACTIVE_JOB_STATES.includes(job.status)),
    attention: jobs.filter((job) => ATTENTION_JOB_STATES.includes(job.status)),
    history: jobs.filter(
      (job) => job.status === "succeeded" || job.status === "cancelled",
    ),
  };
}

/** Çalışan işin GPU bütçesinde nerede olduğu; bütçe dakika cinsindendir. */
export function gpuBudgetUsage(job: TrainingJob): {
  percent: number;
  label: string;
} {
  const budgetSeconds = Math.max(1, job.max_gpu_minutes * 60);
  const percent = Math.min(100, (job.elapsed_seconds / budgetSeconds) * 100);
  const spent = Math.floor(job.elapsed_seconds / 60);
  return { percent, label: `${spent}/${job.max_gpu_minutes} dk` };
}

/** Bir eğitim işinin ürettiği model sürümü; yoksa null. */
export function modelForJob(
  jobId: string,
  items: (PipelineModelItem | null)[],
): PipelineModelItem | null {
  return (
    items.find((item) => item !== null && item.version.training_job_id === jobId)
    ?? null
  );
}

export interface PromotionMetricRow {
  label: string;
  candidate: string;
  champion: string;
  limit: string;
  passed: boolean | null;
}

const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

/**
 * Terfi kapısını okunur kılan karşılaştırma: aday, yürürlükteki model ve
 * politika sınırı yan yana durur. passed null ise ölçüm yok.
 */
export function promotionMetricRows(
  candidate: ModelEvaluation | null,
  champion: ModelEvaluation | null,
  policy: PromotionPolicy | null,
): PromotionMetricRow[] {
  const row = (
    label: string,
    read: (evaluation: ModelEvaluation) => number,
    format: (value: number) => string,
    limit: number | null,
    ok: (value: number, limit: number) => boolean,
    limitText: (limit: number) => string,
  ): PromotionMetricRow => {
    const value = candidate ? read(candidate) : null;
    return {
      label,
      candidate: value === null ? "—" : format(value),
      champion: champion ? format(read(champion)) : "—",
      limit: limit === null ? "—" : limitText(limit),
      passed: value === null || limit === null ? null : ok(value, limit),
    };
  };
  const atLeast = (value: number, limit: number) => value >= limit;
  const atMost = (value: number, limit: number) => value <= limit;
  return [
    row(
      "mAP50-95",
      (item) => item.map_50_95,
      percent,
      policy?.minimum_map_50_95 ?? null,
      atLeast,
      (limit) => `≥ ${percent(limit)}`,
    ),
    row(
      "kritik recall",
      (item) => item.critical_recall,
      percent,
      policy?.minimum_critical_recall ?? null,
      atLeast,
      (limit) => `≥ ${percent(limit)}`,
    ),
    row(
      "yanlış alarm/saat",
      (item) => item.false_alarms_per_hour,
      (value) => value.toFixed(1),
      policy?.maximum_false_alarms_per_hour ?? null,
      atMost,
      (limit) => `≤ ${limit.toFixed(1)}`,
    ),
    row(
      "p95 gecikme",
      (item) => item.p95_latency_ms,
      (value) => `${value.toFixed(0)} ms`,
      policy?.maximum_p95_latency_ms ?? null,
      atMost,
      (limit) => `≤ ${limit.toFixed(0)} ms`,
    ),
    row(
      "tepe bellek",
      (item) => item.peak_memory_mb,
      (value) => `${value} MB`,
      policy?.maximum_peak_memory_mb ?? null,
      atMost,
      (limit) => `≤ ${limit} MB`,
    ),
    row(
      "tekrar",
      (item) => item.repetitions,
      (value) => `${value}`,
      policy?.minimum_repetitions ?? null,
      atLeast,
      (limit) => `≥ ${limit}`,
    ),
  ];
}
