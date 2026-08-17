import type { Event, OperatorMessage } from "../types/events";

interface SyncReset {
  kind: "sync_reset";
  oldest_seq: number;
  latest_seq: number;
}

export class EventSequence {
  private lastSeq = 0;

  get current(): number { return this.lastSeq; }

  accept(event: Event): boolean {
    if (event.seq > 0 && event.seq <= this.lastSeq) return false;
    if (event.seq > 0) this.lastSeq = event.seq;
    return true;
  }

  reset(oldestSeq: number): void {
    this.lastSeq = Math.max(0, oldestSeq - 1);
  }
}

/** Otomatik yeniden bağlanan, tipli WS istemcisi. */
export class DortgozSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handler: (e: Event) => void;
  private resetHandler: () => void;
  private sequence = new EventSequence();
  private closed = false;
  // Bağlantı kurulmadan gönderilenler SESSİZCE düşüyordu (sayfa açılır açılmaz
  // "demo"ya basmak 4 start_run'ı yutuyordu) → açılana dek kuyrukta bekletilir.
  private pending: OperatorMessage[] = [];

  constructor(handler: (e: Event) => void, resetHandler: () => void = () => undefined) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.url = `${proto}://${location.host}/ws`;
    this.handler = handler;
    this.resetHandler = resetHandler;
    this.connect();
  }

  private connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.ws?.send(JSON.stringify({ kind: "sync", from_seq: this.sequence.current }));
      for (const msg of this.pending.splice(0)) this.ws?.send(JSON.stringify(msg));
    };
    this.ws.onmessage = (msg) => {
      const incoming = JSON.parse(msg.data) as Event | SyncReset;
      if ("kind" in incoming && incoming.kind === "sync_reset") {
        this.sequence.reset(incoming.oldest_seq);
        this.resetHandler();
        return;
      }
      if (this.sequence.accept(incoming as Event)) this.handler(incoming as Event);
    };
    this.ws.onclose = () => {
      if (!this.closed) setTimeout(() => this.connect(), 1500);
    };
  }

  send(msg: OperatorMessage) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else this.pending.push(msg);
  }

  close() {
    this.closed = true;
    this.ws?.close();
  }
}
