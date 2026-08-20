import { describe, expect, test } from "bun:test";

import {
  DortgozSocket,
  EventSequence,
  RECONNECT_BASE_MS,
  RECONNECT_MAX_MS,
  reconnectDelay,
  type ConnectionState,
} from "../src/lib/ws";
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

/** Sahte soket: gerçek ağ olmadan open/close olaylarını sürer. */
class FakeSocket {
  readyState = 0;
  sent: string[] = [];
  closeCalls = 0;
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;

  send(data: string) { this.sent.push(data); }
  close() { this.closeCalls += 1; }

  /** Sunucu bağlantıyı kabul etti. */
  accept() { this.readyState = 1; this.onopen?.(); }
  /** Sunucu bağlantıyı düşürdü (ör. yavaş istemci tahliyesi). */
  drop() { this.readyState = 3; this.onclose?.(); }
}

function harness() {
  const sockets: FakeSocket[] = [];
  const timers: { fn: () => void; ms: number; cancelled: boolean }[] = [];
  const states: ConnectionState[] = [];
  const socket = new DortgozSocket(() => undefined, () => undefined, {
    url: "ws://test/ws",
    onState: (state) => states.push(state),
    createSocket: () => {
      const fake = new FakeSocket();
      sockets.push(fake);
      return fake as unknown as WebSocket;
    },
    schedule: (fn, ms) => timers.push({ fn, ms, cancelled: false }) - 1,
    cancel: (handle) => { timers[handle].cancelled = true; },
  });
  return { socket, sockets, timers, states };
}

describe("WebSocket yeniden bağlanma", () => {
  test("geri çekilme katlanır ve üst sınırda durur", () => {
    expect(reconnectDelay(0)).toBe(RECONNECT_BASE_MS);
    expect(reconnectDelay(1)).toBe(RECONNECT_BASE_MS * 2);
    expect(reconnectDelay(2)).toBe(RECONNECT_BASE_MS * 4);
    expect(reconnectDelay(50)).toBe(RECONNECT_MAX_MS);
  });

  test("bağlantı durumu operatöre bildirilir", () => {
    const { socket, sockets, states } = harness();

    expect(states).toEqual(["connecting"]);
    sockets[0].accept();
    expect(states).toEqual(["connecting", "open"]);
    expect(socket.connection).toBe("open");

    sockets[0].drop();
    expect(states).toEqual(["connecting", "open", "reconnecting"]);
    expect(socket.connection).toBe("reconnecting");
  });

  test("art arda kopuşta bekleme süresi büyür, açılışta sıfırlanır", () => {
    const { sockets, timers } = harness();
    sockets[0].accept();

    sockets[0].drop();
    expect(timers[0].ms).toBe(reconnectDelay(0));
    timers[0].fn();
    expect(sockets).toHaveLength(2);

    sockets[1].drop();
    expect(timers[1].ms).toBe(reconnectDelay(1));
    timers[1].fn();

    sockets[2].accept();
    sockets[2].drop();
    expect(timers[2].ms).toBe(reconnectDelay(0));
  });

  test("close() bekleyen yeniden bağlanmayı iptal eder", () => {
    const { socket, sockets, timers, states } = harness();
    sockets[0].accept();
    sockets[0].drop();

    socket.close();

    expect(timers[0].cancelled).toBe(true);
    expect(states[states.length - 1]).toBe("closed");
    // İptal edilen zamanlayıcı yine de tetiklenirse soket AÇILMAMALI.
    timers[0].fn();
    expect(sockets).toHaveLength(1);
    expect(socket.connection).toBe("closed");
  });

  test("kapanmış soketin geç onclose'u yeni deneme başlatmaz", () => {
    const { sockets, timers } = harness();
    sockets[0].accept();
    sockets[0].drop();
    timers[0].fn();

    sockets[0].drop();

    expect(timers).toHaveLength(1);
    expect(sockets).toHaveLength(2);
  });

  test("bağlantı açılana dek gönderilenler kuyrukta bekler", () => {
    const { socket, sockets } = harness();

    socket.send({ kind: "chat", text: "merhaba" });
    expect(sockets[0].sent).toEqual([]);

    sockets[0].accept();

    expect(sockets[0].sent).toEqual([
      JSON.stringify({ kind: "sync", from_seq: 0 }),
      JSON.stringify({ kind: "chat", text: "merhaba" }),
    ]);
  });
});
