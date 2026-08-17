import { describe, expect, test } from "bun:test";

import { EventSequence } from "../src/lib/ws";
import type { Event } from "../src/types/events";

function event(seq: number): Event {
  return {
    seq,
    ts: seq,
    payload: { type: "chat_message", role: "agent", text: `${seq}`, streaming: false },
  } as Event;
}

describe("WebSocket sıra eşleme", () => {
  test("canlı ve replay kopyalarını ikinci kez işlemez", () => {
    const cursor = new EventSequence();

    expect(cursor.accept(event(4))).toBe(true);
    expect(cursor.accept(event(4))).toBe(false);
    expect(cursor.accept(event(3))).toBe(false);
    expect(cursor.accept(event(5))).toBe(true);
    expect(cursor.current).toBe(5);
  });

  test("history boşluğunda en eski saklı sıraya döner", () => {
    const cursor = new EventSequence();
    cursor.accept(event(20));

    cursor.reset(8);

    expect(cursor.current).toBe(7);
    expect(cursor.accept(event(8))).toBe(true);
  });
});
