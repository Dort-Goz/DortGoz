import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

import {
  evidenceFrameUrl,
  selectReviewEvidence,
} from "../src/components/TriagePanel";
import { consoleReducer, initialState } from "../src/state";
import type { EventEvidenceRef } from "../src/types/events";

const evidence: EventEvidenceRef[] = [
  { frame_id: "f_001", timestamp: 4, claim: "Olay öncesi gözlem." },
  { frame_id: "f_002", timestamp: 9, claim: "Olayın başlangıcı." },
  { frame_id: "f_003", timestamp: 13, claim: "Olayın zirve anı." },
  { frame_id: "f_004", timestamp: 20, claim: "Olay sonrası gözlem." },
];

describe("olay inceleme merkezi", () => {
  test("kanıt galerisinde önce, zirve ve sonra karesini seçer", () => {
    expect(selectReviewEvidence(evidence, 12).map((item) => item.timestamp)).toEqual([
      4, 13, 20,
    ]);
  });

  test("kanıt URL'si olay anahtarını güvenli biçimde kodlar", () => {
    expect(evidenceFrameUrl("KAM-1:inc-1", 12.5)).toBe(
      "/api/triage/evidence-frame?key=KAM-1%3Ainc-1&timestamp=12.5",
    );
  });

  test("sayfa yenilense bile kanıt seçimi videoyu ve zamanı geri kurar", () => {
    const state = consoleReducer(initialState, {
      kind: "seek",
      feed: "",
      timestamp: 12.5,
      video: "crime.mp4",
    });

    expect(state.feeds[""].video).toBe("crime.mp4");
    expect(state.feeds[""].seekTo).toBe(12.5);
  });

  test("tek-video ekranından erişilir ve mock kipte de açıktır", () => {
    const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
    const panel = readFileSync(
      new URL("../src/components/TriagePanel.tsx", import.meta.url), "utf8",
    );
    const console_ = readFileSync(
      new URL("../src/components/ReviewConsole.tsx", import.meta.url), "utf8",
    );

    // Sayfa adı artık üst çubuktaki sekmededir; panelin içinde başlık hücresi yok.
    expect(console_).toContain('className="toolbar"');
    expect(app).toContain("<ReviewConsole onOpenTraining={setTrainingEventId} />");
    expect(app).toContain('["analysis", "Analiz"]');
    expect(app).toContain('["live", "Canlı"]');
    expect(app).toContain('["review", "Olay inceleme"]');
    expect(app).toContain("ARAYÜZ TEST AKIŞI");
    // Nöbet kuyruğu geçici canlı kuyruktur; yeri Canlı çalışma alanıdır.
    // Olay inceleme kalıcı kayıt defteridir ve kendi durum süzgecini taşır.
    expect(app).not.toContain('title="Karar bekleyen olaylar"');
    expect(app).not.toContain('layout="workspace"');
    expect(app).not.toContain("triagePending");
    expect(console_).toContain("setStatus");
    expect(panel).toContain("Doğrulanmış video kanıtı");
    expect(panel).toContain("Operatör notu");
    expect(panel).toContain("Güvenli yerel taslak önerileri");
    expect(panel).toContain("if (!r.ok) throw new Error()");
    expect(panel).toContain("İnceleme kayıtları alınamadı. Bağlantıyı denetleyin.");
  });

  test("inceleme merkezi kaynak, durum ve aciliyet süzgeçlerini sunar", () => {
    const console_ = readFileSync(
      new URL("../src/components/ReviewConsole.tsx", import.meta.url), "utf8",
    );

    expect(console_).toContain('value="live"');
    expect(console_).toContain('value="analysis"');
    expect(console_).toContain("/api/review/events?");
    for (const label of ["kaynak", "durum", "aciliyet", "olay türü", "kamera", "ara"]) {
      expect(console_).toContain(`>${label}</span>`);
    }
  });
});
