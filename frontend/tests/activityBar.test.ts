import { describe, expect, test } from "bun:test";
import { toCells } from "../src/components/ActivityBar";
import { ACTIVITY_WINDOW_SECONDS, consoleReducer, initialState, pruneActivity } from "../src/state";
import type { ActivityStrip, Event } from "../src/types/events";

function strip(wallEnd: number, levels: number[], status = "hareket"): ActivityStrip {
  return {
    type: "activity_strip",
    window_start: 0,
    window_end: levels.length,
    wall_end: wallEnd,
    gate: 0.01,
    peak: 0.05,
    status: status as ActivityStrip["status"],
    levels,
  };
}

function activityEvent(feed: string, live: boolean, s: ActivityStrip): Event {
  return { seq: 1, ts: 1, feed, live, payload: s } as Event;
}

describe("kamera etkinlik şeridi", () => {
  test("iki dakikadan eski şeritler düşer", () => {
    const now = 1_000_000;
    const kept = pruneActivity(
      [
        strip(now - ACTIVITY_WINDOW_SECONDS - 5, [1, 1]),
        strip(now - 30, [2, 2]),
        strip(now - 1, [3, 3]),
      ],
      now,
    );

    expect(kept.map((s) => s.wall_end)).toEqual([now - 30, now - 1]);
  });

  test("hücreler zaman sırasına dizilir ve son 120 kare tutulur", () => {
    const now = 1_000_000;
    const many = Array.from({ length: 10 }, (_, i) =>
      strip(now - (10 - i), Array.from({ length: 30 }, () => 1)));

    const cells = toCells(many);

    expect(cells.length).toBe(120);
    expect(cells.every((cell) => cell.level === 1)).toBe(true);
  });

  test("eşik altı kareler sıfır seviyede kalır", () => {
    const cells = toCells([strip(10, [0, 2, 0, 3])]);

    expect(cells.map((cell) => cell.level)).toEqual([0, 2, 0, 3]);
  });

  test("şeridin durumu her hücreye taşınır", () => {
    const cells = toCells([strip(10, [1, 2], "anomali")]);

    expect(cells.every((cell) => cell.status === "anomali")).toBe(true);
  });

  test("canlı ve analiz şeritleri ayrı akışlarda birikir", () => {
    const now = Date.now() / 1000;
    let state = initialState;
    state = consoleReducer(state, {
      kind: "event", event: activityEvent("kamera1", true, strip(now, [1, 2, 3])),
    });
    state = consoleReducer(state, {
      kind: "event", event: activityEvent("KAM-1", false, strip(now, [1])),
    });

    expect(state.feeds["kamera1"].activity).toHaveLength(1);
    expect(state.feeds["kamera1"].live).toBe(true);
    expect(state.feeds["KAM-1"].activity).toHaveLength(1);
    expect(state.feeds["KAM-1"].live).toBe(false);
  });
});
