import type {
  PipelineModelItem,
  PipelineReadiness,
  PipelineStage,
  PipelineStageSummary,
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
