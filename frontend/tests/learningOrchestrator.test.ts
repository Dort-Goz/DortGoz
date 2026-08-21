import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

describe("öğrenme yönlendiricisi güvenlik sınırı", () => {
  test("öğrenme değerini müdahale önceliğinden ayrı gösterir", () => {
    const source = readFileSync(
      new URL("../src/components/TrainingReviewPanel.tsx", import.meta.url),
      "utf8",
    );

    expect(source).toContain("Öğrenme yönlendiricisi");
    expect(source).toContain("learningPlan.learning_score");
    expect(source).toContain("learningPlan.intervention_score");
    expect(source).toContain("Müdahale önceliği:");
  });

  test("otomatik eğitim ve terfiyi açmaz", () => {
    const source = readFileSync(
      new URL("../src/components/TrainingReviewPanel.tsx", import.meta.url),
      "utf8",
    );
    const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");

    expect(source).toContain("Otomatik eğitim ve canlı modele otomatik terfi kapalıdır.");
    expect(source).toContain("selectedUses");
    expect(api).toContain("approved_uses: DevelopmentUse[]");
  });
});
