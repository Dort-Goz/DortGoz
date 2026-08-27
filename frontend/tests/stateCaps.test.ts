import { describe, expect, test } from "bun:test";
import { CAPS, consoleReducer, initialState } from "../src/state";
import type { Event } from "../src/types/events";

function reportEvent(i: number): Event {
  return {
    seq: i,
    ts: i,
    feed: "KAM-1",
    payload: {
      type: "window_report",
      window_start: i * 30,
      window_end: (i + 1) * 30,
      anomaly_type: "normal",
      summary: `pencere ${i}`,
      events: [],
      uncertainties: [],
    },
  } as Event;
}

describe("7/24 dayanıklılık: state sınırları", () => {
  test("pencere raporları CAPS.reports ile sınırlı, en yeniler kalır", () => {
    let state = initialState;
    const n = CAPS.reports + 50;
    for (let i = 0; i < n; i++) {
      state = consoleReducer(state, { kind: "event", event: reportEvent(i) });
    }
    const reports = state.feeds["KAM-1"].reports;
    expect(reports.length).toBe(CAPS.reports);
    expect(reports[reports.length - 1].summary).toBe(`pencere ${n - 1}`);
    expect(reports[0].summary).toBe(`pencere ${n - CAPS.reports}`);
  });

  test("sohbet CAPS.chat ile sınırlı", () => {
    let state = initialState;
    for (let i = 0; i < CAPS.chat + 20; i++) {
      state = consoleReducer(state, {
        kind: "event",
        event: {
          seq: i, ts: i, feed: "",
          payload: { type: "chat_message", role: "operator", text: `m${i}` },
        } as Event,
      });
    }
    expect(state.chat.length).toBe(CAPS.chat);
    expect(state.chat[state.chat.length - 1].text).toBe(`m${CAPS.chat + 19}`);
  });

  test("yeni run_id aynı feed içindeki eski olayları koşulsuz temizler", () => {
    let state = consoleReducer(initialState, { kind: "event", event: reportEvent(1) });
    state = consoleReducer(state, {
      kind: "event",
      event: {
        seq: 2,
        ts: 2,
        feed: "KAM-1",
        payload: {
          type: "run_status",
          run_id: "run-1",
          state: "done",
          progress: 1,
          detail: "bitti",
          video: "old.mp4",
        },
      } as Event,
    });
    state = consoleReducer(state, { kind: "event", event: reportEvent(3) });
    state = consoleReducer(state, {
      kind: "event",
      event: {
        seq: 4,
        ts: 4,
        feed: "KAM-1",
        payload: {
          type: "run_status",
          run_id: "run-2",
          state: "processing",
          progress: 0,
          detail: "başladı",
          video: "new.mp4",
        },
      } as Event,
    });

    expect(state.feeds["KAM-1"].video).toBe("new.mp4");
    expect(state.feeds["KAM-1"].reports).toEqual([]);
  });

  test("canlı segment değişimi etkinlik şeridini sıfırlamaz", () => {
    const strip = (i: number): Event => ({
      seq: i, ts: i, feed: "KAM-1", live: true,
      payload: {
        type: "activity_strip",
        window_start: i, window_end: i + 1,
        wall_end: Date.now() / 1000,
        content_start: 0,
        status: "hareket",
        levels: [1],
      },
    } as unknown as Event);
    const segment = (i: number, runId: string): Event => ({
      seq: i, ts: i, feed: "KAM-1", live: true,
      payload: {
        type: "run_status", run_id: runId, state: "processing",
        progress: 0, detail: "segment", video: "",
      },
    } as Event);

    let state = consoleReducer(initialState, { kind: "event", event: segment(1, "canli-KAM-1-0001") });
    state = consoleReducer(state, { kind: "event", event: strip(2) });
    state = consoleReducer(state, { kind: "event", event: strip(3) });
    expect(state.feeds["KAM-1"].activity.length).toBe(2);

    // Sonraki segment yeni bir run_id taşır; şerit yine de birikmeye devam eder.
    state = consoleReducer(state, { kind: "event", event: segment(4, "canli-KAM-1-0002") });
    expect(state.feeds["KAM-1"].activity.length).toBe(2);
    state = consoleReducer(state, { kind: "event", event: strip(5) });
    expect(state.feeds["KAM-1"].activity.length).toBe(3);

    // Dosya analizinde yeni koşu hâlâ tertemiz başlar.
    const fileStrip = { ...strip(6), live: false } as Event;
    let file = consoleReducer(initialState, { kind: "event", event: fileStrip });
    file = consoleReducer(file, {
      kind: "event",
      event: { ...segment(7, "run-2"), live: false } as Event,
    });
    expect(file.feeds["KAM-1"].activity).toEqual([]);
  });

  test("yeni analiz koşusu eski video sohbetini temizler", () => {
    let state = consoleReducer(initialState, {
      kind: "event",
      event: {
        seq: 1, ts: 1, feed: "",
        payload: { type: "chat_message", role: "operator", text: "eski video" },
      } as Event,
    });

    state = consoleReducer(state, {
      kind: "run_started", video: "new.mp4", feed: "",
    });

    expect(state.chat).toEqual([]);
  });

  test("video seçimi sohbeti açıkça temizleyebilir", () => {
    const withChat = consoleReducer(initialState, {
      kind: "event",
      event: {
        seq: 1, ts: 1, feed: "",
        payload: { type: "chat_message", role: "operator", text: "eski video" },
      } as Event,
    });

    expect(consoleReducer(withChat, { kind: "clear_chat" }).chat).toEqual([]);
  });

  test("temizle düğmesi analizi boşaltır, canlı izlemeye dokunmaz", () => {
    const status = (seq: number, feed: string, live: boolean, runId: string): Event => ({
      seq, ts: seq, feed, live,
      payload: {
        type: "run_status", run_id: runId, state: "done",
        progress: 1, detail: "", video: "a.mp4",
      },
    } as Event);

    let state = consoleReducer(initialState, {
      kind: "event", event: status(1, "KAM-1", true, "canli-KAM-1-0001"),
    });
    state = consoleReducer(state, {
      kind: "event", event: status(2, "KAM-9", false, "run-1"),
    });

    const cleared = consoleReducer(state, { kind: "clear_analysis" });

    expect(Object.keys(cleared.feeds)).toEqual(["KAM-1"]);
    expect(cleared.actuatorRequests).toEqual([]);
    expect(cleared.chat).toEqual([]);
    expect(cleared.activeLive).toBe(state.activeLive);
  });

  test("sync reset tüm karışmış istemci state'ini temizler", () => {
    const state = consoleReducer(
      consoleReducer(initialState, { kind: "event", event: reportEvent(1) }),
      { kind: "sync_reset" },
    );
    expect(state).toEqual(initialState);
  });
});

