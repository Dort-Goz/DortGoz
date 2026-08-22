import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

describe("öğrenme yönlendiricisi güvenlik sınırı", () => {
  test("öğrenme değerini müdahale önceliğinden ayrı gösterir", () => {
    const source = readFileSync(
      new URL("../src/components/TrainingReviewPanel.tsx", import.meta.url),
      "utf8",
    );

    expect(source).toContain("Öğrenme Merkezi · olay planı");
    expect(source).toContain("learningPlan.learning_score");
    expect(source).toContain("learningPlan.intervention_score");
    expect(source).toContain("Müdahale önceliği:");
  });

  test("otomatik eğitim ve terfiyi açmaz", () => {
    const source = readFileSync(
      new URL("../src/components/LearningOrchestratorPanel.tsx", import.meta.url),
      "utf8",
    );
    const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");

    expect(source).toContain("Otomatik yürütme, otomatik eğitim ve canlı modele otomatik terfi kapalıdır.");
    expect(source).toContain("overview.priority_candidates");
    expect(api).toContain("approved_uses: DevelopmentUse[]");
    expect(api).toContain("/api/system/learning-orchestrator");
  });

  test("sistem görünümü gerçek operatör konsolundan erişilir", () => {
    const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
    const panel = readFileSync(
      new URL("../src/components/LearningOrchestratorPanel.tsx", import.meta.url),
      "utf8",
    );

    expect(app).toContain("<LearningOrchestratorPanel");
    expect(app).toContain("!fixtureMode");
    expect(app).toContain("◈ öğrenme merkezi");
    expect(panel).toContain("Öğrenme Merkezi");
    expect(panel).toContain("İnsan onaylı geliştirme akışları");
  });
});
