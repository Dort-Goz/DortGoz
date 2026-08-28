import { afterEach, describe, expect, test } from "bun:test";

import {
  ApiError,
  getMaintenanceReviews,
  saveMaintenanceReview,
} from "../src/lib/api";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function missingRouteResponse(): Response {
  return new Response(
    JSON.stringify({
      error: {
        code: "NOT_FOUND",
        message: "Not Found",
      },
    }),
    {
      status: 404,
      headers: { "Content-Type": "application/json" },
    },
  );
}

describe("bakım API sürüm koruması", () => {
  test("eski backend ham Not Found metnini kullanıcıya göstermez", async () => {
    globalThis.fetch = async () => missingRouteResponse();

    try {
      await getMaintenanceReviews("event-1");
      throw new Error("istek hata vermeliydi");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).code).toBe("MAINTENANCE_API_UNAVAILABLE");
      expect((error as Error).message).toBe(
        "Bakım servisi güncel değil. Backend'i güncelleyip yeniden başlatın.",
      );
    }
  });

  test("IT kararını kaydeden yol da aynı sürüm hatasını üretir", async () => {
    globalThis.fetch = async () => missingRouteResponse();

    expect(
      saveMaintenanceReview("event-1", {
        operator_review_id: "review-1",
        decision: "confirm",
        reviewer: "it-operator",
        note: "IT kararı",
        event_type: "possible_theft",
      }),
    ).rejects.toMatchObject({
      code: "MAINTENANCE_API_UNAVAILABLE",
      status: 503,
    });
  });
});
