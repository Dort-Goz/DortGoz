import { describe, expect, test } from "bun:test";

import { includeUploadedVideo, startCanonicalRun } from "../src/lib/canonicalRun";

describe("canonical production start", () => {
  test("tek kullanıcı aksiyonu yalnız bir WS start_run gönderir", () => {
    const gate = { current: false };
    const messages: unknown[] = [];
    const startedVideos: string[] = [];
    const args = {
      selected: "uploaded.mp4",
      busy: false,
      gate,
      overrides: {
        model: "local-model",
        system_prompt: "system",
        task_prompt: "task",
      },
      dispatchStarted: (video: string) => startedVideos.push(video),
      send: (message: unknown) => messages.push(message),
    };

    expect(startCanonicalRun(args)).toBe(true);
    expect(startCanonicalRun(args)).toBe(false);
    expect(startedVideos).toEqual(["uploaded.mp4"]);
    expect(messages).toEqual([
      {
        kind: "start_run",
        video: "uploaded.mp4",
        model: "local-model",
        system_prompt: "system",
        task_prompt: "task",
      },
    ]);
  });

  test("boş seçim veya meşgul konsol start göndermez", () => {
    const messages: unknown[] = [];
    const base = {
      gate: { current: false },
      overrides: {},
      dispatchStarted: () => undefined,
      send: (message: unknown) => messages.push(message),
    };

    expect(startCanonicalRun({ ...base, selected: "", busy: false })).toBe(false);
    expect(startCanonicalRun({ ...base, selected: "video.mp4", busy: true })).toBe(false);
    expect(messages).toEqual([]);
  });

  test("upload edilen video normal seçim listesine eklenir", () => {
    expect(includeUploadedVideo(["existing.mp4"], "uploaded.mp4")).toEqual([
      "existing.mp4",
      "uploaded.mp4",
    ]);
    expect(includeUploadedVideo(["uploaded.mp4"], "uploaded.mp4")).toEqual([
      "uploaded.mp4",
    ]);
  });
});

describe("reachable production UI", () => {
  test("yalnız canonical WS analiz deneyimini render eder", async () => {
    const appSource = await Bun.file(new URL("../src/App.tsx", import.meta.url)).text();
    const apiSource = await Bun.file(new URL("../src/lib/api.ts", import.meta.url)).text();

    expect(appSource).not.toContain("REST analiz");
    expect(appSource).not.toContain("<QueryPanel");
    expect(appSource).not.toContain("<EventDetail");
    expect(appSource).not.toContain("JSON rapor");
    expect(appSource.match(/startCanonicalRun\(\{/g)).toHaveLength(1);
    expect(appSource).toContain('kind: "stop_run"');
    expect(appSource).toContain("startDemo");
    expect(appSource).toContain("feed: feedName");
    expect(appSource).toContain("<Timeline");
    expect(appSource).toContain("incidents={feed.incidents}");
    expect(appSource).toContain("<AgentTrace");
    expect(appSource).toContain("entries={feed.trace}");
    expect(appSource).toContain("<ChatPanel");
    expect(appSource).toContain("onSend={send.chat}");
    expect(apiSource).not.toContain("startAnalysis");
    expect(apiSource).not.toContain('profile = "mock"');
    expect(apiSource).not.toContain("/analyze");
  });
});
