import { describe, expect, test } from "bun:test";
import { maintenanceSource } from "./maintenanceSource";
import {
  clampToLimit,
  gpuBudgetUsage,
  groupTrainingJobs,
  modelForJob,
  orderedQueueGroups,
  promotionMetricRows,
  trainingLimits,
} from "../src/lib/pipelinePresentation";
import type {
  DfineTrainingPolicy,
  ModelEvaluation,
  PipelineModelItem,
  PipelineQueueGroup,
  PromotionPolicy,
  TrainingJob,
  TrainingJobStatus,
} from "../src/types/domain";

const group = (
  use: PipelineQueueGroup["use"],
  count: number,
): PipelineQueueGroup => ({
  use,
  downstream: "havuz",
  safety_gate: "insan onayı",
  count,
  items: Array.from({ length: count }, (_, index) => ({
    event_id: `event-${use}-${index}`,
    event_revision: 1,
    event_type: "physical_fight",
    review_id: "review-1",
    approval_id: "approval-1",
    use,
    learning_score: 70 - index,
    learning_band: "high" as const,
    downstream: "havuz",
  })),
});

const trainingPolicy: DfineTrainingPolicy = {
  policy_version: "dfine-training-v1",
  minimum_verified_frames: 100,
  minimum_train_frames: 80,
  minimum_validation_frames: 20,
  minimum_source_videos: 10,
  maximum_epochs: 20,
  maximum_batch_size: 4,
  maximum_workers: 4,
  maximum_gpu_minutes_per_job: 90,
  maximum_gpu_minutes_per_day: 120,
  allowed_architectures: ["dfine_n", "dfine_s"],
};

const promotionPolicy: PromotionPolicy = {
  policy_version: "dfine-promotion-v1",
  minimum_map_50_95: 0.5,
  minimum_critical_recall: 0.9,
  maximum_false_alarms_per_hour: 4,
  maximum_p95_latency_ms: 250,
  maximum_peak_memory_mb: 4096,
  minimum_repetitions: 3,
  maximum_critical_recall_drop: 0,
  maximum_false_alarm_increase: 0,
  maximum_latency_increase_ratio: 0.1,
};

const job = (
  jobId: string,
  status: TrainingJobStatus,
  values: Partial<TrainingJob> = {},
): TrainingJob => ({
  job_id: jobId,
  dataset_id: "dataset-1",
  dataset_fingerprint: "a".repeat(64),
  export_fingerprint: "b".repeat(64),
  export_ref: "datasets/coco.json",
  selection_policy_version: "dfine-selection-v1",
  architecture: "dfine_n",
  category_names: ["person"],
  verified_frame_count: 240,
  train_frame_count: 192,
  validation_frame_count: 48,
  source_video_count: 14,
  box_count: 720,
  dfine_repository_revision: "c".repeat(40),
  seed: 0,
  epochs: 12,
  batch_size: 2,
  workers: 2,
  gpu_index: 0,
  max_gpu_minutes: 90,
  daily_gpu_minutes: 120,
  status,
  requested_by: "muhendis",
  output_ref: "runs/dfine/1",
  checkpoint_ref: null,
  checkpoint_sha256: null,
  started_at: null,
  finished_at: null,
  elapsed_seconds: 0,
  error_code: null,
  error_message: null,
  created_at: "2026-08-20T09:00:00Z",
  updated_at: "2026-08-20T09:00:00Z",
  revision: 1,
  ...values,
});

const evaluation = (values: Partial<ModelEvaluation> = {}): ModelEvaluation => ({
  evaluation_id: "eval-1",
  checkpoint_sha256: "d".repeat(64),
  map_50_95: 0.61,
  map_50: 0.79,
  critical_recall: 0.94,
  false_alarms_per_hour: 2.2,
  p95_latency_ms: 188,
  peak_memory_mb: 2510,
  repetitions: 3,
  shadow_passed: true,
  evaluator: "muhendis",
  measured_at: "2026-08-20T09:00:00Z",
  ...values,
});

const model = (
  modelVersionId: string,
  trainingJobId: string,
  values: Partial<PipelineModelItem> = {},
): PipelineModelItem => ({
  version: {
    model_version_id: modelVersionId,
    training_job_id: trainingJobId,
    architecture: "dfine_n",
    checkpoint_ref: "runs/a.pth",
    checkpoint_sha256: "d".repeat(64),
    stage: "candidate",
    evaluation: null,
    deployment: null,
    promotion_policy_version: null,
    approved_by: null,
    promotion_reason: null,
    created_at: "2026-08-20T09:00:00Z",
    promoted_at: null,
    retired_at: null,
  },
  gate_failures: [],
  gate_passed: false,
  onnx_exported: false,
  measured: false,
  shadow_passed: false,
  ...values,
});

