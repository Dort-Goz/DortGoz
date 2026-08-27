import { describe, expect, test } from "bun:test";
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

  test("ana ekran dört KPI, sade boş durumlar ve insan kapısını gösterir", () => {
    const source = readFileSync(
      new URL("../src/components/LearningOrchestratorPanel.tsx", import.meta.url),
      "utf8",
    );

    for (const label of ["Toplam olay", "İnceleme bekleyen", "Onay bekleyen", "Hazır işlemler"]) {
      expect(source).toContain(label);
    }
    expect(source).toContain("İşlem bekleyen olaylar");
    expect(source).toContain("Şu anda işlem bekleyen olay yok.");
    expect(source).toContain("getIncidentMedia");
    expect(source).toContain("media.thumbnail_url");
    const queueSource = source
      .split('<section aria-labelledby="pending-events-title">')[1]
      .split('<section aria-labelledby="development-suggestions-title">')[0];
    expect(queueSource).toContain("candidateActionTitle(candidate, eventLabel)");
    expect(queueSource).toContain("Kanıt {shortClock(media.clip_start)}");
    expect(queueSource).not.toContain("presentation.description");
    expect(queueSource).not.toContain("candidateReviewReason");
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

  test("olay ayrıntısını tek karar ve tek onay akışına indirir", () => {
    const detail = readFileSync(
      new URL("../src/components/TrainingReviewPanel.tsx", import.meta.url),
      "utf8",
    );

    expect(detail).toContain("Bu olayın “${canonicalEventLabel}” olduğu doğru mu?");
    expect(detail).toContain("Evet, doğru");
    expect(detail).toContain("Hayır, yanlış");
    expect(detail).toContain("grid grid-cols-3 gap-2");
    expect(detail).toContain("Ayrıntılı karara git");
    expect(detail).toContain("border-amber-500 bg-amber-500");
    expect(detail.indexOf("Ayrıntılı karara git")).toBeLessThan(
      detail.indexOf("Hayır, yanlış"),
    );
    expect(detail).not.toContain("Görüntüyü kontrol edin. Kararınızı verin.");
    expect(detail).not.toContain(">Olay sonucu</h3>");
    expect(detail).not.toContain("Hayır, sorun yok");
    expect(detail).toContain("Sistem bu olaydan öğrensin mi?");
    expect(detail).toContain("Onay ver ve kapat");
    expect(detail).toContain("approved_uses: recommendedApprovalUses");
    expect(detail).toContain("Diğer bilgiler");
    expect(detail).toContain("Geçmiş kararlar ve geliştirme ekibi araçları bu alandadır.");
    expect(detail).not.toContain("Olay İnceleme ve Geliştirme Hazırlığı");
    expect(detail).not.toContain("Sol taraftaki adımları tamamlayın");
  });
});
