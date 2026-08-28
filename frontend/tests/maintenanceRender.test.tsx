import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import {
  MeasurementStage,
  PromotionStage,
  QueueStage,
  TrainingStage,
  type StageProps,
} from "../src/components/MaintenanceStages";
import type {
  DevelopmentUse,
  LearningPipelineView,
  ModelEvaluation,
  ModelVersion,
  PipelineModelItem,
  PipelineQueueGroup,
  TrainingJob,
  TrainingJobStatus,
} from "../src/types/domain";

const queueGroup = (
  use: DevelopmentUse,
  count: number,
): PipelineQueueGroup => ({
  use,
  downstream: `${use} havuzu`,
  safety_gate: "insan onayı",
  count,
  items: Array.from({ length: count }, (_, index) => ({
    event_id: `event-${use}-${index}`,
    event_revision: 1,
    event_type: index % 2 === 0 ? "physical_fight" : "fire_smoke",
    review_id: "review-1",
    approval_id: "approval-1",
    use,
    learning_score: 70 - index,
    learning_band: "high" as const,
    downstream: `${use} havuzu`,
  })),
});

const trainingJob = (
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
  requested_by: "bakim.muhendisi",
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

const modelVersion = (
  modelVersionId: string,
  trainingJobId: string,
  values: Partial<ModelVersion> = {},
): ModelVersion => ({
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
  ...values,
});

const evaluation = (values: Partial<ModelEvaluation> = {}): ModelEvaluation => ({
  evaluation_id: "eval-1",
  checkpoint_sha256: "d".repeat(64),
  map_50_95: 0.612,
  map_50: 0.79,
  critical_recall: 0.941,
  false_alarms_per_hour: 2.2,
  p95_latency_ms: 188,
  peak_memory_mb: 2510,
  repetitions: 3,
  shadow_passed: true,
  evaluator: "bakim.muhendisi",
  measured_at: "2026-08-20T09:00:00Z",
  ...values,
});

const view = (values: Partial<LearningPipelineView> = {}): LearningPipelineView => ({
  pipeline_version: "dortgoz-learning-pipeline-v1",
  stages: [],
  review_items: [],
  approval_items: [],
  queue: [queueGroup("d_fine_training", 2), queueGroup("camera_rule", 0)],
  jobs: [],
  candidates: [],
  champion: null,
  readiness: {
    can_plan: true,
    can_run: true,
    blockers: [],
    active_workload: null,
    training_policy_version: "dfine-training-v1",
    promotion_policy_version: "dfine-promotion-v1",
  },
  training_policy: {
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
  },
  promotion_policy: {
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
  },
  drift: {
    state: "stable",
    score: 0,
    reviewed_events: 8,
    baseline_size: 4,
    current_size: 4,
    minimum_required: 8,
    metrics: [],
    mode: "shadow",
    automatic_training: false,
    automatic_promotion: false,
    generated_at: "2026-08-20T09:00:00Z",
  },
  mode: "human_gated",
  automatic_training: false,
  automatic_promotion: false,
  generated_at: "2026-08-20T09:00:00Z",
  ...values,
});

const props = (values: Partial<StageProps> = {}): StageProps => ({
  view: view(),
  signed: "bakim.muhendisi",
  busy: "",
  act: () => {},
  onOpenEvent: () => {},
  onOpenStage: () => {},
  ...values,
});

/** Etiketleri okumak için işaretlemeden düz metin çıkarır. */
const text = (markup: string): string =>
  markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("bakım alt sekmeleri ekrana ne yazar", () => {
  test("kuyruk yalnız D-FINE için kabul edilen kayıtları gösterir", () => {
    const markup = renderToStaticMarkup(<QueueStage {...props()} />);
    const body = text(markup);

    expect(body).toContain("en çok 20");
    expect(body).toContain("D-FINE kuyruğu 2");
    // Olay ham kimliğiyle değil, önce türüyle okunur.
    expect(body).toContain("kavga");
    expect(body).toContain("70");
    expect(body).not.toContain("Kamera kuralı");
    expect(body).not.toContain("Güvenlik kapısı");
    expect(markup).toContain('max="20"');
  });

  test("kuyruk hazır olmayan makinede paket düğmesini kapatır", () => {
    const blocked = view({
      readiness: {
        can_plan: false,
        can_run: false,
        blockers: ["Temel ağırlık dosyası bulunamadı"],
        active_workload: null,
        training_policy_version: "dfine-training-v1",
        promotion_policy_version: "dfine-promotion-v1",
      },
    });
    const markup = renderToStaticMarkup(<QueueStage {...props({ view: blocked })} />);

    expect(markup).toContain("disabled");
    expect(text(markup)).toContain("Temel ağırlık dosyası bulunamadı");
  });

  test("eğitim çalışan işin GPU bütçesini çubukla ve dakikayla verir", () => {
    const jobs = [
      trainingJob("job-live", "running", {
        started_at: "2026-08-20T09:00:00Z",
        elapsed_seconds: 2700,
      }),
      trainingJob("job-boom", "failed", {
        started_at: "2026-08-20T08:00:00Z",
        finished_at: "2026-08-20T08:07:00Z",
        elapsed_seconds: 420,
        error_code: "GPU_OUT_OF_MEMORY",
        error_message: "CUDA out of memory.",
      }),
      trainingJob("job-done", "succeeded", {
        started_at: "2026-08-20T07:00:00Z",
        finished_at: "2026-08-20T07:50:00Z",
        elapsed_seconds: 3000,
        checkpoint_ref: "runs/best.pth",
        checkpoint_sha256: "d".repeat(64),
      }),
    ];
    const markup = renderToStaticMarkup(
      <TrainingStage
        {...props({
          view: view({
            jobs,
            candidates: [
              {
                version: modelVersion("model-done", "job-done"),
                gate_failures: [],
                gate_passed: false,
                onnx_exported: false,
                measured: false,
                shadow_passed: false,
              },
            ],
          }),
        })}
      />,
    );
    const body = text(markup);

    expect(body).toContain("makinede");
    expect(body).toContain("45/90 dk");
    expect(markup).toContain("width:50%");
    // Hata insan bekleyen kümede kodu ve iletisiyle görünür.
    expect(body).toContain("insan bekliyor");
    expect(body).toContain("GPU_OUT_OF_MEMORY");
    // Geçmişteki iş ürettiği model sürümüne bağlanır.
    expect(body).toContain("geçmiş");
    expect(body).toContain("model-done");
  });

  test("ölçüm üç adımı sırayla ve tamamlananı işaretli gösterir", () => {
    const candidate: PipelineModelItem = {
      version: modelVersion("model-a", "job-a", {
        deployment: { onnx_ref: "models/a.onnx", onnx_sha256: "e".repeat(64) },
      }),
      gate_failures: ["candidate evaluation kaydı yok"],
      gate_passed: false,
      onnx_exported: true,
      measured: false,
      shadow_passed: false,
    };
    const body = text(
      renderToStaticMarkup(
        <MeasurementStage {...props({ view: view({ candidates: [candidate] }) })} />,
      ),
    );

    expect(body).toContain("ONNX aktarımı");
    expect(body).toContain("Dedektör ölçümü");
    expect(body).toContain("Gölge koşusu");
    // Aktarım bittiği için düğme değil, dış komut görünür.
    expect(body).not.toContain("ONNX'e aktar");
    expect(body).toContain("prepare-evaluation");
    expect(body).toContain("Kopyala");
  });

  test("terfi adayı politika ve yürürlükteki modelle yan yana koyar", () => {
    const champion: PipelineModelItem = {
      version: modelVersion("model-champ", "job-champ", {
        stage: "champion",
        evaluation: evaluation({
          map_50_95: 0.552,
          critical_recall: 0.912,
          false_alarms_per_hour: 3.1,
          p95_latency_ms: 196,
          peak_memory_mb: 2400,
        }),
        approved_by: "bakim.muhendisi",
        promotion_reason: "ölçüm kapısı geçildi",
        promotion_policy_version: "dfine-promotion-v1",
        promoted_at: "2026-08-20T09:00:00Z",
      }),
      gate_failures: [],
      gate_passed: true,
      onnx_exported: true,
      measured: true,
      shadow_passed: true,
    };
    const candidate: PipelineModelItem = {
      version: modelVersion("model-c", "job-c", { evaluation: evaluation() }),
      gate_failures: [],
      gate_passed: true,
      onnx_exported: true,
      measured: true,
      shadow_passed: true,
    };
    const body = text(
      renderToStaticMarkup(
        <PromotionStage
          {...props({ view: view({ champion, candidates: [candidate] }) })}
        />,
      ),
    );

    expect(body).toContain("Yürürlükteki model");
    expect(body).toContain("ölçüt");
    expect(body).toContain("aday");
    expect(body).toContain("yürürlükte");
    expect(body).toContain("politika");
    // Aday değeri, yürürlükteki değer ve politika sınırı aynı satırda okunur.
    expect(body).toContain("✓ 61.2%");
    expect(body).toContain("55.2%");
    expect(body).toContain("≥ 50.0%");
    expect(body).toContain("Kapıdan geçti");
    expect(body).toContain("✓ geçti");
  });

  test("terfi kapısı kapalı adayda düğmeyi kilitler ve düşen ölçütü işaretler", () => {
    const candidate: PipelineModelItem = {
      version: modelVersion("model-d", "job-d", {
        evaluation: evaluation({
          map_50_95: 0.474,
          critical_recall: 0.883,
          shadow_passed: false,
        }),
      }),
      gate_failures: ["mAP50-95 0.474 < 0.500", "shadow test geçmedi"],
      gate_passed: false,
      onnx_exported: true,
      measured: true,
      shadow_passed: false,
    };
    const markup = renderToStaticMarkup(
      <PromotionStage {...props({ view: view({ candidates: [candidate] }) })} />,
    );
    const body = text(markup);

    expect(markup).toContain("disabled");
    expect(body).toContain("Kapı engelli");
    expect(body).toContain("✕ 47.4%");
    expect(body).toContain("✕ geçmedi");
    // Politika sınırı ölçümü olmayan yürürlükteki modelde tire kalır.
    expect(body).toContain("kapı kaydı (2)");
  });

  test("imzasız konsolda hiçbir alt sekme işlem başlatmaz", () => {
    const candidate: PipelineModelItem = {
      version: modelVersion("model-c", "job-c", { evaluation: evaluation() }),
      gate_failures: [],
      gate_passed: true,
      onnx_exported: true,
      measured: true,
      shadow_passed: true,
    };
    const unsigned = props({ signed: "" });

    expect(renderToStaticMarkup(<QueueStage {...unsigned} />)).toContain("disabled");
    expect(
      renderToStaticMarkup(
        <PromotionStage
          {...props({ signed: "", view: view({ candidates: [candidate] }) })}
        />,
      ),
    ).toContain("disabled");
  });
});
