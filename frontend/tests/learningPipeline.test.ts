import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  STAGE_ORDER,
  STAGE_TR,
  firstActionableStage,
  formatElapsed,
  jobStatusLabel,
  measurementSteps,
  ratio,
  readinessSummary,
} from "../src/lib/pipelinePresentation";
import type {
  PipelineModelItem,
  PipelineReadiness,
  PipelineStage,
  PipelineStageSummary,
} from "../src/types/domain";

const stage = (
  name: PipelineStage,
  count = 0,
  blocked = 0,
): PipelineStageSummary => ({
  stage: name,
  count,
  blocked_count: blocked,
  action_label: "işlem",
  detail: "ayrıntı",
});

const readiness = (values: Partial<PipelineReadiness> = {}): PipelineReadiness => ({
  can_plan: true,
  can_run: true,
  blockers: [],
  active_workload: null,
  training_policy_version: "dfine-training-v1",
  promotion_policy_version: "dfine-promotion-v1",
  ...values,
});

const candidate = (values: Partial<PipelineModelItem> = {}): PipelineModelItem => ({
  version: {
    model_version_id: "model-1",
    training_job_id: "job-1",
    architecture: "dfine_n",
    checkpoint_ref: "runs/a.pth",
    checkpoint_sha256: "a".repeat(64),
    stage: "candidate",
    evaluation: null,
    deployment: null,
    promotion_policy_version: null,
    approved_by: null,
    promotion_reason: null,
    created_at: "2026-08-27T00:00:00Z",
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

describe("öğrenme hattı sunumu", () => {
  test("altı aşamayı veri akışı sırasında tutar", () => {
    expect(STAGE_ORDER).toEqual([
      "review",
      "approval",
      "queue",
      "training",
      "measurement",
      "promotion",
    ]);
    expect(STAGE_ORDER.map((name) => STAGE_TR[name])).toEqual([
      "İnceleme",
      "Onay",
      "Kuyruk",
      "Eğitim",
      "Ölçüm",
      "Terfi",
    ]);
  });

  test("engelli aşamayı bekleyen aşamadan önce açar", () => {
    const stages = [
      stage("review", 12),
      stage("approval", 4),
      stage("queue"),
      stage("training", 0, 1),
      stage("measurement"),
      stage("promotion"),
    ];

    expect(firstActionableStage(stages)).toBe("training");
    expect(firstActionableStage([stage("review", 12), stage("approval", 4)]))
      .toBe("review");
    expect(firstActionableStage([stage("review"), stage("approval")])).toBe("queue");
  });

  test("ölçüm adımlarını sırayla ve kısmi tamamlanmayla gösterir", () => {
    expect(measurementSteps(candidate()).map((step) => step.done)).toEqual([
      false,
      false,
      false,
    ]);
    const partial = measurementSteps(
      candidate({ onnx_exported: true, measured: true }),
    );
    expect(partial.map((step) => step.label)).toEqual([
      "ONNX aktarımı",
      "Dedektör ölçümü",
      "Gölge koşusu",
    ]);
    expect(partial.map((step) => step.done)).toEqual([true, true, false]);
  });

  test("çalıştırma engelini insan diliyle özetler", () => {
    expect(readinessSummary(readiness())).toBe("Eğitim başlatılabilir");
    expect(
      readinessSummary(readiness({ can_run: false, active_workload: "training" })),
    ).toBe("Münhasır iş çalışıyor: training");
    expect(
      readinessSummary(
        readiness({ can_plan: false, can_run: false, blockers: ["depo yok"] }),
      ),
    ).toBe("depo yok");
  });

  test("süre ve oran biçimlerini sabit tutar", () => {
    expect(formatElapsed(0)).toBe("0dk 00sn");
    expect(formatElapsed(95)).toBe("1dk 35sn");
    expect(formatElapsed(3725)).toBe("1s 02dk");
    expect(ratio(0.6123)).toBe("61.2%");
    expect(ratio(null)).toBe("—");
    expect(ratio(undefined)).toBe("—");
    expect(jobStatusLabel("budget_stopped")).toBe("bütçe sınırında durdu");
  });

  test("otomatik eğitim kapalı sözleşmesini arayüzde yazar", () => {
    const source = readFileSync(
      new URL("../src/components/LearningPipelinePanel.tsx", import.meta.url),
      "utf8",
    );

    expect(source).toContain("otomatik eğitim ve otomatik");
    expect(source).toContain("terfi KAPALIDIR");
    expect(source).toContain("Paket oluştur");
    expect(source).toContain("Eğitimi başlat");
    expect(source).toContain("Terfi ettir");
    // Terfi düğmesi kapıdan geçmeyen adayda kapalı kalır.
    expect(source).toContain("disabled={!item.gate_passed");
    // Eğitim düğmesi hazır olmayan makinede kapalı kalır.
    expect(source).toContain("disabled={!view.readiness.can_run");
    expect(source).toContain("disabled={\n                    !view.readiness.can_plan");
  });
});
