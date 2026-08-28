import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  actionBelongsToMode, analysisRunIds, consoleReducer, initialState,
} from "../src/state";
import type { ActuatorRequest, ActuatorResult, Event } from "../src/types/events";

const request: ActuatorRequest = {
  type: "actuator_request",
  request_id: "req-1",
  actuator: "emniyet_bildirimi_hazirla",
  action_label: "Emniyet bildirimi",
  reason: "Kanıtlı saldırı",
  incident_id: "inc-1",
  incident_title: "Saldırı şüphesi",
  run_id: "run-1",
  feed: "KAM-1",
  live: false,
  anomaly_type: "saldiri",
  risk: "kritik",
  evidence_timestamps: [5, 7.5],
  mode: "preview",
  status: "pending",
  requested_at: 100,
};

const result: ActuatorResult = {
  type: "actuator_result",
  request_id: "req-1",
  actuator: "emniyet_bildirimi_hazirla",
  action_label: "Emniyet bildirimi",
  approved: true,
  status: "prepared",
  detail: "Emniyet bildirimi hazırlandı. Dış kuruma gönderilmedi.",
  incident_id: "inc-1",
  run_id: "run-1",
  feed: "KAM-1",
  live: false,
  mode: "preview",
  delivered: false,
  external_side_effect: false,
  artifact_url: "/api/actions/req-1/artifact",
  operator: "Operatör 1",
  resolved_at: 110,
};

function event(payload: ActuatorRequest | ActuatorResult, seq: number): Event {
  return { seq, ts: seq, feed: "KAM-1", payload };
}

describe("aksiyon günlüğü", () => {
  test("aynı WebSocket isteğini ikinci karta dönüştürmez", () => {
    const first = consoleReducer(initialState, { kind: "event", event: event(request, 1) });
    const repeated = consoleReducer(first, { kind: "event", event: event(request, 2) });

    expect(repeated.actuatorRequests).toHaveLength(1);
    expect(repeated.actuatorRequests[0].feed).toBe("KAM-1");
  });

  test("aynı sonuç kimliğini son durumla günceller", () => {
    const first = consoleReducer(initialState, { kind: "event", event: event(result, 1) });
    const changed = { ...result, status: "failed" as const, detail: "hata" };
    const repeated = consoleReducer(first, { kind: "event", event: event(changed, 2) });

    expect(repeated.actuatorResults).toHaveLength(1);
    expect(repeated.actuatorResults[0].status).toBe("failed");
  });

  test("REST snapshot sayfa yenilemesinden sonra durumu kurar", () => {
    const hydrated = consoleReducer(initialState, {
      kind: "hydrate_actions",
      requests: [request],
      results: [result],
    });

    expect(hydrated.actuatorRequests[0].request_id).toBe("req-1");
    expect(hydrated.actuatorResults[0].delivered).toBe(false);
  });

  test("REST snapshot canlı ve analiz aksiyonlarını ayrı günlüklerde kurar", () => {
    const liveRequest = { ...request, request_id: "req-live", live: true };
    const liveResult = { ...result, request_id: "req-live", live: true };
    const hydrated = consoleReducer(initialState, {
      kind: "hydrate_actions",
      requests: [request, liveRequest],
      results: [result, liveResult],
    });

    expect(hydrated.actuatorRequests.map((item) => item.request_id)).toEqual(["req-1"]);
    expect(hydrated.liveActuatorRequests.map((item) => item.request_id)).toEqual(["req-live"]);
    expect(hydrated.actuatorResults.map((item) => item.request_id)).toEqual(["req-1"]);
    expect(hydrated.liveActuatorResults.map((item) => item.request_id)).toEqual(["req-live"]);
  });

  test("günlük yalnız açık analiz koşularını tanır", () => {
    const runStatus = (run_id: string, seq: number): Event => ({
      seq, ts: seq, feed: "KAM-1",
      payload: {
        type: "run_status", run_id, state: "done", progress: 1, detail: "", video: "a.mp4",
      },
    });
    const first = consoleReducer(initialState, { kind: "event", event: runStatus("run-1", 1) });
    const second = consoleReducer(first, { kind: "event", event: runStatus("run-2", 2) });

    expect([...analysisRunIds(first)]).toEqual(["run-1"]);
    expect([...analysisRunIds(second)]).toEqual(["run-2"]);
  });

  test("fixture aksiyonu yalnız mock kipte görünür", () => {
    const fixture = { ...request, run_id: "fixture-ui-crime-1" };
    const mockLive = { ...request, run_id: "canli-mock-giris-0001" };
    const realLive = { ...request, run_id: "canli-KAM-1-seg_001" };

    expect(actionBelongsToMode(fixture, true)).toBe(true);
    expect(actionBelongsToMode(fixture, false)).toBe(false);
    expect(actionBelongsToMode(mockLive, true)).toBe(true);
    expect(actionBelongsToMode(mockLive, false)).toBe(false);
    expect(actionBelongsToMode(realLive, false)).toBe(true);
    expect(actionBelongsToMode(realLive, true)).toBe(false);
    expect(actionBelongsToMode(request, false)).toBe(true);
    expect(actionBelongsToMode(request, true)).toBe(false);
  });

  test("arayüz ham fonksiyon adı yerine teslim sınırını gösterir", () => {
    const source = readFileSync(new URL("../src/components/ActionLog.tsx", import.meta.url), "utf8");
    const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
    expect(source).toContain("DEMO MODU · DIŞ KURUMA İLETİLMEDİ");
    expect(source).toContain("Bildirimi hazırla");
    expect(source).not.toContain('request.run_id.startsWith("fixture-")');
    expect(source).not.toContain("{req.actuator}()");
    expect(appSource).toContain(
      "ARAYÜZ TEST AKIŞI · “BAŞLAT” KAYITLI BİR ÖRNEK AKIŞI OYNATIR");
    expect(appSource).toContain("actionBelongsToMode(request, fixtureMode)");
    expect(appSource).toContain("analysisRuns.has(result.run_id)");
    expect(appSource).not.toContain("readOnly={fixtureMode}");
  });
});
