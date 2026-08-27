import { describe, expect, test } from "bun:test";
import {
  outranks,
  severityRank,
  shouldChime,
  unseenAlerts,
  type AlertCandidate,
} from "../src/lib/liveAlerts";

function item(
  key: string,
  risk: string,
  band = "routine",
  score = 0,
  wall = 0,
): AlertCandidate {
  return { key, risk, intervention_band: band, intervention_score: score, wall };
}

describe("canlı anomali bildirimleri", () => {
  test("izlenen olaydan daha kritik olan öne geçer", () => {
    const izlenen = item("a", "yuksek", "high", 70);
    expect(outranks(item("b", "kritik", "urgent", 90), izlenen)).toBe(true);
    expect(outranks(item("c", "orta", "review", 40), izlenen)).toBe(false);
  });

  test("aynı seviyede yüksek etki puanı öne geçer", () => {
    const izlenen = item("a", "yuksek", "high", 70);
    expect(outranks(item("b", "yuksek", "high", 85), izlenen)).toBe(true);
    expect(outranks(item("c", "yuksek", "high", 70), izlenen)).toBe(false);
  });

  test("öncelik bandı risk etiketinden yüksekse o belirler", () => {
    expect(severityRank(item("a", "dusuk", "urgent", 95))).toBe(3);
  });

  test("görülmüş olaylar yeniden bildirilmez, en ağırı başa gelir", () => {
    const pending = [
      item("a", "orta", "review", 40, 10),
      item("b", "kritik", "urgent", 90, 20),
      item("c", "yuksek", "high", 70, 30),
    ];
    const alerts = unseenAlerts(pending, new Set(["a"]));

    expect(alerts.map((i) => i.key)).toEqual(["b", "c"]);
  });

  test("yalnız kritik seviye sesli uyarı ister", () => {
    expect(shouldChime([item("a", "yuksek", "high", 70)])).toBe(false);
    expect(shouldChime([item("b", "kritik", "urgent", 90)])).toBe(true);
  });
});
