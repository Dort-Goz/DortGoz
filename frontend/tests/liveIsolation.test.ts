import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { consoleReducer, feedNames, initialState } from "../src/state";
import type { Event } from "../src/types/events";

function incident(feed: string, live: boolean, id = "INC-1"): Event {
  return {
    seq: 1,
    ts: 1,
    feed,
    live,
    payload: {
      type: "incident_update",
      incident_id: id,
      t: 12,
      phase: "basladi",
      title: "olay",
      anomaly_type: "kavga",
      risk: "yuksek",
      detail: "",
      boxes: [],
      needs_review: false,
      review_reason: "",
    },
  } as Event;
}

function runStatus(feed: string, live: boolean): Event {
  return {
    seq: 2,
    ts: 2,
    feed,
    live,
    payload: {
      type: "run_status",
      run_id: `run-${feed}`,
      state: "processing",
      progress: 0.2,
      speed: 1,
      detail: "canlı segment işleniyor",
      video: `${feed}.mp4`,
    },
  } as Event;
}

describe("canlı ve analiz ayrımı", () => {
  test("canlı akış analiz akış listesine girmez", () => {
    let state = initialState;
    state = consoleReducer(state, { kind: "event", event: incident("kamera1", true) });
    state = consoleReducer(state, { kind: "event", event: incident("KAM-1", false) });

    expect(feedNames(state, true)).toEqual(["kamera1"]);
    expect(feedNames(state, false)).toEqual(["KAM-1"]);
  });

  test("canlı olay analiz tarafının seçili akışını değiştirmez", () => {
    let state = initialState;
    state = consoleReducer(state, { kind: "event", event: incident("KAM-1", false) });
    state = consoleReducer(state, { kind: "event", event: incident("kamera1", true) });

    expect(state.active).toBe("KAM-1");
    expect(state.activeLive).toBe("kamera1");
  });

  test("canlı koşu durumu analiz üst çubuğunu ele geçirmez", () => {
    let state = initialState;
    state = consoleReducer(state, { kind: "event", event: runStatus("kamera1", true) });

    expect(state.active).toBe("");
    expect(state.feeds["kamera1"].live).toBe(true);
    expect(state.feeds[""]).toBeUndefined();
  });

  test("canlı sohbet ve aksiyon taslakları analiz paneline düşmez", () => {
    let state = initialState;
    state = consoleReducer(state, {
      kind: "event",
      event: {
        seq: 3, ts: 3, feed: "kamera1", live: true,
        payload: { type: "chat_message", role: "agent", text: "canlı özet" },
      } as Event,
    });
    state = consoleReducer(state, {
      kind: "event",
      event: {
        seq: 4, ts: 4, feed: "kamera1", live: true,
        payload: {
          type: "actuator_request", request_id: "R-1", actuator: "emniyet",
          reason: "canlı", incident_id: "INC-1", mode: "preview", status: "pending",
          live: true,
        },
      } as Event,
    });

    expect(state.chat).toEqual([]);
    expect(state.actuatorRequests).toEqual([]);
    expect(state.liveActuatorRequests).toHaveLength(1);
    expect(state.liveActuatorRequests[0].request_id).toBe("R-1");
  });

  test("canlı kayıt analiz video paneline sarılamaz", () => {
    let state = initialState;
    state = consoleReducer(state, { kind: "event", event: incident("kamera1", true) });
    const before = state;
    state = consoleReducer(state, {
      kind: "seek", feed: "kamera1", timestamp: 12, video: "seg.mp4",
    });

    expect(state).toBe(before);
    expect(state.active).toBe("");
  });

  test("canlı aksiyon günlüğü tek yerde, nöbet kuyruğunun altında durur", () => {
    const source = readFileSync(new URL("../src/components/LiveGrid.tsx", import.meta.url), "utf8");

    expect(source).toContain("logPanel={(");
    expect(source).toContain("requests={actionRequests}");
    expect(source).toContain("results={actionResults}");
    expect(source).not.toContain('"aksiyonlar"');
    expect(source.match(/<ActionLog/g)).toHaveLength(1);
  });
});