describe("bakım alt sekmeleri", () => {
  test("kuyrukta eğitim paketini besleyen havuzları başa alır", () => {
    const ordered = orderedQueueGroups([
      group("camera_rule", 0),
      group("prompt_example", 3),
      group("evaluation", 4),
      group("siglip_training", 0),
      group("d_fine_training", 2),
    ]);

    expect(ordered.map((item) => item.use)).toEqual([
      "d_fine_training",
      "evaluation",
      "prompt_example",
      "camera_rule",
      "siglip_training",
    ]);
  });

  test("boş havuzu listeden atmaz; neden görünür kalır", () => {
    const ordered = orderedQueueGroups([group("camera_rule", 0), group("evaluation", 1)]);

    expect(ordered).toHaveLength(2);
    expect(ordered.some((item) => item.count === 0)).toBe(true);
  });

  test("form üst sınırlarını eğitim politikasından okur", () => {
    const limits = trainingLimits(trainingPolicy);

    expect(limits.epochs.max).toBe(20);
    expect(limits.batchSize.hint).toBe("en çok 4");
    expect(limits.gpuMinutes.hint).toBe("en çok 90 dk");
    expect(clampToLimit(500, limits.epochs)).toBe(20);
    expect(clampToLimit(0, limits.batchSize)).toBe(1);
    expect(clampToLimit(12, limits.epochs)).toBe(12);
  });

  test("politika okunamadığında alan sınırı şema üst sınırına düşer", () => {
    const limits = trainingLimits(null);

    expect(limits.epochs.max).toBe(500);
    expect(limits.epochs.hint).toBe("politika okunamadı");
  });

  test("eğitim işlerini makine, insan ve geçmiş kümelerine ayırır", () => {
    const jobs = [
      job("job-1", "running"),
      job("job-2", "queued"),
      job("job-3", "failed"),
      job("job-4", "budget_stopped"),
      job("job-5", "interrupted"),
      job("job-6", "succeeded"),
      job("job-7", "cancelled"),
    ];

    const grouped = groupTrainingJobs(jobs);

    expect(grouped.active.map((item) => item.job_id)).toEqual(["job-1", "job-2"]);
    expect(grouped.attention.map((item) => item.job_id)).toEqual([
      "job-3",
      "job-4",
      "job-5",
    ]);
    expect(grouped.history.map((item) => item.job_id)).toEqual(["job-6", "job-7"]);
  });

  test("GPU bütçesini oranla ve dakikayla verir, tavanı aşmaz", () => {
    expect(gpuBudgetUsage(job("job-1", "running", { elapsed_seconds: 2700 }))).toEqual({
      percent: 50,
      label: "45/90 dk",
    });
    expect(
      gpuBudgetUsage(job("job-1", "running", { elapsed_seconds: 99999 })).percent,
    ).toBe(100);
  });

  test("eğitim işini ürettiği model sürümüne bağlar", () => {
    const items = [model("model-a", "job-a"), null, model("model-b", "job-b")];

    expect(modelForJob("job-b", items)?.version.model_version_id).toBe("model-b");
    expect(modelForJob("job-yok", items)).toBeNull();
  });

  test("terfi tablosunu aday, yürürlükteki model ve politika sınırıyla kurar", () => {
    const rows = promotionMetricRows(
      evaluation(),
      evaluation({
        map_50_95: 0.552,
        critical_recall: 0.912,
        false_alarms_per_hour: 3.1,
        p95_latency_ms: 196,
        peak_memory_mb: 2400,
      }),
      promotionPolicy,
    );

    expect(rows.map((row) => row.label)).toEqual([
      "mAP50-95",
      "kritik recall",
      "yanlış alarm/saat",
      "p95 gecikme",
      "tepe bellek",
      "tekrar",
    ]);
    expect(rows[0]).toEqual({
      label: "mAP50-95",
      candidate: "61.0%",
      champion: "55.2%",
      limit: "≥ 50.0%",
      passed: true,
    });
    expect(rows.every((row) => row.passed === true)).toBe(true);
  });

  test("sınırı aşan ölçümü kapıdan geçmemiş olarak işaretler", () => {
    const rows = promotionMetricRows(
      evaluation({
        map_50_95: 0.474,
        critical_recall: 0.883,
        false_alarms_per_hour: 5.3,
        p95_latency_ms: 264,
        repetitions: 2,
      }),
      null,
      promotionPolicy,
    );

    expect(rows.filter((row) => row.passed === false).map((row) => row.label)).toEqual([
      "mAP50-95",
      "kritik recall",
      "yanlış alarm/saat",
      "p95 gecikme",
      "tekrar",
    ]);
    expect(rows[0].champion).toBe("—");
  });

  test("ölçümü olmayan adayda karar vermez", () => {
    const rows = promotionMetricRows(null, null, promotionPolicy);

    expect(rows.every((row) => row.passed === null)).toBe(true);
    expect(rows.every((row) => row.candidate === "—")).toBe(true);
  });

  test("politika okunamadığında terfi sınırını uydurmaz", () => {
    const rows = promotionMetricRows(evaluation(), null, null);

    expect(rows.every((row) => row.limit === "—")).toBe(true);
    expect(rows.every((row) => row.passed === null)).toBe(true);
  });

  test("dört alt sekme insan onayı olmadan işlem başlatmaz", () => {
    const source = maintenanceSource();

    // Kuyruk: paket, hem makine hazır hem imza dolu olmadan kurulmaz.
    expect(source).toContain("disabled={!view.readiness.can_plan || !signed");
    // Eğitim: münhasır iş çalışırken başlatma düğmesi kapalıdır.
    expect(source).toContain("disabled={!view.readiness.can_run");
    // Terfi: kapıdan geçmeyen ya da imzasız aday terfi edemez.
    expect(source).toContain("disabled={!item.gate_passed || !signed");
  });

  test("alt sekmeler ölçüm ve terfi kanıtını ekranda tutar", () => {
    const source = maintenanceSource();

    // Ölçüm: üç adım ve dışarıda çalışacak komut birlikte görünür.
    expect(source).toContain("measurementSteps(item)");
    expect(source).toContain("prepare-evaluation");
    // Terfi: aday sayısı politikanın yanında durur.
    expect(source).toContain("promotionMetricRows");
    // Eğitim: çalışan işin GPU bütçesi görünür.
    expect(source).toContain("gpuBudgetUsage(job)");
  });
});
