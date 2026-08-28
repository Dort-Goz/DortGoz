import { describe, expect, test } from "bun:test";
import { maintenanceSource } from "./maintenanceSource";
import { MAINTENANCE_STAGE_ORDER } from "../src/components/ModelMaintenancePanel";
import { readFileSync } from "node:fs";
import {
  approvalWaitingCount,
  candidateActionTitle,
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

  test("işlem başlığını olay türü ve sıradaki adımdan üretir", () => {
    expect(candidateActionTitle(candidate(), "olası hırsızlık")).toBe(
      "Olası hırsızlık kaydı inceleme bekliyor",
    );
    expect(candidateActionTitle(candidate({
      recommended_uses: ["camera_rule"],
      blockers: ["İnsan onayı gerekli"],
    }), "Olası hırsızlık")).toBe(
      "Olası hırsızlık kaydı kamera ayarı için onay bekliyor",
    );
    expect(candidateActionTitle(candidate({
      recommended_uses: ["evaluation"],
      ready_uses: ["evaluation"],
      blockers: [],
    }), "Olası hırsızlık")).toBe("Olası hırsızlık kaydı işleme hazır");
  });

  test("sıfır değerli önerileri ve ikincil KPI'ları sunumda gizler", () => {
    const visible = visibleDevelopmentSuggestions([
      summary(),
      summary({ use: "d_fine_training", recommended_count: 2, awaiting_gate_count: 2 }),
    ]);

    expect(visible.map((item) => item.use)).toEqual(["d_fine_training"]);
    expect(approvalWaitingCount(3, 2)).toBe(5);
  });

  test("bakım ekranı kanıt görselini ve mühendis dilini taşır", () => {
    const source = maintenanceSource();

    expect(source).toContain("getIncidentMedia");
    expect(source).toContain("media.thumbnail_url");
    expect(source).toContain("Kanıt {shortClock(media.clip_start)}");
    // Kuyruk bileşeni operatör örtmecesiyle değil teknik adıyla anılır.
    expect(source).toContain("presentationForUse(group.use)");
    expect(source).toContain("presentation.technicalComponent");
    expect(source).not.toContain("presentation.title");
    expect(source).toContain("Besleyeceği bileşenler");
    expect(source).toContain("İnceleme bekleyen olay yok.");
    expect(source).toContain("Fine-tune onayı bekleyen olay yok.");
    expect(source).toContain("refreshToken: number;");
    expect(source).toContain("[load, refreshToken]");
    expect(MAINTENANCE_STAGE_ORDER).toEqual([
      "review", "approval", "queue", "training", "measurement", "promotion",
    ]);
    expect(source).toContain('active === "review"');
    expect(source).toContain('review: "İnceleme bekliyor"');
    expect(source).toContain('actionLabel="Olayı incele"');
    expect(source).toContain('actionLabel="Fine-tune için değerlendir"');
    expect(source).toContain("grid grid-cols-1 sm:grid-cols-[11rem_minmax(0,1fr)_minmax(10rem,12rem)]");
    expect(source).toContain("btn btn-accent btn-wrap min-w-0 w-full");
  });

  test("teknik ayrıntılar kapalı bölümde kalır ve öğrenme kendi çalışma alanıdır", () => {
    const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
    const detail = readFileSync(
      new URL("../src/components/TrainingReviewPanel.tsx", import.meta.url),
      "utf8",
    );

    expect(app).toContain("<ModelMaintenancePanel");
    expect(app).toContain("!fixtureMode");
    // Bakım sekmesi operatör sekmelerinden ayrı bir kümede durur.
    expect(app).toContain('workspaceTab("maintenance", "Bakım")');
    expect(app).toContain('aria-label="Bakım çalışma alanı"');
    expect(app).not.toContain('["maintenance", "Bakım"]');
    expect(app).not.toContain("LearningOrchestratorPanel");
    expect(app).not.toContain("◈ öğrenme merkezi");
    expect(detail).toContain("<details");
    expect(detail).toContain("Teknik detaylar");
    expect(detail).toContain("presentation.technicalComponent");
    expect(detail).toContain("presentation.technicalType");
    expect(detail).not.toContain("Kayma gözcüsü");
    expect(app).toContain("openedFromMaintenance");
    expect(app).toContain("onReviewEvent={openMaintenanceReviewEvent}");
    expect(app).toContain("handoffEventId={maintenanceHandoffEventId}");
    expect(app).toContain('history.replaceState(null, "", "#bakim")');
    expect(app).toContain("onBack={openedFromMaintenance ? returnToMaintenance : undefined}");
    expect(app).toContain("refreshToken={maintenanceRefreshToken}");
    expect(app).toContain("setMaintenanceRefreshToken((current) => current + 1)");
    expect(detail).toContain("← Geri dön");
    expect(detail).toContain("Bakım ekranına dön");
    expect(detail).toContain("Fine-tune adayına al");
  });

  test("olay kararıyla bakım iznini ayrı görev kiplerinde tutar", () => {
    const detail = readFileSync(
      new URL("../src/components/TrainingReviewPanel.tsx", import.meta.url),
      "utf8",
    );

    expect(detail).toContain("Bu olayın “${canonicalEventLabel}” olduğu doğru mu?");
    expect(detail).toContain("Evet, doğru");
    expect(detail).toContain("Hayır, düzenle");
    expect(detail).toContain("Anomali yok");
    expect(detail).toContain("grid grid-cols-3 gap-2");
    expect(detail).toContain("border-amber-500 bg-amber-500");
    expect(detail).toContain('setReviewVerdict("anomali")');
    expect(detail.indexOf("Hayır, düzenle")).toBeLessThan(
      detail.indexOf("Anomali yok"),
    );
    expect(detail).not.toContain("Görüntüyü kontrol edin. Kararınızı verin.");
    expect(detail).not.toContain(">Olay sonucu</h3>");
    expect(detail).not.toContain("Hayır, sorun yok");
    expect(detail).not.toContain("Ayrıntılı karara git");
    expect(detail).not.toContain("Hayır, yanlış");
    expect(detail).toContain("Fine-tune adayına alınsın mı?");
    expect(detail).toContain("İstemiyorum");
    expect(detail).toContain("rejectEventLearningApproval");
    expect(detail).toContain("Kayıt fine-tune hazırlığından çıkarıldı.");
    expect(detail).toContain("Fine-tune adayına al ve kapat");
    expect(detail).toContain("approved_uses: recommendedApprovalUses");
    expect(detail).toContain('export type TrainingReviewMode = "review" | "maintenance";');
    expect(detail).toContain('mode === "review"');
    expect(detail).toContain('mode === "maintenance"');
    expect(detail).toContain("onReviewSaved?.()");
    expect(detail).toContain("const reviewPending = latestReview === null || reviewRestarted;");
    expect(detail).toContain("onClick={restartReview}");
    expect(detail).toContain("{!reviewPending ? (");
    expect(detail).toContain('mode === "maintenance"');
    expect(detail).toContain("&& recommendedApprovalUses.length > 0 && (");
    expect(detail).toContain('setReviewNote("");');
    expect(detail).toContain('setIntervention("");');
    expect(detail).toContain("Diğer bilgiler");
    expect(detail).toContain("Önceki insan kararları bu alandadır.");
    expect(detail).toContain("İnsan kararı bağlamı ve bakım araçları bu alandadır.");
    expect(detail).not.toContain("Olay İnceleme ve Geliştirme Hazırlığı");
    expect(detail).not.toContain("Sol taraftaki adımları tamamlayın");
  });
});
