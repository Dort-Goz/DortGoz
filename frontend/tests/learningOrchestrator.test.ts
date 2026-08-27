import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  approvalWaitingCount,
  candidateReviewReason,
  candidateStatus,
  operatorApprovalState,
  presentationForUse,
  primaryPresentationForCandidate,
  visibleDevelopmentSuggestions,
} from "../src/lib/learningPresentation";
import type {
  LearningCandidateSummary,
  LearningRoute,
  LearningRouteSummary,
} from "../src/types/domain";

const candidate = (
  values: Partial<LearningCandidateSummary> = {},
): LearningCandidateSummary => ({
  event_id: "event-1",
  event_type: "possible_theft",
  video_id: "video-1",
  learning_score: 70,
  learning_band: "high",
  intervention_score: 40,
  recommended_uses: [],
  ready_uses: [],
  blockers: ["İnsan incelemesi gerekli"],
  ...values,
});

const summary = (
  values: Partial<LearningRouteSummary> = {},
): LearningRouteSummary => ({
  use: "evaluation",
  recommended_count: 0,
  ready_count: 0,
  awaiting_gate_count: 0,
  downstream: "internal",
  safety_gate: "human",
  ...values,
});

describe("Öğrenme Merkezi operatör sunumu", () => {
  test("teknik kullanım türlerini operatörün anlayacağı problemlere eşler", () => {
    expect(presentationForUse("threshold_calibration").action).toBe(
      "Alarm hassasiyetini yeniden değerlendir.",
    );
    expect(presentationForUse("siglip_training").title).toBe(
      "Sistem bazı görüntüleri ayırt etmekte zorlanıyor",
    );
    expect(presentationForUse("d_fine_training").title).toBe(
      "Bazı kişi veya nesneler eksik tespit edilmiş olabilir",
    );
    expect(presentationForUse("prompt_example").title).toBe(
      "Olay yorumlamasında tutarsızlık tespit edildi",
    );
  });

  test("insan incelemesini öneri türlerinden önce açıklar", () => {
    const pending = candidate();

    expect(primaryPresentationForCandidate(pending).title).toBe(
      "Bu olay için insan kararı gerekiyor",
    );
    expect(candidateStatus(pending)).toBe("İnceleme gerekiyor");
    expect(candidateReviewReason(pending)).toContain("henüz insan kararı");
  });

  test("değişen inceleme için sade yeniden onay dili kullanır", () => {
    const stale = candidate({
      recommended_uses: ["evaluation"],
      blockers: ["Yeni inceleme nedeniyle izin yenilenmeli"],
    });
    const route: LearningRoute = {
      use: "evaluation",
      recommended: true,
      approval_state: "stale",
      ready: false,
      downstream: "internal",
      reason: "internal",
      safety_gate: "human",
    };

    expect(candidateStatus(stale)).toBe("Yeniden onay gerekiyor");
    expect(candidateReviewReason(stale)).toBe(
      "İnceleme sonucu değiştiği için yeniden onay gerekiyor.",
    );
    expect(operatorApprovalState(route)).toBe("Yeniden onay gerekiyor");
  });

  test("sıfır değerli önerileri ve ikincil KPI'ları sunumda gizler", () => {
    const visible = visibleDevelopmentSuggestions([
      summary(),
      summary({ use: "d_fine_training", recommended_count: 2, awaiting_gate_count: 2 }),
    ]);

    expect(visible.map((item) => item.use)).toEqual(["d_fine_training"]);
    expect(approvalWaitingCount(3, 2)).toBe(5);
  });

  test("ana ekran dört KPI, sade boş durumlar ve insan kapısını gösterir", () => {
    const source = readFileSync(
      new URL("../src/components/LearningOrchestratorPanel.tsx", import.meta.url),
      "utf8",
    );

    for (const label of ["Toplam olay", "İnceleme bekleyen", "Onay bekleyen", "Hazır işlemler"]) {
      expect(source).toContain(label);
    }
    expect(source).toContain("İnceleme kuyruğu");
    expect(source).toContain("İncelenecek olay yok.");
    expect(source).toContain("Şu anda işlem gerektiren bir geliştirme önerisi yok.");
    expect(source).toContain("visibleDevelopmentSuggestions");
    expect(source).toContain(
      "Otomatik eğitim ve canlı sisteme otomatik geçiş kapalıdır. Tüm geliştirmeler insan onayıyla ilerler.",
    );
    expect(source).not.toContain("Hazır rota");
    expect(source).not.toContain("Kayma gözcüsü");
    expect(source).not.toContain("Öğrenme rotaları");
    expect(source).not.toContain("SigLIP");
    expect(source).not.toContain("D-FINE");
  });

  test("teknik ayrıntılar kapalı bölümde kalır ve sistem gerçek konsoldan erişilir", () => {
    const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
    const detail = readFileSync(
      new URL("../src/components/TrainingReviewPanel.tsx", import.meta.url),
      "utf8",
    );

    expect(app).toContain("<LearningOrchestratorPanel");
    expect(app).toContain("!fixtureMode");
    expect(app).toContain("◈ öğrenme merkezi");
    expect(detail).toContain("<details");
    expect(detail).toContain("Teknik detaylar");
    expect(detail).toContain("presentation.technicalComponent");
    expect(detail).toContain("presentation.technicalType");
    expect(detail).not.toContain("Kayma gözcüsü");
  });
});
