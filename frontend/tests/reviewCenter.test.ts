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

  test("tek-video ekranından erişilir ve mock akışından ayrı kalır", () => {
    const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
    const panel = readFileSync(
      new URL("../src/components/TriagePanel.tsx", import.meta.url), "utf8",
    );

    expect(app).toContain('title="Olay İnceleme Merkezi"');
    expect(app).toContain("!fixtureMode && (");
    expect(app).toContain('kind: "seek"');
    expect(app).toContain("video: reviewVideo");
    expect(panel).toContain("Doğrulanmış video kanıtı");
    expect(panel).toContain("Operatör notu");
    expect(panel).toContain("Güvenli yerel taslak önerileri");
  });
});
