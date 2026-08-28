import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

describe("model metni kesme", () => {
  test("kesme aracı tam metni ipucunda tutar", () => {
    const source = read("../src/components/ClampText.tsx");

    expect(source).toContain("title={text}");
    // Tailwind sınıfları kaynak taramasıyla üretilir; şablon dizgisiyle
    // yazılan `line-clamp-${n}` derlemede hiç oluşmaz ve kesme sessizce kalkar.
    expect(source).not.toMatch(/line-clamp-\$\{/);
    expect(source).toContain('1: "line-clamp-1"');
  });

  test("uzun model metinleri elle kesilmez, ClampText kullanır", () => {
    for (const path of [
      "../src/components/Timeline.tsx",
      "../src/components/ActionLog.tsx",
      "../src/components/TriagePanel.tsx",
    ]) {
      const source = read(path);
      expect(source).toContain("import ClampText");
      expect(source).toContain("<ClampText");
    }
  });
});
