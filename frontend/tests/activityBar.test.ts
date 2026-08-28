import { describe, expect, test } from "bun:test";
import { buildTimeline } from "../src/components/ActivityBar";
import { ACTIVITY_WINDOW_SECONDS, consoleReducer, initialState, pruneActivity } from "../src/state";
import type { ActivityStrip, Event } from "../src/types/events";

function strip(
  contentStart: number, levels: number[], status = "hareket",
): ActivityStrip {
  return {
    type: "activity_strip",
    window_start: 0,
    window_end: levels.length,
    wall_end: contentStart + levels.length,
    content_start: contentStart,
    gate: 0.01,
    peak: 0.05,
    status: status as ActivityStrip["status"],
    levels,
  };
}

const NOW = 1_000_000;

describe("kamera etkinlik şeridi", () => {
  test("şerit sabit iki dakikalık pencereye oturur", () => {
    const slots = buildTimeline([], NOW);

    expect(slots).toHaveLength(ACTIVITY_WINDOW_SECONDS);
    expect(slots[0].wall).toBe(NOW - ACTIVITY_WINDOW_SECONDS);
    expect(slots[slots.length - 1].wall).toBe(NOW - 1);
  });

  test("zaman ilerleyince pencere kayar", () => {
    const first = buildTimeline([], NOW);
    const later = buildTimeline([], NOW + 10);

    expect(later[0].wall - first[0].wall).toBe(10);
  });

  test("çözümlenmemiş son saniyeler beyaz kalır", () => {
    const slots = buildTimeline([strip(NOW - 60, Array(30).fill(2))], NOW);

    expect(slots[slots.length - 1].kind).toBe("bekliyor");
    expect(slots.filter((s) => s.kind === "bekliyor").length).toBe(30);
  });

  test("çözümlenen iki şerit arasındaki boşluk beyaz değil koyu kalır", () => {
    const slots = buildTimeline(
      [strip(NOW - 90, Array(20).fill(2)), strip(NOW - 40, Array(40).fill(2))],
      NOW,
    );
    const gap = slots.filter((s) => s.wall > NOW - 70 && s.wall < NOW - 40);

    expect(gap.length).toBeGreaterThan(0);
    expect(gap.every((s) => s.kind === "atlandi")).toBe(true);
    expect(slots.filter((s) => s.kind === "bekliyor").every((s) => s.wall >= NOW - 1)).toBe(true);
  });

  test("veri gelmemiş eski zaman boş kalır, beyaz olmaz", () => {
    const slots = buildTimeline([strip(NOW - 30, Array(30).fill(1))], NOW);

    expect(slots[0].kind).toBe("bos");
  });

  test("eşik altı kare yeşil değil gri olur", () => {
    const slots = buildTimeline([strip(NOW - 4, [0, 2, 0, 3])], NOW);
    const painted = slots.filter((s) => s.kind !== "bos" && s.kind !== "bekliyor");

    expect(painted.map((s) => s.kind)).toEqual(["sessiz", "hareket", "sessiz", "hareket"]);
  });

  test("anomali penceresinde sessiz saniye de kırmızı kalır", () => {
    const slots = buildTimeline([strip(NOW - 4, [0, 3, 0, 1], "anomali")], NOW);
    const painted = slots.filter((s) => s.kind !== "bos" && s.kind !== "bekliyor");

    expect(painted.every((s) => s.kind === "anomali")).toBe(true);
    expect(painted.map((s) => s.level)).toEqual([0, 3, 0, 1]);
  });

  test("dikkat penceresi sarı bandına düşer", () => {
    const slots = buildTimeline([strip(NOW - 2, [2, 2], "dikkat")], NOW);

    expect(slots.filter((s) => s.kind === "dikkat")).toHaveLength(2);
  });

  test("eleme hareket bandında gösterilir", () => {
    const slots = buildTimeline([strip(NOW - 2, [2, 2], "eleme")], NOW);

    expect(slots.filter((s) => s.kind === "hareket")).toHaveLength(2);
  });

  test("pencere dışına düşen eski şerit çizilmez", () => {
    const slots = buildTimeline([strip(NOW - 600, Array(30).fill(3))], NOW);

    expect(slots.every((s) => s.level === 0)).toBe(true);
  });

  test("iki dakikadan eski şeritler durumdan düşer", () => {
    const kept = pruneActivity(
      [strip(NOW - ACTIVITY_WINDOW_SECONDS - 40, [1]), strip(NOW - 10, [2])],
      NOW,
    );

    expect(kept).toHaveLength(1);
  });

  test("canlı ve analiz şeritleri ayrı akışlarda birikir", () => {
    const now = Date.now() / 1000;
    let state = initialState;
    for (const [feed, live] of [["kamera1", true], ["KAM-1", false]] as const) {
      state = consoleReducer(state, {
        kind: "event",
        event: { seq: 1, ts: 1, feed, live, payload: strip(now, [1, 2, 3]) } as Event,
      });
    }

    expect(state.feeds["kamera1"].activity).toHaveLength(1);
    expect(state.feeds["kamera1"].live).toBe(true);
    expect(state.feeds["KAM-1"].live).toBe(false);
  });
});