describe("operatör konsolu dayanıklılığı", () => {
  test("bağlantı durumu üst çubukta kalıcı rozetle gösterilir", async () => {
    const appSource = await Bun.file(new URL("../src/App.tsx", import.meta.url)).text();

    expect(appSource).toContain("{ onState: setConnection }");
    expect(appSource).toContain("CONNECTION_CLS[connection]");
    expect(appSource).toContain("CONNECTION_TR[connection]");
    for (const state of ["connecting", "open", "reconnecting", "closed"]) {
      expect(appSource).toContain(`${state}:`);
    }
    const header = appSource.slice(
      appSource.indexOf("<header"),
      appSource.indexOf('className="ml-auto'),
    );
    expect(header).toContain("CONNECTION_TR[connection]");
  });

  test("karar gönderimi düşerse kart kilitlenmez", async () => {
    const triageSource = await Bun.file(
      new URL("../src/components/TriagePanel.tsx", import.meta.url)).text();

    expect(triageSource.match(/setBusy\(false\)/g)).toHaveLength(1);
    expect(triageSource).toMatch(/\}\s*finally\s*\{\s*setBusy\(false\);\s*\}/);
    expect(triageSource).toContain("Karar kaydedilemedi — {failed}");
    expect(triageSource).toContain("Karar sunucuya iletilemedi.");
    expect(triageSource).toContain("body.error?.message");
  });
});
