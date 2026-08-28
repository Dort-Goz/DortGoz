import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

describe("analiz üst çubuğu", () => {
  test("denetimler koşu sırasına göre dizilir", () => {
    const app = read("../src/App.tsx");
    const order = [
      "block\">kaynak",
      "<UploadPanel",
      "çalışma kipi",
      "Başlat",
      "⊞ demo",
      "✕ temizle",
      "◎ ayrıntılı incele",
      "↓ dışarı çıkar",
      "<ImportPackage />",
    ];
    const positions = order.map((needle) => app.indexOf(needle));

    expect(positions.filter((index) => index < 0)).toEqual([]);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  test("deney paneli konsoldan kaldırıldı", () => {
    const app = read("../src/App.tsx");

    expect(app).not.toContain("deney");
    expect(app).not.toContain("ExperimentPanel");
    expect(app).not.toContain("interpret_config");
  });

  test("paket dışarı çıkarma biten koşuya bağlıdır", () => {
    const app = read("../src/App.tsx");

    expect(app).toContain("`/api/runs/${exportRunId}/export`");
    expect(app).toContain("disabled={!exportRunId}");
  });

  test("içeri alma düğmesi analiz ve olay inceleme çubuklarında aynıdır", () => {
    expect(read("../src/components/ImportPackage.tsx")).toContain("↑ içeri al");
    expect(read("../src/App.tsx")).toContain("<ImportPackage />");
    expect(read("../src/components/ReviewConsole.tsx")).toContain("<ImportPackage />");
  });
});

describe("operatör sohbeti", () => {
  test("hızlı işlem ve gönder düğmeleri yazı alanının yanında durur", () => {
    const chat = read("../src/components/ChatPanel.tsx");
    const input = chat.indexOf("<input");
    const quick = chat.indexOf('aria-label="Olayı aydınlat"');
    const send = chat.indexOf('aria-label="Gönder"');
    const title = chat.indexOf("Operatör Sohbeti");

    expect(input).toBeGreaterThan(-1);
    expect(quick).toBeGreaterThan(input);
    expect(send).toBeGreaterThan(quick);
    expect(chat.slice(title, input)).not.toContain("aydınlat");
  });
});
